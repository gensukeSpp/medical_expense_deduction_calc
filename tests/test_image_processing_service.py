"""Tests for app/services/image_processing_service.py — ImageProcessingService orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.image_processing_service import ImageProcessingService

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_ocr_engine() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_ocr_engine: MagicMock) -> ImageProcessingService:
    return ImageProcessingService(ocr_engine=mock_ocr_engine)


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    p = tmp_path / "receipt-001.jpg"
    p.write_text("fake-image-data")
    return p


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "output_json"
    d.mkdir()
    return d


# ------------------------------------------------------------------
# _get_mtime
# ------------------------------------------------------------------


class TestGetMtime:
    def test_returns_mtime_when_file_exists(self, image_path: Path):
        mtime = ImageProcessingService._get_mtime(image_path)
        assert isinstance(mtime, int)
        assert mtime > 0

    def test_fallback_when_file_does_not_exist(self, tmp_path: Path):
        missing = tmp_path / "nope.jpg"
        mtime = ImageProcessingService._get_mtime(missing)
        assert isinstance(mtime, int)
        assert mtime > 0


# ------------------------------------------------------------------
# _make_output_path
# ------------------------------------------------------------------


class TestMakeOutputPath:
    def test_returns_correct_path(self, image_path: Path, output_dir: Path):
        mtime = 1234567890
        result = ImageProcessingService._make_output_path(image_path, output_dir, mtime)
        assert result == output_dir / "receipt-001_1234567890-raw_data.json"
        assert result.parent == output_dir


# ------------------------------------------------------------------
# _run_ocr
# ------------------------------------------------------------------


class TestRunOcr:
    def test_calls_process_image_and_logs(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path, caplog: pytest.LogCaptureFixture
    ):
        output_json_path = output_dir / "receipt-001_12345-raw_data.json"
        with patch("app.services.image_processing_service.process_image", return_value=[{"text": "hello"}]) as mock_pi:
            service._run_ocr(image_path, output_dir, output_json_path)
            mock_pi.assert_called_once_with(
                image_path,
                output_dir=output_dir,
                output_json_path=output_json_path,
                ocr=service.ocr_engine,
                target_short_side=960,
            )
        assert "Saved 1 item to" in caplog.text

    def test_logs_warning_when_none(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path, caplog: pytest.LogCaptureFixture
    ):
        output_json_path = output_dir / "receipt-001_12345-raw_data.json"
        with patch("app.services.image_processing_service.process_image", return_value=None):
            service._run_ocr(image_path, output_dir, output_json_path)
        assert "process_image returned None" in caplog.text

    def test_raises_on_exception(self, service: ImageProcessingService, image_path: Path, output_dir: Path):
        output_json_path = output_dir / "receipt-001_12345-raw_data.json"
        with patch("app.services.image_processing_service.process_image", side_effect=ValueError("ocr failed")):
            with pytest.raises(ValueError, match="ocr failed"):
                service._run_ocr(image_path, output_dir, output_json_path)


# ------------------------------------------------------------------
# _normalize_coords
# ------------------------------------------------------------------


class TestNormalizeCoords:
    def test_returns_false_when_normalized(self, service: ImageProcessingService, output_dir: Path):
        output_json_path = output_dir / "test.json"
        output_json_path.write_text("[]")
        with patch(
            "app.services.image_processing_service.normalize_coordinates",
            return_value={
                "normalized": True,
                "offset_x": 10,
                "offset_y": 20,
            },
        ):
            result = service._normalize_coords(output_json_path)
        assert result is False

    def test_returns_true_when_low_confidence(self, service: ImageProcessingService, output_dir: Path):
        output_json_path = output_dir / "test.json"
        output_json_path.write_text("[]")
        with patch(
            "app.services.image_processing_service.normalize_coordinates",
            return_value={
                "normalized": False,
                "low_confidence": True,
                "topmost_confidence": 0.5,
            },
        ):
            result = service._normalize_coords(output_json_path)
        assert result is True

    def test_returns_false_on_exception(
        self, service: ImageProcessingService, output_dir: Path, caplog: pytest.LogCaptureFixture
    ):
        output_json_path = output_dir / "test.json"
        output_json_path.write_text("[]")
        with patch("app.services.image_processing_service.normalize_coordinates", side_effect=RuntimeError("boom")):
            result = service._normalize_coords(output_json_path)
        assert result is False
        assert "Coordinate normalization failed" in caplog.text


# ------------------------------------------------------------------
# _parse_structured
# ------------------------------------------------------------------


class TestParseStructured:
    def test_calls_process_input_json(self, service: ImageProcessingService, output_dir: Path):
        output_json_path = output_dir / "test.json"
        output_json_path.write_text("[]")
        with patch("app.services.image_processing_service.process_input_json") as mock_pij:
            service._parse_structured(output_json_path, model="mock", output_dir=output_dir, db_path=None)
            mock_pij.assert_called_once_with(
                output_json_path,
                model="mock",
                output_dir=output_dir,
                db_path=None,
            )

    def test_raises_on_exception(self, service: ImageProcessingService, output_dir: Path):
        output_json_path = output_dir / "test.json"
        output_json_path.write_text("[]")
        with patch("app.services.image_processing_service.process_input_json", side_effect=ValueError("parse failed")):
            with pytest.raises(ValueError, match="parse failed"):
                service._parse_structured(output_json_path, model="mock", output_dir=output_dir, db_path=None)


# ------------------------------------------------------------------
# _apply_low_confidence_flag
# ------------------------------------------------------------------


class TestApplyLowConfidenceFlag:
    def test_sets_flag_when_structured_file_exists(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path
    ):
        mtime = 12345
        structured_path = output_dir / f"{image_path.stem}_{mtime}-structured_data.json"
        structured_path.write_text(json.dumps({"clinic_name": "test"}))

        service._apply_low_confidence_flag(image_path, output_dir, mtime)

        data = json.loads(structured_path.read_text())
        assert data["low_confidence"] is True

    def test_skips_when_structured_file_missing(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path, caplog: pytest.LogCaptureFixture
    ):
        service._apply_low_confidence_flag(image_path, output_dir, 99999)
        # No error should be raised; just skipped silently
        assert len(caplog.records) == 0 or all(r.levelno < 40 for r in caplog.records)


# ------------------------------------------------------------------
# process (full orchestration)
# ------------------------------------------------------------------


class TestProcess:
    def test_rejects_unknown_preprocess_mode(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path
    ):
        with patch.object(service, "_run_ocr") as mock_ocr:
            with pytest.raises(ValueError, match="Unknown preprocessing mode"):
                service.process(
                    image_path,
                    output_dir,
                    model="mock",
                    preprocess_mode="unknown",
                )

        mock_ocr.assert_not_called()

    def test_happy_path(self, service: ImageProcessingService, image_path: Path, output_dir: Path):
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"
        structured_path = output_dir / f"receipt-001_{mtime}-structured_data.json"

        with (
            patch.object(service, "_run_ocr") as mock_ocr,
            patch.object(service, "_get_topmost_confidence", return_value=(False, 0.95)) as mock_conf,
            patch.object(service, "_normalize_coords", return_value=False) as mock_norm,
            patch.object(service, "_parse_structured") as mock_parse,
            patch.object(service, "_apply_low_confidence_flag") as mock_aug,
        ):
            service.process(image_path, output_dir, model="mock", db_path=None)

        mock_ocr.assert_called_once_with(image_path, output_dir, raw_path, 960)
        mock_conf.assert_called_once_with(raw_path)
        mock_norm.assert_called_once_with(raw_path)
        mock_parse.assert_called_once_with(raw_path, "mock", output_dir, None)
        mock_aug.assert_not_called()

    def test_low_confidence_triggers_augmentation(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path
    ):
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"

        with (
            patch.object(service, "_run_ocr") as mock_ocr,
            patch.object(service, "_get_topmost_confidence", return_value=(True, 0.5)),
            patch.object(service, "_normalize_coords", return_value=True),
            patch.object(service, "_parse_structured"),
            patch.object(service, "_apply_low_confidence_flag") as mock_aug,
        ):
            service.process(image_path, output_dir, model="mock", db_path=None)

        mock_aug.assert_called_once_with(image_path, output_dir, mtime)

    def test_ocr_failure_propagates(self, service: ImageProcessingService, image_path: Path, output_dir: Path):
        with patch.object(service, "_run_ocr", side_effect=ValueError("ocr failed")):
            with pytest.raises(ValueError, match="ocr failed"):
                service.process(image_path, output_dir, model="mock", db_path=None)

    def test_parse_failure_propagates(self, service: ImageProcessingService, image_path: Path, output_dir: Path):
        with (
            patch.object(service, "_run_ocr"),
            patch.object(service, "_get_topmost_confidence", return_value=(False, 0.95)),
            patch.object(service, "_normalize_coords", return_value=False),
            patch.object(service, "_parse_structured", side_effect=ValueError("parse failed")),
        ):
            with pytest.raises(ValueError, match="parse failed"):
                service.process(image_path, output_dir, model="mock", db_path=None)

    def test_happy_path_anchor_called_when_learned_template(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path
    ):
        """db_path 指定 + 低Confidenceでない場合、date anchor 正規化が実行される。"""
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"

        with (
            patch.object(service, "_run_ocr"),
            patch.object(service, "_get_topmost_confidence", return_value=(False, 0.95)),
            patch.object(service, "_normalize_coords", return_value=False),
            patch.object(service, "_parse_structured", return_value={"clinic": "A", "date": "2026-01-15"}),
            patch("app.date_anchor.apply_date_anchor_normalization") as mock_anchor,
        ):
            service.process(image_path, output_dir, model="mock", db_path="/tmp/db.sqlite3")

        mock_anchor.assert_called_once_with(
            raw_path,
            {"clinic": "A", "date": "2026-01-15"},
            "/tmp/db.sqlite3",
            output_dir=output_dir,
        )

    def test_happy_path_anchor_skipped_when_low_confidence(
        self, service: ImageProcessingService, image_path: Path, output_dir: Path
    ):
        """低Confidence時は date anchor 正規化を実行しない（既存挙動維持）。"""
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"

        with (
            patch.object(service, "_run_ocr"),
            patch.object(service, "_get_topmost_confidence", return_value=(True, 0.5)),
            patch.object(service, "_normalize_coords", return_value=True),
            patch.object(service, "_parse_structured", return_value={"clinic": "A", "date": "2026-01-15"}),
            patch("app.date_anchor.apply_date_anchor_normalization") as mock_anchor,
        ):
            service.process(image_path, output_dir, model="mock", db_path="/tmp/db.sqlite3")

        mock_anchor.assert_not_called()


class TestProcessPreprocessRetry:
    def test_low_confidence_triggers_preprocess_retry_keeps_both_raw(self, service, image_path, output_dir, tmp_path):
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"
        preprocessed_raw_path = output_dir / f"receipt-001_{mtime}-raw_data.preprocessed.json"
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir()

        with (
            patch.object(service, "_run_ocr") as mock_ocr,
            patch.object(
                service,
                "_get_topmost_confidence",
                side_effect=[(True, 0.5), (True, 0.7)],  # 1回目→low / 再試行後→まだlow
            ) as mock_conf,
            patch.object(service, "_preprocess_and_retry", return_value=preprocessed_raw_path) as mock_retry,
            patch.object(service, "_normalize_coords", return_value=False) as mock_norm,
            patch.object(service, "_parse_structured") as mock_parse,
            patch.object(service, "_apply_low_confidence_flag") as mock_aug,
        ):
            service.process(
                image_path,
                output_dir,
                model="mock",
                db_path=None,
                processed_dir=processed_dir,
                preprocess_mode="clahe+adaptive",
                target_short_side=1200,
            )

        # 低Confidence → 前処理再試行が呼ばれる（原本 raw_data.json は温存）
        mock_ocr.assert_called_once_with(image_path, output_dir, raw_path, 1200)
        mock_retry.assert_called_once_with(image_path, output_dir, raw_path, processed_dir, "clahe+adaptive", 1200)
        # 後段の正規化・パースは前処理済み raw を対象にする
        mock_norm.assert_called_once_with(preprocessed_raw_path)
        mock_parse.assert_called_once_with(preprocessed_raw_path, "mock", output_dir, None)
        # 再試行後もまだ低Confidence → flag 付与
        mock_aug.assert_called_once_with(image_path, output_dir, mtime)

    def test_no_preprocess_when_confident(self, service, image_path, output_dir, tmp_path):
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir()

        with (
            patch.object(service, "_run_ocr"),
            patch.object(service, "_get_topmost_confidence", return_value=(False, 0.95)),
            patch.object(service, "_preprocess_and_retry") as mock_retry,
            patch.object(service, "_normalize_coords", return_value=False) as mock_norm,
            patch.object(service, "_parse_structured") as mock_parse,
        ):
            service.process(
                image_path,
                output_dir,
                model="mock",
                db_path=None,
                processed_dir=processed_dir,
                preprocess_mode="clahe+adaptive",
                target_short_side=960,
            )

        mock_retry.assert_not_called()
        # Confidence が十分な場合は原本 raw をそのまま後段へ
        mock_norm.assert_called_once_with(raw_path)

    def test_preprocess_force_applies_even_when_confident(self, service, image_path, output_dir, tmp_path):
        mtime = int(image_path.stat().st_mtime)
        raw_path = output_dir / f"receipt-001_{mtime}-raw_data.json"
        preprocessed_raw_path = output_dir / f"receipt-001_{mtime}-raw_data.preprocessed.json"
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir()

        with (
            patch.object(service, "_run_ocr"),
            patch.object(service, "_get_topmost_confidence", return_value=(False, 0.95)),
            patch.object(service, "_preprocess_and_retry", return_value=preprocessed_raw_path) as mock_retry,
            patch.object(service, "_normalize_coords", return_value=False) as mock_norm,
            patch.object(service, "_parse_structured"),
        ):
            service.process(
                image_path,
                output_dir,
                model="mock",
                db_path=None,
                processed_dir=processed_dir,
                preprocess_mode="clahe+adaptive",
                preprocess_force=True,
                target_short_side=960,
            )

        mock_retry.assert_called_once_with(image_path, output_dir, raw_path, processed_dir, "clahe+adaptive", 960)
        # 前処理済み raw が後段の正規化対象になる
        mock_norm.assert_called_once_with(preprocessed_raw_path)
