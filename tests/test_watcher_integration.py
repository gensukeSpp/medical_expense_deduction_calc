import cv2
import numpy as np
from pathlib import Path
import threading
import time
import json
import sys
from unittest.mock import patch

from app.watcher import run_loop, run_watchdog, scan_and_process


class FakeOCR:
    def predict(self, img):
        # Return a simple list-shaped result matching pipeline expectations
        box = [[0, 0], [10, 0], [10, 10], [0, 10]]
        return [[[box, ["TEST_TEXT", 0.95]]]]


def test_scan_and_process_run_once(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"

    input_dir.mkdir()

    # create a small dummy image
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    img_path = input_dir / "test.jpg"
    assert cv2.imwrite(str(img_path), img)

    ocr = FakeOCR()

    processed_count = scan_and_process(
        input_dir=input_dir,
        ocr=ocr,
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        max_files=None,
        retries=0,
    )

    assert processed_count == 1

    # Two JSON files should be created: raw_data + structured_data
    raw_outputs = list(output_dir.glob("*-raw_data.json"))
    structured_outputs = list(output_dir.glob("*-structured_data.json"))
    assert len(raw_outputs) == 1
    assert len(structured_outputs) == 1

    # original should NOT be moved to processed
    assert not (processed_dir / "test.jpg").exists()
    assert (input_dir / "test.jpg").exists()
    assert not (failed_dir / "test.jpg").exists()


def test_process_skips_file_still_writing(tmp_path):
    """Create a file that is being written while scan runs. First scan should skip, second should process."""
    input_dir = tmp_path / "input2"
    output_dir = tmp_path / "output2"
    processed_dir = tmp_path / "processed2"
    failed_dir = tmp_path / "failed2"

    input_dir.mkdir()

    img_path = input_dir / "inprogress.jpg"

    # create a dummy valid image
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(img_path), img)

    # background writer that appends data for ~2 seconds
    def writer(path: Path):
        for _ in range(6):
            time.sleep(0.3)
            with open(path, "ab") as f:
                f.write(b"x" * 512)

    t = threading.Thread(target=writer, args=(img_path,), daemon=True)
    t.start()

    ocr = FakeOCR()

    # First scan attempts processing but should skip because file is unstable
    processed_count = scan_and_process(
        input_dir=input_dir,
        ocr=ocr,
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        max_files=None,
        retries=0,
    )
    assert processed_count == 0
    assert img_path.exists()

    # Wait for writer to finish and add a small buffer
    t.join()
    time.sleep(1)

    # Second scan should process the now-stable file
    processed_count2 = scan_and_process(
        input_dir=input_dir,
        ocr=ocr,
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        max_files=None,
        retries=2,  # Increased retries
    )
    assert processed_count2 == 1

    # Two JSON files created: raw_data + structured_data
    raw_outputs = list(output_dir.glob("*-raw_data.json"))
    structured_outputs = list(output_dir.glob("*-structured_data.json"))
    assert len(raw_outputs) == 1
    assert len(structured_outputs) == 1
    # should NOT be in processed
    assert not (processed_dir / "inprogress.jpg").exists()
    assert (input_dir / "inprogress.jpg").exists()


def test_scan_and_process_skips_when_output_exists(tmp_path):
    """If output JSON already exists for the image (based on mtime naming), scanner should skip processing without moving image."""
    input_dir = tmp_path / "input3"
    output_dir = tmp_path / "output3"
    processed_dir = tmp_path / "processed3"
    failed_dir = tmp_path / "failed3"

    input_dir.mkdir()

    # create image
    img = np.zeros((32, 32, 3), dtype=np.uint8)
    img_path = input_dir / "already.jpg"
    assert cv2.imwrite(str(img_path), img)

    # compute expected output name based on mtime
    mtime = int(img_path.stat().st_mtime)
    out_fname = f"{img_path.stem}_{mtime}-raw_data.json"
    out_path = output_dir / out_fname
    output_dir.mkdir()

    # create a pre-existing JSON file to simulate prior processing
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([{"text": "PREV", "confidence": 1.0, "box": []}], f)

    class RaisingOCR:
        def predict(self, img):
            raise RuntimeError("OCR should not be called when output exists")

    ocr = RaisingOCR()

    processed_count = scan_and_process(
        input_dir=input_dir,
        ocr=ocr,
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        max_files=None,
        retries=0,
    )

    assert processed_count == 1
    # should NOT be in processed
    assert not (processed_dir / "already.jpg").exists()
    assert (input_dir / "already.jpg").exists()
    assert (out_path).exists()


