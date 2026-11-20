"""Tests for app.services.watch_controller (Issue #56)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from app.db_migrations import run_migrations
from app.services.watch_controller import PREPROCESS_MODES, WatchBusyError, WatchController

SCHEMA_PATH = "docs/schema.sql"
# リポジトリ直下の data/（実データ置き場）。テストはここへ書き込んではいけない。
REPO_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# --- 設定: input_dir ---


def test_set_input_dir_creates_directory(tmp_path, monkeypatch):
    """存在しない input_dir を指定すると mkdir され、状態が更新される"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    ctl = WatchController(output_dir=str(tmp_path / "output"))
    target = home / "nested" / "receipts"
    result = ctl.set_input_dir(str(target))
    assert target.is_dir()
    assert ctl.input_dir == target
    assert result == target


def test_set_input_dir_rejects_outside_home(tmp_path, monkeypatch):
    """[中-4] 許可ルート（ホーム）外の input_dir は ValueError"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    ctl = WatchController(output_dir=str(tmp_path / "output"))
    with pytest.raises(ValueError, match="ホーム"):
        ctl.set_input_dir(str(tmp_path / "outside"))
    assert ctl.input_dir != tmp_path / "outside"


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
        data_dir=str(tmp_path / "data"),
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


def test_stop_restart_no_duplicate_thread(tmp_path):
    """[中-1] 停止→再開で監視スレッドが二重化しない"""
    entered = threading.Event()
    release = threading.Event()

    def slow_scan(*args, **kwargs):
        entered.set()
        release.wait(timeout=10)

    ctl = _make_ctl(tmp_path, scan_fn=slow_scan, poll_interval=0.1)
    ctl.start_watch()
    old = ctl._thread
    assert old is not None
    assert entered.wait(timeout=5)
    # スキャン中に停止 → join timeout で戻るが、生存中の _thread は保持される
    ctl.stop_watch()
    assert ctl.watch_on is True
    # 生存中に再開しても新スレッドは作られない（二重化防止）
    ctl.start_watch()
    assert ctl._thread is old
    # スキャン完了 → 専用 stop Event により旧スレッドが自己終了する
    release.set()
    old.join(timeout=5)
    assert not old.is_alive()
    assert ctl.watch_on is False
    # 再開できる（新スレッド 1 本だけ）
    ctl.start_watch()
    assert ctl._thread is not old
    assert ctl.watch_on is True
    alive = [t for t in threading.enumerate() if t.name == "web-watcher" and t.is_alive()]
    assert len(alive) == 1
    ctl.stop_watch()


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


def test_scan_evicts_unreadable_and_clears_processing(tmp_path):
    """Issue #60: _run_scan 完了で is_processing が解除され、読込不能画像が input から消える"""
    input_dir = tmp_path / "input"
    failed_dir = tmp_path / "failed"
    input_dir.mkdir()
    broken = input_dir / "broken.jpg"
    broken.write_bytes(b"\x00\x01\x02 not a real image")

    # fake scan: _sync_process の失敗退避（move）を再現する
    def fake_scan(in_dir, ocr, out_dir, proc_dir, fail_dir, **kwargs):
        for img in in_dir.iterdir():
            if img.name == "broken.jpg":
                fail_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(img), str(fail_dir / img.name))

    ctl = _make_ctl(tmp_path, scan_fn=fake_scan, poll_interval=0.05)
    ctl._run_scan()

    assert ctl.is_processing is False
    assert not (input_dir / "broken.jpg").exists()  # input から取り除かれた
    assert (failed_dir / "broken.jpg").exists()  # failed/ へ退避された
    ctl.stop_watch()


# --- [中-3] 処理待ち状態 ---


