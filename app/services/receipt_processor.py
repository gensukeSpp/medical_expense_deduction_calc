from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

from .file_repository import FileRepository

LOG = logging.getLogger("receipt_processor")


class ProcessorProtocol(Protocol):
    """
    ReceiptProcessor のインターフェースを定義するプロトコル。
    DI (Dependency Injection) を容易にする。
    """

    async def process(self, image_path: Path) -> bool: ...


class ReceiptProcessor:
    """
    ビジネスロジック: OCRから解析までを担当するクラス。
    """

    def __init__(
        self,
        ocr,
        file_repository: FileRepository,
        output_dir: Path,
        model: str = "mock",
        db_path: Path | str | None = None,
        retries: int = 1,
        processed_dir: Path | None = None,
        preprocess_mode: str = "none",
        target_short_side: int = 960,
        preprocess_force: bool = False,
    ):
        self.ocr = ocr
        self.file_repository = file_repository
        self.output_dir = output_dir
        self.model = model
        self.db_path = db_path
        self.retries = retries
        self.processed_dir = processed_dir
        self.preprocess_mode = preprocess_mode
        self.preprocess_force = preprocess_force
        self.target_short_side = target_short_side

    async def process(self, image_path: Path) -> bool:
        """
        単一の画像を処理する。
        """
        import asyncio

        # from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_running_loop()
        # process メソッドが呼び出されるたびに ThreadPoolExecutor を新規作成して破棄しているため、
        # パフォーマンス低下やリソースの無駄遣いにつながります。
        # with ThreadPoolExecutor() as pool:
        #     return await loop.run_in_executor(pool, self._sync_process, image_path)
        return await loop.run_in_executor(None, self._sync_process, image_path)

    def _preprocess_and_retry(
        self,
        image_path: Path,
        output_dir: Path,
        output_json_path: Path,
        mtime: int,
    ) -> Path:
        """前処理を適用した画像で OCR を再実行し、前処理済み raw JSON を別名保存する。

        Note:
            元の raw_data.json（output_json_path）は上書きせず温存する。
            前処理済みの結果は、{stem}_{mtime}-raw_data.preprocessed.json に保存する。

        前処理済み画像は processed_dir に保存する（目視検証用）。

        Returns:
            前処理済み raw_data のパス。
        """
        from app.image_preprocessing import build_preprocess_fn
        from app.ocr_pipeline import process_image

        processed_dir = self.processed_dir
        preprocessed_image_path = (
            processed_dir / f"preprocessed_{image_path.name}"
            if processed_dir
            else output_dir / f"preprocessed_{image_path.name}"
        )
        preprocessed_raw_path = output_dir / f"{image_path.stem}_{mtime}-raw_data.preprocessed.json"
        preprocess_fn = build_preprocess_fn(self.preprocess_mode)

        process_image(
            image_path,
            output_dir=output_dir,
            output_json_path=preprocessed_raw_path,
            ocr=self.ocr,
            preprocess_fn=preprocess_fn,
            preprocess_output_path=preprocessed_image_path,
            target_short_side=self.target_short_side,
        )
        LOG.info(
            "Preprocessed image saved to %s; preprocessed raw saved to %s",
            preprocessed_image_path,
            preprocessed_raw_path,
        )
        return preprocessed_raw_path

    def _sync_process(self, image_path: Path) -> bool:
        """
        同期的に画像を処理する (process_one のロジックを移植)。
        """
        from app.ocr_pipeline import process_image
        from app.structural_parser import process_input_json

        output_dir = self.output_dir

        # deterministic output name based on file mtime to enable idempotency
        try:
            mtime = int(image_path.stat().st_mtime)
        except Exception:
            mtime = int(time.time())
        out_fname = f"{image_path.stem}_{mtime}-raw_data.json"
        output_json_path = output_dir / out_fname

        # if output already exists, treat as already processed
        if output_json_path.exists():
            LOG.info("Output already exists for %s -> %s, skipping", image_path, output_json_path)
            return True

        # skip files that are still being written
        if not self.file_repository.is_file_stable(image_path):
            LOG.info("File appears unstable (still being written), skipping for now: %s", image_path)
            return False

        if self.preprocess_mode != "none":
            from app.image_preprocessing import build_preprocess_fn

            build_preprocess_fn(self.preprocess_mode)

        attempt = 0
        resized_path = None
        while attempt <= self.retries:
            try:
                LOG.info("Processing %s (attempt %d)", image_path, attempt + 1)
                structured = process_image(
                    image_path,
                    output_dir=output_dir,
                    output_json_path=output_json_path,
                    ocr=self.ocr,
                    target_short_side=self.target_short_side,
                )

                resized_path = output_dir / f"resized_gray_{image_path.name}"

                LOG.info("Processed %s -> %s (%d items)", image_path, output_json_path, len(structured))

                # Coordinate normalization: convert absolute coords to relative
                low_confidence = False
                active_raw_path = output_json_path
                try:
                    from app.coord_normalizer import normalize_coordinates

                    norm_result = normalize_coordinates(output_json_path)
                    low_confidence = norm_result.get("low_confidence", False)
                    if norm_result.get("normalized"):
                        LOG.info(
                            "Normalized coordinates for %s (offset_x=%d, offset_y=%d)",
                            output_json_path,
                            norm_result["offset_x"],
                            norm_result["offset_y"],
                        )
                    elif low_confidence:
                        LOG.info(
                            "Skipped coordinate normalization for %s (topmost confidence=%.3f < 0.8)",
                            output_json_path,
                            norm_result.get("topmost_confidence", 0),
                        )
                except Exception:
                    LOG.exception("Coordinate normalization failed for %s", output_json_path)

                # Issue #36: 低Confidence時のみ前処理で再試行（1回）
                # 原本 raw_data.json は温存し、前処理済み結果は -raw_data.preprocessed.json に別名保存
                if (
                    (low_confidence or self.preprocess_force)
                    and self.processed_dir is not None
                    and self.preprocess_mode != "none"
                ):
                    active_raw_path = self._preprocess_and_retry(image_path, output_dir, output_json_path, mtime)
                    low_confidence = False
                    try:
                        from app.coord_normalizer import normalize_coordinates

                        norm_result = normalize_coordinates(active_raw_path)
                        low_confidence = norm_result.get("low_confidence", False)
                    except Exception:
                        LOG.exception("Coordinate normalization failed for %s", active_raw_path)

                # Generate structured data from OCR raw data
                structured = None
                try:
                    structured = process_input_json(
                        active_raw_path,
                        model=self.model,
                        output_dir=output_dir,
                        db_path=self.db_path,
                    )
                    LOG.info("Structured data generated for %s", active_raw_path)
                except Exception:
                    LOG.exception("Failed to generate structured data for %s", active_raw_path)

                # Issue #39: 学習済み date テンプレートがあれば raw を date 基準へ再正規化し、
                # DB の ocr_json を新基準へ更新する（受け入れ条件 6）。
                # ※ 構造化出力の値はテキスト抽出に由来し座標基準へ依存しないため、再解析は行わない。
                if structured and self.db_path and not low_confidence:
                    try:
                        from app.date_anchor import apply_date_anchor_normalization

                        apply_date_anchor_normalization(
                            active_raw_path, structured, self.db_path, output_dir=output_dir
                        )
                    except Exception:
                        LOG.exception("Date-anchor normalization failed for %s", active_raw_path)

                # Set low_confidence flag in structured data if needed
                if low_confidence:
                    try:
                        from app.input import read_json
                        from app.output import write_json_atomic

                        structured_data_path = output_dir / f"{image_path.stem}_{mtime}-structured_data.json"
                        if structured_data_path.exists():
                            sd = read_json(structured_data_path)
                            sd["low_confidence"] = True
                            write_json_atomic(structured_data_path, sd)
                            LOG.info("Set low_confidence flag in %s", structured_data_path)
                    except Exception:
                        LOG.exception("Failed to set low_confidence flag for %s", output_json_path)

                # Cleanup resized image
                if resized_path and resized_path.exists():
                    self.file_repository.cleanup_file(resized_path)

                return True
            except Exception as e:
                LOG.exception("Failed processing %s: %s", image_path, e)
                attempt += 1
                time.sleep(1)

        # Cleanup resized image on failure
        if resized_path and resized_path.exists():
            self.file_repository.cleanup_file(resized_path)

        # All attempts failed; copy to failed_dir
        self.file_repository.move_to_failed(image_path)
        return False
