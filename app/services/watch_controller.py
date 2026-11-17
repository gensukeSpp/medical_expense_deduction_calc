"""Web UI 用 watch オーケストレーター (Issue #56)。

CLI の --watch / --input-dir / --db-path / --preprocess-mode 相当の状態と
ポーリング監視スレッドを保持し、FastAPI (server.py) から操作される。
状態はプロセス内メモリのみ（Q3 案A: ユーザー確認済み・DB スキーマ変更なし）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app import watcher

LOG = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = str(Path.home() / "Downloads" / "receipts")
PREPROCESS_MODES = ["none", "clahe", "adaptive", "clahe+adaptive"]
DATA_DIR = Path("data")
SCHEMA_PATH = Path("docs/schema.sql")


def _default_ocr_factory():
    """PaddleOCR の遅延初期化。--serve 起動を速く保つため module 直下では生成しない。"""
    from paddleocr import PaddleOCR

    return PaddleOCR(use_angle_cls=True, lang="japan", enable_mkldnn=False)


class WatchController:
    """Web UI 用の watch 状態・設定・処理オーケストレーター。"""

    def __init__(
        self,
        input_dir: str | Path = DEFAULT_INPUT_DIR,
        output_dir: str | Path = "output_json",
        processed_dir: str | Path = "processed",
        failed_dir: str | Path = "failed",
        db_path: str | Path | None = None,
        preprocess_mode: str = "none",
        poll_interval: int = 5,
        model: str = "mock",
        scan_fn=watcher.scan_and_process,
        ocr_factory=None,
        schema_path: Path = SCHEMA_PATH,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.processed_dir = Path(processed_dir)
        self.failed_dir = Path(failed_dir)
        self.poll_interval = poll_interval
        self.model = model
        self.schema_path = Path(schema_path)
        self.scan_fn = scan_fn
        self._ocr_factory = ocr_factory or _default_ocr_factory
        self._db_path: Path | None = None
        self._preprocess_mode = "none"
        self._ocr = None
        self._ocr_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_processing = False
        self.last_error: str | None = None
        self.set_preprocess_mode(preprocess_mode)
        if db_path:
            self.set_db_path(db_path)

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------

    def set_input_dir(self, path: str | Path) -> Path:
        """input_dir を更新し、存在しなければ作成する。"""
        p = Path(path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        self.input_dir = p
        LOG.info("input_dir -> %s", p)
        return p

    def set_db_path(self, path: str | Path | None) -> None:
        """db_path を設定し、migration を適用する。None で未選択に戻す。"""
        if path is None:
            self._db_path = None
            return
        from app.db_migrations import run_migrations

        p = Path(path)
        run_migrations(p, self.schema_path)
        self._db_path = p
        LOG.info("db_path -> %s", p)

    def set_preprocess_mode(self, mode: str) -> None:
        """前処理モードを検証して設定する。未知の値は ValueError。"""
        if mode not in PREPROCESS_MODES:
            raise ValueError(f"unknown preprocess_mode: {mode}")
        self._preprocess_mode = mode

    @property
    def preprocess_mode(self) -> str:
        return self._preprocess_mode

    @property
    def has_db(self) -> bool:
        return self._db_path is not None

    @property
    def db_path(self) -> str | None:
        return str(self._db_path) if self._db_path else None

    # ------------------------------------------------------------------
    # watch スレッド
    # ------------------------------------------------------------------

    def start_watch(self) -> None:
        """ポーリング監視スレッドを開始する。DB 未選択は ValueError。"""
        if not self.has_db:
            raise ValueError("データベースを選択してください")
        if self.watch_on:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, name="web-watcher", daemon=True)
        self._thread.start()
        LOG.info("watch started (poll_interval=%d)", self.poll_interval)

    def stop_watch(self) -> None:
        """監視スレッドを停止する。スキャン中の場合は timeout で戻る（daemon）。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval + 5)
            self._thread = None
        LOG.info("watch stopped")

    @property
    def watch_on(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_processing(self) -> bool:
        return self._is_processing

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_scan()
            self._stop_event.wait(self.poll_interval)

    def _run_scan(self) -> None:
        """input_dir をスキャンして処理する。既に処理中ならスキップ。"""
        if not self._scan_lock.acquire(blocking=False):
            LOG.debug("Scan already in progress, skipping")
            return
        self._is_processing = True
        try:
            self.scan_fn(
                self.input_dir,
                self._get_ocr(),
                self.output_dir,
                self.processed_dir,
                self.failed_dir,
                model=self.model,
                db_path=self._db_path,
                preprocess_mode=self._preprocess_mode,
            )
            self.last_error = None
        except Exception:
            LOG.exception("Scan failed")
            self.last_error = "スキャン中にエラーが発生しました"
        finally:
            self._is_processing = False
            self._scan_lock.release()

    def _get_ocr(self):
        """OCR エンジンの遅延初期化（スレッドセーフ）。テストでは ocr_factory を注入。"""
        if self._ocr is None:
            with self._ocr_lock:
                if self._ocr is None:
                    self._ocr = self._ocr_factory()
        return self._ocr

    # ------------------------------------------------------------------
    # DB アップロード保存 / 画像コピー
    # ------------------------------------------------------------------

    def save_db_upload(self, content: bytes, filename: str) -> Path:
        """アップロードされた DB を data/ に保存し migration を適用する。

        不正なファイルは例外を投げ、保存先を掃除する（Q2 案A: data/ へ元ファイル名で保存）。
        """
        name = Path(filename).name  # basename のみ（パス注入防止）
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        dest = DATA_DIR / name
        dest.write_bytes(content)
        try:
            self.set_db_path(dest)
        except Exception:
            LOG.exception("Failed to migrate uploaded DB %s", dest)
            dest.unlink(missing_ok=True)
            raise
        return dest

    def copy_image(self, filename: str, content: bytes) -> Path:
        """アップロード画像を input_dir へコピーする（案C: コピーのみ・即時処理しない）。"""
        name = Path(filename).name
        if Path(name).suffix.lower() not in watcher.IMAGE_EXT:
            raise ValueError("画像ファイルのみ指定できます")
        self.input_dir.mkdir(parents=True, exist_ok=True)
        dest = self.input_dir / name
        dest.write_bytes(content)
        LOG.info("Copied uploaded image to %s", dest)
        return dest

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """フロントエンドのポーリング用に現在状態を返す。"""
        return {
            "watch_on": self.watch_on,
            "is_processing": self.is_processing,
            "input_dir": str(self.input_dir),
            "db_path": self.db_path,
            "has_db": self.has_db,
            "preprocess_mode": self.preprocess_mode,
            "poll_interval": self.poll_interval,
            "last_error": self.last_error,
            "structured_count": len(list(self.output_dir.glob("*-structured_data.json"))),
        }
