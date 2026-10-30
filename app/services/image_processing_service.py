"""Image processing orchestrator.

ImageProcessingService is the single orchestrator for the OCR processing pipeline.
It coordinates:
1. OCR pipeline execution (via app.ocr_pipeline.process_image)
2. Coordinate normalization (via app.coord_normalizer.normalize_coordinates)
3. Structural parsing (via app.structural_parser.process_input_json)
4. Low-confidence flag augmentation (inline)

This service is used by both the CLI (app/processor.py) and the watcher (app/watcher.py).
"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

from paddleocr import PaddleOCR
from app.ocr_pipeline import process_image
from app.coord_normalizer import normalize_coordinates
from app.structural_parser import process_input_json
from app.input import read_json
from app.output import write_json_atomic

logger = logging.getLogger(__name__)


class ImageProcessingService:
    """
    Orchestrator for single-image OCR processing.

    Coordinates the full pipeline: OCR → coordinate normalization →
    structural parsing → low-confidence flag augmentation.
    """

    def __init__(self, ocr_engine: PaddleOCR) -> None:
        self.ocr_engine = ocr_engine

    def process(
        self,
        image_path: Path,
        output_dir: Path,
        model: str,
        db_path: Path | str | None = None,
        processed_dir: Optional[Path] = None,
        preprocess_mode: str = "none",
        preprocess_force: bool = False,
        target_short_side: int = 960,
    ) -> None:
        """Run the full processing pipeline for a single image.

        Args:
            image_path: Path to the input image file.
            output_dir: Directory for output JSON files.
            model: LLM model name or 'mock' for local heuristic.
            db_path: Optional SQLite database path for persisting results.
            processed_dir: Directory to save preprocessed images (Issue #36).
            preprocess_mode: "none" | "clahe" | "adaptive" | "clahe+adaptive".
            preprocess_force: Apply preprocessing even when confidence is not low.
            target_short_side: Target short-side size in px for resizing.
        """
        if preprocess_mode != "none":
            from app.image_preprocessing import build_preprocess_fn

            build_preprocess_fn(preprocess_mode)

        mtime = self._get_mtime(image_path)
        output_json_path = self._make_output_path(image_path, output_dir, mtime)

        self._run_ocr(image_path, output_dir, output_json_path, target_short_side)
        low_confidence, _ = self._get_topmost_confidence(output_json_path)

        active_raw_path = output_json_path
        should_preprocess = processed_dir is not None and preprocess_mode != "none"
        if should_preprocess and (low_confidence or preprocess_force):
            active_raw_path = self._preprocess_and_retry(
                image_path, output_dir, output_json_path, processed_dir, preprocess_mode, target_short_side
            )
            low_confidence, _ = self._get_topmost_confidence(active_raw_path)

        low_confidence = self._normalize_coords(active_raw_path) or low_confidence
        structured = self._parse_structured(active_raw_path, model, output_dir, db_path)

        # Issue #39: 学習済み date テンプレートがあれば、raw を date 基準へ再正規化し、
        # DB に保存済みの ocr_json を新基準へ更新する（受け入れ条件 6 の座標系同期）。
        # ※ 構造化出力の値はテキスト抽出に由来し座標基準に依存しないため、再解析は行わない
        #   （process_input_json が毎回 insert するため二重登録を避けるためでもある）。
        if structured and db_path and not low_confidence:
            from app.date_anchor import apply_date_anchor_normalization

            apply_date_anchor_normalization(active_raw_path, structured, db_path, output_dir=output_dir)

        if low_confidence:
            self._apply_low_confidence_flag(image_path, output_dir, mtime)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_topmost_confidence(output_json_path: Path) -> tuple[bool, Optional[float]]:
        """読み取り専用で topmost Confidence を取得する。"""
        try:
            from app.coord_normalizer import get_topmost_confidence

            return get_topmost_confidence(output_json_path)
        except Exception:
            logger.exception("Failed to check topmost confidence for %s", output_json_path)
            return True, None

    def _preprocess_and_retry(
        self,
        image_path: Path,
        output_dir: Path,
        output_json_path: Path,
        processed_dir: Path,
        preprocess_mode: str,
        target_short_side: int,
    ) -> Path:
        """前処理を適用した画像で OCR を再実行し、前処理済み raw JSON を別名保存する。

        Note:
            元の raw_data.json（output_json_path）は上書きせず温存する。
            前処理済みの結果は、{stem}_raw_data.preprocessed.json に保存する。

        前処理済み画像は processed_dir に保存する（目視検証用）。

        Returns:
            前処理済み raw_data のパス。後段の正規化・パースはこれを対象にする。
        """
        from app.image_preprocessing import build_preprocess_fn

        preprocessed_path = processed_dir / f"preprocessed_{image_path.name}"
        preprocessed_raw_path = Path(str(output_json_path).replace("-raw_data.json", "-raw_data.preprocessed.json"))
        preprocess_fn = build_preprocess_fn(preprocess_mode)

        process_image(
            image_path,
            output_dir=output_dir,
            output_json_path=preprocessed_raw_path,
            ocr=self.ocr_engine,
            preprocess_fn=preprocess_fn,
            preprocess_output_path=preprocessed_path,
            target_short_side=target_short_side,
        )
        logger.info(
            "Preprocessed image saved to %s; preprocessed raw saved to %s",
            preprocessed_path,
            preprocessed_raw_path,
        )
        return preprocessed_raw_path

    @staticmethod
    def _get_mtime(image_path: Path) -> int:
        try:
            return int(image_path.stat().st_mtime)
        except Exception:
            return int(datetime.now().timestamp())

    @staticmethod
    def _make_output_path(image_path: Path, output_dir: Path, mtime: int) -> Path:
        out_fname = f"{image_path.stem}_{mtime}-raw_data.json"
        return output_dir / out_fname

    def _run_ocr(
        self, image_path: Path, output_dir: Path, output_json_path: Path, target_short_side: int = 960
    ) -> None:
        try:
            structured = process_image(
                image_path,
                output_dir=output_dir,
                output_json_path=output_json_path,
                ocr=self.ocr_engine,
                target_short_side=target_short_side,
            )
            if structured is None:
                logger.warning("process_image returned None for %s", image_path)
                return
            elif isinstance(structured, dict):
                logger.info("Saved 1 item to %s", output_json_path)
            else:
                logger.info("Saved %d items to %s", len(structured), output_json_path)
        except Exception:
            logger.exception("OCR pipeline failed for %s", image_path)
            raise

    @staticmethod
    def _normalize_coords(output_json_path: Path) -> bool:
        """Run coordinate normalization. Returns True if low_confidence flag should be set."""
        low_confidence = False
        try:
            norm_result = normalize_coordinates(output_json_path)
            low_confidence = norm_result.get("low_confidence", False)

            if norm_result.get("normalized"):
                logger.info(
                    "Normalized coordinates for %s (offset_x=%d, offset_y=%d)",
                    output_json_path,
                    norm_result["offset_x"],
                    norm_result["offset_y"],
                )
            elif low_confidence:
                logger.info(
                    "Skipped coordinate normalization for %s (topmost confidence=%.3f < 0.8)",
                    output_json_path,
                    norm_result.get("topmost_confidence", 0),
                )
        except Exception:
            logger.exception("Coordinate normalization failed for %s", output_json_path)
        return low_confidence

    @staticmethod
    def _parse_structured(
        output_json_path: Path,
        model: str,
        output_dir: Path,
        db_path: Path | str | None,
    ) -> Optional[Dict[str, Any]]:
        try:
            result = process_input_json(
                output_json_path,
                model=model,
                output_dir=output_dir,
                db_path=db_path,
            )
            logger.info("Structured data generated for %s", output_json_path)
            return result
        except Exception:
            logger.exception("Failed to generate structured data for %s", output_json_path)
            raise

    @staticmethod
    def _apply_low_confidence_flag(image_path: Path, output_dir: Path, mtime: int) -> None:
        """Set low_confidence flag in the structured data JSON file."""
        try:
            structured_data_path = output_dir / f"{image_path.stem}_{mtime}-structured_data.json"
            if structured_data_path.exists():
                sd = read_json(structured_data_path)
                sd["low_confidence"] = True
                write_json_atomic(structured_data_path, sd)
                logger.info("Set low_confidence flag in %s", structured_data_path)
        except Exception:
            logger.exception("Failed to apply low_confidence flag for %s", image_path)