def test_copy_image_sets_pending(tmp_path):
    """[中-3] 画像コピー直後は処理待ち（pending）状態になる"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    assert ctl.pending_process is True
    assert ctl.get_status()["pending"] is True


def test_scan_clears_pending_when_structured_generated(tmp_path):
    """[中-3] structured_data 生成後は pending が解除される"""

    def fake_scan(*args, **kwargs):
        input_dir = Path(args[0])
        output_dir = Path(args[2])
        output_dir.mkdir(parents=True, exist_ok=True)
        for p in input_dir.iterdir():
            if p.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                mtime = int(p.stat().st_mtime)
                out = output_dir / f"{p.stem}_{mtime}-structured_data.json"
                if not out.exists():
                    out.write_text('{"name": "山田 太郎"}', encoding="utf-8")
        return 1

    ctl = _make_ctl(tmp_path, scan_fn=fake_scan)
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    assert ctl.pending_process is True
    mtime = ctl._pending_mtime
    assert mtime is not None
    ctl._run_scan()
    assert ctl.pending_process is False
    assert (ctl.output_dir / f"IMG_001_{mtime}-structured_data.json").exists()


def test_pending_cleared_when_image_moved_to_failed(tmp_path):
    """[中-3] 処理待ち画像が failed_dir へ退避済みなら pending が解除される"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    ctl.failed_dir.mkdir(parents=True, exist_ok=True)
    # move_to_failed は copy2 のため mtime が保存される前提で退避を再現する
    shutil.copy2(ctl.input_dir / "IMG_001.jpg", ctl.failed_dir / "IMG_001.jpg")
    ctl._run_scan()
    assert ctl.pending_process is False


def test_pending_stays_until_consumed(tmp_path):
    """[中-3] スキャンが画像を処理しなかった場合は pending が維持される"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    ctl._run_scan()
    assert ctl.pending_process is True


def test_pending_not_cleared_by_old_output(tmp_path):
    """[PR59-中1] 過去の同名出力が残っていても、新しい画像が未処理なら pending は解除されない"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    # 過去のアップロードで生成された出力（旧 mtime）を事前作成
    ctl.output_dir.mkdir(parents=True, exist_ok=True)
    (ctl.output_dir / "IMG_001_9999999999-structured_data.json").write_text('{"name": "過去"}', encoding="utf-8")
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    assert ctl.pending_process is True
    ctl._run_scan()  # スキャンは何も処理しない
    assert ctl.pending_process is True  # 過去出力では解除されない


def test_pending_not_cleared_by_old_failed_copy(tmp_path):
    """[PR59-中1] 過去の failed コピー（mtime 不一致）では pending は解除されない"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.failed_dir.mkdir(parents=True, exist_ok=True)
    # 過去の失敗退避（mtime が古い）を事前作成
    old = ctl.failed_dir / "IMG_001.jpg"
    old.write_bytes(b"old-failed-bytes")
    os.utime(old, (1_000_000_000, 1_000_000_000))  # 2001 年相当の旧 mtime
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    ctl._run_scan()
    assert ctl.pending_process is True


def test_pending_cleared_by_new_output_with_matching_mtime(tmp_path):
    """[PR59-中1] コピー時 mtime と一致する出力が生成されたら pending が解除される"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    mtime = ctl._pending_mtime
    assert mtime is not None
    ctl.output_dir.mkdir(parents=True, exist_ok=True)
    (ctl.output_dir / f"IMG_001_{mtime}-structured_data.json").write_text('{"name": "山田 太郎"}', encoding="utf-8")
    ctl._run_scan()
    assert ctl.pending_process is False


