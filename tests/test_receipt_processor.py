"""Tests for app/services/receipt_processor.py — ReceiptProcessor class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.date_anchor import ANCHOR_UNRESOLVED, NO_TEMPLATE
from app.services.receipt_processor import ReceiptProcessor
from app.services.file_repository import FileRepository


@pytest.fixture
def file_repo(tmp_path: Path) -> FileRepository:
    return FileRepository(
        output_dir=tmp_path / "output",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
    )


@pytest.fixture
def processor(tmp_path: Path, file_repo: FileRepository) -> ReceiptProcessor:
    return ReceiptProcessor(
        ocr=MagicMock(),
        file_repository=file_repo,
        output_dir=tmp_path / "output",
        model="mock",
        db_path=None,
        retries=1,
    )


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.jpg"
    p.write_text("fake-image-data")
    return p


class TestInit:
    def test_initializes_with_dependencies(self, tmp_path: Path, file_repo: FileRepository):
        ocr = MagicMock()
        proc = ReceiptProcessor(
            ocr=ocr,
            file_repository=file_repo,
            output_dir=tmp_path / "output",
            model="test-model",
            db_path="/tmp/test.db",
            retries=3,
        )
        assert proc.ocr is ocr
        assert proc.model == "test-model"
        assert proc.db_path == "/tmp/test.db"
        assert proc.retries == 3


class TestSyncProcess:
    def test_returns_true_when_output_already_exists(self, processor: ReceiptProcessor, image_path: Path):
        # Create output JSON to simulate prior processing
        output_dir = processor.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        mtime = int(image_path.stat().st_mtime)
        out_fname = f"{image_path.stem}_{mtime}-raw_data.json"
        (output_dir / out_fname).write_text("[]")

        result = processor._sync_process(image_path)
        assert result is True

    def test_returns_false_when_file_unstable(self, processor: ReceiptProcessor, image_path: Path):
        # Make the file appear to be growing by using a short interval
        # and modifying the file during the check
        import threading
        import time

        def grow():
            time.sleep(0.1)
            image_path.write_text("more-data")

        t = threading.Thread(target=grow, daemon=True)
        t.start()

        result = processor._sync_process(image_path)
        assert result is False

    @patch("app.ocr_pipeline.process_image")
    @patch("app.structural_parser.process_input_json")
    def test_returns_true_on_success(
        self,
        mock_process_input_json,
        mock_process_image,
        processor: ReceiptProcessor,
        image_path: Path,
    ):
        mock_process_image.return_value = [{"text": "test", "confidence": 0.95}]
        processor.target_short_side = 1200
        # Ensure output dir exists
        processor.output_dir.mkdir(parents=True, exist_ok=True)

        result = processor._sync_process(image_path)

        assert result is True
        mock_process_image.assert_called_once_with(
            image_path,
            output_dir=processor.output_dir,
            output_json_path=processor.output_dir
            / f"{image_path.stem}_{int(image_path.stat().st_mtime)}-raw_data.json",
            ocr=processor.ocr,
            target_short_side=1200,
        )
        mock_process_input_json.assert_called_once()

    @patch(
        "app.coord_normalizer.normalize_coordinates",
        return_value={"normalized": False, "low_confidence": False, "topmost_confidence": 0.95},
    )
    @patch("app.ocr_pipeline.process_image", return_value=[])
    @patch("app.structural_parser.process_input_json")
    def test_preprocess_force_retries_even_when_confident(
        self,
        mock_process_input_json,
        mock_process_image,
        mock_normalize_coordinates,
        processor: ReceiptProcessor,
        image_path: Path,
    ):
        processor.processed_dir = processor.file_repository.processed_dir
        processor.preprocess_mode = "clahe"
        processor.preprocess_force = True
        processor.target_short_side = 1200
        processor.output_dir.mkdir(parents=True, exist_ok=True)

        result = processor._sync_process(image_path)

        assert result is True
        assert mock_process_image.call_count == 2
        assert mock_process_image.call_args_list[0].kwargs["target_short_side"] == 1200
        assert mock_process_image.call_args_list[1].kwargs["target_short_side"] == 1200
        assert mock_process_image.call_args_list[1].kwargs["preprocess_fn"] is not None
        assert mock_normalize_coordinates.call_count == 2
        mock_process_input_json.assert_called_once()

    @patch("app.ocr_pipeline.process_image")
    def test_returns_false_on_all_retries_exhausted(
        self,
        mock_process_image,
        processor: ReceiptProcessor,
        image_path: Path,
    ):
        mock_process_image.side_effect = RuntimeError("OCR failed")
        processor.output_dir.mkdir(parents=True, exist_ok=True)

        result = processor._sync_process(image_path)

        assert result is False
        # Should have been copied to failed dir
        failed_files = list(processor.file_repository.failed_dir.glob("*"))
        assert len(failed_files) >= 1


class TestDateBasisForceIntegration:
    """Issue #50: apply_date_anchor_normalization が None のとき apply_date_basis_force が呼ばれる."""

    @patch("app.date_anchor.apply_date_basis_force")
    @patch("app.date_anchor.apply_date_anchor_normalization", return_value=(False, ANCHOR_UNRESOLVED))
    @patch(
        "app.coord_normalizer.normalize_coordinates",
        return_value={"normalized": True, "low_confidence": False, "offset_x": 0, "offset_y": 0},
    )
    @patch("app.structural_parser.process_input_json", return_value={"clinic": "A", "date": "2026-01-15"})
    @patch("app.ocr_pipeline.process_image", return_value=[])
    def test_force_called_when_anchor_none(
        self,
        mock_process_image,
        mock_process_input_json,
        mock_normalize_coordinates,
        mock_anchor,
        mock_force,
        tmp_path: Path,
        image_path: Path,
    ):
        file_repo = FileRepository(
            output_dir=tmp_path / "output",
            processed_dir=tmp_path / "processed",
            failed_dir=tmp_path / "failed",
        )
        proc = ReceiptProcessor(
            ocr=MagicMock(),
            file_repository=file_repo,
            output_dir=tmp_path / "output",
            model="mock",
            db_path=tmp_path / "db.sqlite3",
            retries=1,
        )
        proc.output_dir.mkdir(parents=True, exist_ok=True)

        result = proc._sync_process(image_path)

        assert result is True
        mock_anchor.assert_called_once()
        mock_force.assert_called_once()

    @patch("app.date_anchor.apply_date_basis_force")
    @patch("app.date_anchor.apply_date_anchor_normalization", return_value=(True, (0, 0)))
    @patch(
        "app.coord_normalizer.normalize_coordinates",
        return_value={"normalized": True, "low_confidence": False, "offset_x": 0, "offset_y": 0},
    )
    @patch("app.structural_parser.process_input_json", return_value={"clinic": "A", "date": "2026-01-15"})
    @patch("app.ocr_pipeline.process_image", return_value=[])
    def test_force_not_called_when_anchor_resolved(
        self,
        mock_process_image,
        mock_process_input_json,
        mock_normalize_coordinates,
        mock_anchor,
        mock_force,
        tmp_path: Path,
        image_path: Path,
    ):
        file_repo = FileRepository(
            output_dir=tmp_path / "output",
            processed_dir=tmp_path / "processed",
            failed_dir=tmp_path / "failed",
        )
        proc = ReceiptProcessor(
            ocr=MagicMock(),
            file_repository=file_repo,
            output_dir=tmp_path / "output",
            model="mock",
            db_path=tmp_path / "db.sqlite3",
            retries=1,
        )
        proc.output_dir.mkdir(parents=True, exist_ok=True)

        result = proc._sync_process(image_path)

        assert result is True
        mock_anchor.assert_called_once()
        mock_force.assert_not_called()


