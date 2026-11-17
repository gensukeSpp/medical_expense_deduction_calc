"""Tests for app.services.watch_controller (Issue #56)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from app.db_migrations import run_migrations
from app.services.watch_controller import PREPROCESS_MODES, WatchController

SCHEMA_PATH = "docs/schema.sql"


# --- 設定: input_dir ---


def test_set_input_dir_creates_directory(tmp_path):
    """存在しない input_dir を指定すると mkdir され、状態が更新される"""
    ctl = WatchController(output_dir=str(tmp_path / "output"))
    target = tmp_path / "nested" / "receipts"
    result = ctl.set_input_dir(str(target))
    assert target.is_dir()
    assert ctl.input_dir == target
    assert result == target


def test_default_input_dir_is_downloads_receipts():
    """デフォルト input_dir は ~/Downloads/receipts（args.py と同じ）"""
    ctl = WatchController()
    assert ctl.input_dir == Path.home() / "Downloads" / "receipts"


# --- 設定: db_path ---


def test_set_db_path_runs_migrations(tmp_path):
    """db_path 設定時に migration が走りテーブルが作られる"""
    ctl = WatchController(output_dir=str(tmp_path / "output"))
    db = tmp_path / "db.sqlite3"
    ctl.set_db_path(db)
    assert ctl.has_db is True
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"receipts", "clinics", "templates", "corrections"} <= tables


def test_set_db_path_none_clears():
    """db_path=None で has_db が False になる"""
    ctl = WatchController()
    ctl.set_db_path(None)
    assert ctl.has_db is False


# --- 設定: preprocess_mode ---


def test_set_preprocess_mode_valid():
    ctl = WatchController()
    for mode in PREPROCESS_MODES:
        ctl.set_preprocess_mode(mode)
        assert ctl.preprocess_mode == mode


def test_set_preprocess_mode_invalid_raises():
    ctl = WatchController()
    with pytest.raises(ValueError):
        ctl.set_preprocess_mode("bogus")


# --- watch スレッド ---


def _make_ctl(tmp_path, scan_fn, poll_interval: float = 0.1, with_db: bool = True) -> WatchController:
    """テスト用コントローラ生成。scan_fn は fake、DB は migration 済み実ファイル。"""
    ctl = WatchController(
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output_json"),
        processed_dir=str(tmp_path / "processed"),
        failed_dir=str(tmp_path / "failed"),
        poll_interval=poll_interval,
        scan_fn=scan_fn,
        ocr_factory=lambda: object(),
    )
    if with_db:
        db = tmp_path / "db.sqlite3"
        run_migrations(db, SCHEMA_PATH)
        ctl.set_db_path(db)
    return ctl


def test_start_watch_requires_db(tmp_path):
    """DB 未選択で start_watch は ValueError（受け入れ要件 2）"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    with pytest.raises(ValueError, match="データベース"):
        ctl.start_watch()
    assert ctl.watch_on is False


def test_start_stop_watch_toggles(tmp_path):
    """start で watch_on=True、stop で False に戻る"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.start_watch()
    assert ctl.watch_on is True
    ctl.stop_watch()
    assert ctl.watch_on is False


def test_start_watch_idempotent(tmp_path):
    """二重 start は no-op（スレッドは 1 つ）"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.start_watch()
    first = ctl._thread
    ctl.start_watch()
    assert ctl._thread is first
    ctl.stop_watch()


def test_scan_sets_processing_flag(tmp_path):
    """スキャン中は is_processing=True、終了後 False"""
    entered = threading.Event()
    release = threading.Event()

    def slow_scan(*args, **kwargs):
        entered.set()
        release.wait(timeout=5)

    ctl = _make_ctl(tmp_path, scan_fn=slow_scan, poll_interval=0.1)
    ctl.start_watch()
    assert entered.wait(timeout=5), "scan が開始されない"
    assert ctl.is_processing is True
    release.set()
    deadline = 0
    while ctl.is_processing and deadline < 50:
        time.sleep(0.1)
        deadline += 1
    assert ctl.is_processing is False
    ctl.stop_watch()


def test_scan_skips_when_already_processing(tmp_path):
    """処理中は次のスキャンを開始しない（多重スキャン防止）"""
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow_scan(*args, **kwargs):
        calls.append(1)
        entered.set()
        release.wait(timeout=5)

    ctl = _make_ctl(tmp_path, scan_fn=slow_scan, poll_interval=0.05)
    ctl.start_watch()
    assert entered.wait(timeout=5)
    ctl._run_scan()  # 処理中に手動実行しても開始されない
    assert len(calls) == 1
    release.set()
    ctl.stop_watch()


def test_watch_loop_calls_scan_repeatedly(tmp_path):
    """ポーリングで scan_fn が複数回呼ばれる"""
    calls = []

    def counting_scan(*args, **kwargs):
        calls.append(1)
        return 0

    ctl = _make_ctl(tmp_path, scan_fn=counting_scan, poll_interval=0.05)
    ctl.start_watch()
    deadline = 0
    while len(calls) < 2 and deadline < 100:
        time.sleep(0.1)
        deadline += 1
    ctl.stop_watch()
    assert len(calls) >= 2


# --- DB アップロード保存 ---


def test_save_db_upload_migrates_and_sets_db_path(tmp_path):
    """アップロードした DB は data/ に保存され migration 済みになる（Q2 案A）"""
    db = tmp_path / "src.sqlite3"
    run_migrations(db, SCHEMA_PATH)
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    dest = ctl.save_db_upload(db.read_bytes(), "my.db")
    assert dest == Path("data") / "my.db"
    assert dest.exists()
    assert ctl.has_db is True
    conn = sqlite3.connect(str(dest))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "receipts" in tables


def test_save_db_upload_sanitizes_filename(tmp_path):
    """パス注入を防ぐため basename のみ使う"""
    db = tmp_path / "src.sqlite3"
    run_migrations(db, SCHEMA_PATH)
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    dest = ctl.save_db_upload(db.read_bytes(), "../../evil.db")
    assert dest.name == "evil.db"
    assert dest.parent.name == "data"  # data/ 直下に落ち着く


def test_save_db_upload_invalid_file_raises(tmp_path):
    """DB でないバイト列は例外（ValueError/Exception）になり、保存先は掃除される"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    with pytest.raises(Exception):
        ctl.save_db_upload(b"not a sqlite db", "bad.db")
    assert not (Path("data") / "bad.db").exists()


# --- 画像コピー ---


def test_copy_image_writes_to_input_dir(tmp_path):
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    dest = ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    assert dest == ctl.input_dir / "IMG_001.jpg"
    assert dest.read_bytes() == b"fake-jpeg-bytes"


def test_copy_image_rejects_non_image(tmp_path):
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    with pytest.raises(ValueError, match="画像"):
        ctl.copy_image("notes.txt", b"hello")


# --- 状態 ---


def test_get_status_shape(tmp_path):
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    s = ctl.get_status()
    assert set(s) == {
        "watch_on",
        "is_processing",
        "input_dir",
        "db_path",
        "has_db",
        "preprocess_mode",
        "poll_interval",
        "last_error",
        "structured_count",
    }
    assert s["has_db"] is False
    assert s["preprocess_mode"] == "none"