def test_pending_cleared_by_failed_copy_with_matching_mtime(tmp_path):
    """[PR59-中1] mtime+size 一致の failed コピーで pending が解除される"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
    ctl.failed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ctl.input_dir / "IMG_001.jpg", ctl.failed_dir / "IMG_001.jpg")
    ctl._run_scan()
    assert ctl.pending_process is False


# --- DB アップロード保存 ---


def test_save_db_upload_migrates_and_sets_db_path(tmp_path):
    """アップロードした DB は data/ に保存され migration 済みになる（Q2 案A）"""
    db = tmp_path / "src.sqlite3"
    run_migrations(db, SCHEMA_PATH)
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    dest = ctl.save_db_upload(db.read_bytes(), "my.db")
    assert dest == tmp_path / "data" / "my.db"
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
    assert not (tmp_path / "data" / "bad.db").exists()
    # 一時ファイルも残さない
    assert list((tmp_path / "data").glob(".upload-*")) == []


def test_save_db_upload_rejects_db_without_required_tables(tmp_path):
    """必須テーブルが無い SQLite は拒否される"""
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    with pytest.raises(ValueError, match="必須テーブル"):
        ctl.save_db_upload(other.read_bytes(), "other.db")
    assert not (tmp_path / "data" / "other.db").exists()


# --- [高-1] 既存 DB の上書き・削除防止 ---


def _make_existing_db(path: Path, marker: str) -> None:
    """既存 DB を作成し、内容を識別できる marker テーブルを入れる。"""
    run_migrations(path, SCHEMA_PATH)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
    conn.execute("INSERT INTO marker (value) VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def _read_marker(path: Path) -> list[str]:
    """marker テーブルの値を読み出す。"""
    conn = sqlite3.connect(str(path))
    try:
        return [r[0] for r in conn.execute("SELECT value FROM marker")]
    finally:
        conn.close()


def test_save_db_upload_invalid_keeps_existing_db(tmp_path):
    """[高-1] 不正バイト列を既存同名 DB でアップロードしても既存 DB は消えない"""
    existing = tmp_path / "data" / "db.sqlite3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    _make_existing_db(existing, "original")
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    with pytest.raises(Exception):
        ctl.save_db_upload(b"not a sqlite db", "db.sqlite3")
    assert existing.exists()
    assert _read_marker(existing) == ["original"]


def test_save_db_upload_valid_keeps_existing_db(tmp_path):
    """[高-1] 正常 DB を既存同名でアップロードしても既存 DB は上書きされない"""
    existing = tmp_path / "data" / "db.sqlite3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    _make_existing_db(existing, "original")
    src = tmp_path / "src.sqlite3"
    run_migrations(src, SCHEMA_PATH)
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    dest = ctl.save_db_upload(src.read_bytes(), "db.sqlite3")
    # 既存ファイルはそのまま残る
    assert existing.exists()
    assert _read_marker(existing) == ["original"]
    # 新しい内容は別名（連番）で保存される
    assert dest != existing
    assert dest.exists()
    assert dest.name == "db_1.sqlite3"
    assert ctl.db_path == str(dest)


def test_save_db_upload_does_not_delete_existing_db_on_failure(tmp_path):
    """[高-1] migration 失敗時も既存 DB は削除されない"""
    existing = tmp_path / "data" / "db.sqlite3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    _make_existing_db(existing, "original")
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    # 壊れたスキーマパスで migration を失敗させる
    ctl.schema_path = tmp_path / "missing_schema.sql"
    with pytest.raises(Exception):
        ctl.save_db_upload(existing.read_bytes(), "db.sqlite3")
    assert existing.exists()
    assert _read_marker(existing) == ["original"]
    assert list((tmp_path / "data").glob(".upload-*")) == []


def test_save_db_upload_rejects_empty_filename(tmp_path):
    """空のファイル名は拒否される"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    with pytest.raises(ValueError):
        ctl.save_db_upload(b"x", "")


# --- [低-1] テストの隔離: リポジトリ直下の data/ を書き換えない ---


def test_default_data_dir_is_isolated_from_repo_data():
    """[低-1] data_dir 未指定でも保存先はリポジトリ直下の data/ ではない"""
    ctl = WatchController()
    assert ctl.data_dir != REPO_DATA_DIR
    assert REPO_DATA_DIR not in ctl.data_dir.parents


def test_save_db_upload_with_default_data_dir_does_not_touch_repo(tmp_path):
    """[低-1] data_dir 未指定のアップロードでもリポジトリ直下の data/ には保存されない"""
    db = tmp_path / "src.sqlite3"
    run_migrations(db, SCHEMA_PATH)
    ctl = WatchController()
    dest = ctl.save_db_upload(db.read_bytes(), "isolated.db")
    assert dest.exists()
    assert REPO_DATA_DIR not in dest.parents
    assert not (REPO_DATA_DIR / "isolated.db").exists()


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