def test_scan_and_process_forces_preprocessing_for_confident_receipt(tmp_path):
    input_dir = tmp_path / "input-force"
    output_dir = tmp_path / "output-force"
    processed_dir = tmp_path / "processed-force"
    failed_dir = tmp_path / "failed-force"
    input_dir.mkdir()

    img_path = input_dir / "confident.jpg"
    assert cv2.imwrite(str(img_path), np.full((64, 64, 3), 120, dtype=np.uint8))

    count = scan_and_process(
        input_dir=input_dir,
        ocr=FakeOCR(),
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        retries=0,
        preprocess_mode="clahe",
        preprocess_force=True,
        target_short_side=64,
    )

    assert count == 1
    assert (output_dir / f"confident_{int(img_path.stat().st_mtime)}-raw_data.preprocessed.json").exists()
    assert (processed_dir / "preprocessed_confident.jpg").exists()
    assert len(list(output_dir.glob("*-structured_data.json"))) == 1


def test_run_loop_forwards_preprocess_force(tmp_path):
    args = [tmp_path / name for name in ("input", "output", "processed", "failed")]
    with (
        patch("app.watcher.PaddleOCR"),
        patch("app.watcher.scan_and_process", return_value=0) as scan,
    ):
        run_loop(*args, run_once=True, preprocess_force=True)

    assert scan.call_args.kwargs["preprocess_force"] is True


def test_run_watchdog_fallback_forwards_preprocess_force(tmp_path):
    args = [tmp_path / name for name in ("input", "output", "processed", "failed")]
    with (
        patch.dict(sys.modules, {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}),
        patch("app.watcher.run_loop") as run,
    ):
        run_watchdog(*args, preprocess_force=True)

    assert run.call_args.kwargs["preprocess_force"] is True


def test_unreadable_image_moved_to_failed_and_not_reprocessed(tmp_path):
    """Issue #60: 読込不能画像は 1 サイクルで failed/ へ移動し、次のサイクルで再処理されない"""
    input_dir = tmp_path / "input-u"
    output_dir = tmp_path / "output-u"
    processed_dir = tmp_path / "processed-u"
    failed_dir = tmp_path / "failed-u"

    input_dir.mkdir()
    # 読込不能画像: cv2.imread が読めないバイナリを .jpg として配置（resize 失敗 → []）
    bad = input_dir / "broken.jpg"
    bad.write_bytes(b"\x00\x01\x02 not a real image")

    ocr = FakeOCR()

    first = scan_and_process(
        input_dir=input_dir,
        ocr=ocr,
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        retries=0,
    )
    assert first == 0  # 失敗扱い（成功数に数えない）
    assert not (input_dir / "broken.jpg").exists()  # input から取り除かれた
    assert (failed_dir / "broken.jpg").exists()  # failed/ へ退避された
    assert len(list(output_dir.glob("*-raw_data.json"))) == 0

    # 次のサイクル: 元画像が input にないため再処理されない
    second = scan_and_process(
        input_dir=input_dir,
        ocr=ocr,
        output_dir=output_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        retries=0,
    )
    assert second == 0
    assert not (input_dir / "broken.jpg").exists()
    assert (failed_dir / "broken.jpg").exists()