class TestForceNotCalledForOtherAnchorReasons:
    """レビュー P2#1: anchor 失敗が date 候補 0 件以外なら force を呼ばない."""

    @patch("app.date_anchor.apply_date_basis_force")
    @patch("app.date_anchor.apply_date_anchor_normalization", return_value=(False, NO_TEMPLATE))
    @patch(
        "app.coord_normalizer.normalize_coordinates",
        return_value={"normalized": True, "low_confidence": False, "offset_x": 0, "offset_y": 0},
    )
    @patch("app.structural_parser.process_input_json", return_value={"clinic": "A", "date": "2026-01-15"})
    @patch("app.ocr_pipeline.process_image", return_value=[])
    def test_force_not_called_when_no_template(
        self,
        mock_process_image,
        mock_process_input_json,
        mock_normalize_coordinates,
        mock_anchor,
        mock_force,
        tmp_path: Path,
        image_path: Path,
    ):
        file_repo = FileRepository(
            output_dir=tmp_path / "output",
            processed_dir=tmp_path / "processed",
            failed_dir=tmp_path / "failed",
        )
        proc = ReceiptProcessor(
            ocr=MagicMock(),
            file_repository=file_repo,
            output_dir=tmp_path / "output",
            model="mock",
            db_path=tmp_path / "db.sqlite3",
            retries=1,
        )
        proc.output_dir.mkdir(parents=True, exist_ok=True)

        result = proc._sync_process(image_path)

        assert result is True
        mock_anchor.assert_called_once()
        mock_force.assert_not_called()


class TestAsyncProcess:
    @pytest.mark.asyncio
    async def test_process_delegates_to_sync(self, processor: ReceiptProcessor, image_path: Path):
        # Create output JSON to simulate prior processing
        processor.output_dir.mkdir(parents=True, exist_ok=True)
        mtime = int(image_path.stat().st_mtime)
        out_fname = f"{image_path.stem}_{mtime}-raw_data.json"
        (processor.output_dir / out_fname).write_text("[]")

        result = await processor.process(image_path)
        assert result is True