def test_copy_image_rejects_existing_same_name(tmp_path):
    """[中-5] 同名の既存画像は上書きされず FileExistsError になる"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    ctl.copy_image("IMG_001.jpg", b"first-bytes")
    # [PR59-中3] pending ガードが copy_image 内へ移ったため、
    # スキャンによる消費を模擬して pending を解除してから再アップロードする
    ctl._reset_pending()
    with pytest.raises(FileExistsError):
        ctl.copy_image("IMG_001.jpg", b"second-bytes")
    # 原画像は失われない
    assert (ctl.input_dir / "IMG_001.jpg").read_bytes() == b"first-bytes"


# --- [PR59-中3] 状態確認＋変更の原子性（scan lock による一連化） ---


def _start_slow_scan(tmp_path, entered: threading.Event, release: threading.Event) -> WatchController:
    """slow_scan を注入したコントローラを作り watch を開始し、スキャン開始を待つ。"""

    def slow_scan(*args, **kwargs):
        entered.set()
        release.wait(timeout=10)

    ctl = _make_ctl(tmp_path, scan_fn=slow_scan, poll_interval=0.1)
    ctl.start_watch()
    assert entered.wait(timeout=5), "scan が開始されない"
    return ctl


def test_copy_image_rejected_while_scanning(tmp_path):
    """[PR59-中3] スキャン中のコピーは WatchBusyError で拒否されファイルも作られない"""
    entered = threading.Event()
    release = threading.Event()
    ctl = _start_slow_scan(tmp_path, entered, release)
    try:
        with pytest.raises(WatchBusyError):
            ctl.copy_image("IMG_001.jpg", b"fake-jpeg-bytes")
        assert not (ctl.input_dir / "IMG_001.jpg").exists()
        assert ctl.pending_process is False
    finally:
        release.set()
        ctl.stop_watch()


def test_save_db_upload_rejected_while_scanning(tmp_path):
    """[PR59-中3] スキャン中の DB 差し替えは拒否され db_path は変わらない"""
    entered = threading.Event()
    release = threading.Event()
    ctl = _start_slow_scan(tmp_path, entered, release)
    src = tmp_path / "src.sqlite3"
    run_migrations(src, SCHEMA_PATH)
    try:
        with pytest.raises(WatchBusyError):
            ctl.save_db_upload(src.read_bytes(), "swap.db")
        assert not (ctl.data_dir / "swap.db").exists()
        assert ctl.db_path == str(tmp_path / "db.sqlite3")
    finally:
        release.set()
        ctl.stop_watch()


def test_set_input_dir_rejected_while_scanning(tmp_path, monkeypatch):
    """[PR59-中3] スキャン中の input_dir 変更は拒否される（設定更新の原子性）"""
    entered = threading.Event()
    release = threading.Event()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    ctl = _start_slow_scan(tmp_path, entered, release)
    try:
        with pytest.raises(WatchBusyError):
            ctl.set_input_dir(home / "another")
        assert ctl.input_dir == tmp_path / "input"
    finally:
        release.set()
        ctl.stop_watch()


def test_set_preprocess_mode_rejected_while_scanning(tmp_path):
    """[PR59-中3] スキャン中の preprocess_mode 変更は拒否される"""
    entered = threading.Event()
    release = threading.Event()
    ctl = _start_slow_scan(tmp_path, entered, release)
    try:
        with pytest.raises(WatchBusyError):
            ctl.set_preprocess_mode("clahe")
        assert ctl.preprocess_mode == "none"
    finally:
        release.set()
        ctl.stop_watch()


def test_copy_image_concurrent_same_name_one_succeeds(tmp_path):
    """[PR59-中3] 同名画像の並行コピーは 1 件のみ成功（scan lock + O_EXCL）"""
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0)
    results: list[tuple[str, int]] = []
    results_lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            ctl.copy_image("IMG_001.jpg", f"bytes-{i}".encode())
        except WatchBusyError:
            outcome = "busy"
        else:
            outcome = "ok"
        with results_lock:
            results.append((outcome, i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [r for r in results if r[0] == "ok"]
    busy = [r for r in results if r[0] == "busy"]
    assert len(ok) == 1
    assert len(busy) == 7
    winner = ok[0][1]
    dest = ctl.input_dir / "IMG_001.jpg"
    assert dest.read_bytes() == f"bytes-{winner}".encode()
    assert ctl.pending_process is True


# --- 状態 ---


def test_get_status_shape(tmp_path):
    ctl = _make_ctl(tmp_path, scan_fn=lambda *a, **k: 0, with_db=False)
    s = ctl.get_status()
    assert set(s) == {
        "watch_on",
        "is_processing",
        "pending",
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
