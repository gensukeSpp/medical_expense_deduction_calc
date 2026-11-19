"""Web UI 用 watch オーケストレーター (Issue #56)。

CLI の --watch / --input-dir / --db-path / --preprocess-mode 相当の状態と
ポーリング監視スレッドを保持し、FastAPI (server.py) から操作される。
状態はプロセス内メモリのみ（Q3 案A: ユーザー確認済み・DB スキーマ変更なし）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from app import watcher

LOG = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = str(Path.home() / "Downloads" / "receipts")
PREPROCESS_MODES = ["none", "clahe", "adaptive", "clahe+adaptive"]
DATA_DIR = Path("data")
SCHEMA_PATH = Path("docs/schema.sql")

# SQLite ファイルヘッダ（16 バイト）
SQLITE_MAGIC = b"SQLite format 3\x00"
# アップロードされた DB に必須のテーブル（存在しない場合は拒否する）
REQUIRED_TABLES = ("receipts", "clinics", "templates", "corrections")


def _default_ocr_factory():
    """PaddleOCR の遅延初期化。--serve 起動を速く保つため module 直下では生成しない。"""
    from paddleocr import PaddleOCR

    return PaddleOCR(use_angle_cls=True, lang="japan", enable_mkldnn=False)


def _atomic_install(src: Path, dest: Path) -> None:
    """一時ファイルを配置先へ原子的に移動する（既存ファイルを上書きしない）。

    ``os.link`` は dest が既に存在する場合に失敗するため、上書きが原理的に
    起きない。ハードリンク非対応環境では ``O_EXCL`` で名前を予約してから置換する。

    Args:
        src: 移動元の一時ファイル。
        dest: 移動先（存在しないこと）。

    Raises:
        FileExistsError: 配置先ファイルが既に存在する場合。
    """
    try:
        os.link(src, dest)
    except FileExistsError:
        raise
    except OSError:
        # ハードリンク非対応環境: O_EXCL で名前を排他予約してから置換する
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise
        os.close(fd)
        try:
            os.replace(src, dest)
        except Exception:
            dest.unlink(missing_ok=True)  # 予約した空ファイルを片付ける
            raise
        return
    os.unlink(src)


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
        data_dir: str | Path | None = None,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.processed_dir = Path(processed_dir)
        self.failed_dir = Path(failed_dir)
        # data_dir は実行時にモジュール変数 DATA_DIR から解決する。
        # 既定値を引数定義時に固定しないことで、テストから DATA_DIR を差し替えられる。
        self.data_dir = Path(data_dir) if data_dir else Path(DATA_DIR)
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
        # [中-3] 処理待ち（画像コピー後〜 structured 生成完了まで）の状態
        self._pending_process = False
        self._pending_image: str | None = None
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
        p = Path(path)
        self._run_migrations(p)
        self._db_path = p
        LOG.info("db_path -> %s", p)

    def _run_migrations(self, db_path: Path) -> None:
        """指定 DB に migration を適用する。"""
        from app.db_migrations import run_migrations

        run_migrations(db_path, self.schema_path)

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
        """ポーリング監視スレッドを開始する。DB 未選択は ValueError。

        [中-1] スレッドごとに専用の stop Event を生成する。旧スレッドの
        停止要求（stop_event）は clear しないため、停止→再開で二重化しない。
        旧スレッドが生存中（stop_watch が join timeout した直後）は
        watch_on が True のためここに到達しない（二重起動の防止）。
        """
        if not self.has_db:
            raise ValueError("データベースを選択してください")
        if self.watch_on:
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._watch_loop, name="web-watcher", daemon=True)
        self._thread.start()
        LOG.info("watch started (poll_interval=%d)", self.poll_interval)

    def stop_watch(self) -> None:
        """監視スレッドを停止する。

        [中-1] スキャン中で join timeout した場合は、生存中の _thread を
        保持する（watch_on が True のまま）。停止要求は専用 Event に出して
        あるため、スキャン完了後にスレッドは自己終了する。
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval + 5)
            if not self._thread.is_alive():
                self._thread = None
        LOG.info("watch stopped")

    @property
    def watch_on(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_processing(self) -> bool:
        return self._is_processing

    @property
    def pending_process(self) -> bool:
        """処理待ち（画像コピー後〜 structured 生成完了まで）かどうか。"""
        return self._pending_process

    def _watch_loop(self) -> None:
        # [中-1] 開始時点の専用 Event を捕捉する（再開で差し替えられても影響しない）
        stop_event = self._stop_event
        while not stop_event.is_set():
            self._run_scan()
            stop_event.wait(self.poll_interval)

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
            self._clear_pending_if_consumed()

    def _clear_pending_if_consumed(self) -> None:
        """処理待ち画像が処理済み（出力 JSON 生成 or failed 退避）なら pending を解除する。

        [中-3] 読込不能画像（raw が生成されない）でも failed/ への退避があれば解除し、
        画像指定が永久ロックされないようにする（既存の問題 1 との相互作用対策）。
        """
        if not self._pending_process or self._pending_image is None:
            return
        stem = Path(self._pending_image).stem
        consumed = (
            list(self.output_dir.glob(f"{stem}_*-raw_data.json"))
            or list(self.output_dir.glob(f"{stem}_*-structured_data.json"))
            or (self.failed_dir / self._pending_image).exists()
        )
        if consumed:
            LOG.info("Pending image processed: %s", self._pending_image)
            self._pending_image = None
            self._pending_process = False

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
        """アップロードされた DB を data/ 配下へ保存し migration を適用する。

        既存 DB を失わないよう、次の順序で処理する。

        1. 一時ファイル（``.upload-*.tmp``）へ書き込む
        2. SQLite ヘッダと必須テーブルの存在を検証する
        3. 一時ファイルへ migration を適用する
        4. 検証済みの内容を原子的に rename して保存する

        同名の既存ファイルは上書きせず連番（``name_1.db`` 等）で保存する。
        失敗時は今回作成した一時ファイルのみ削除する（既存 DB は残す）。

        Args:
            content: アップロードされたファイル内容。
            filename: 元ファイル名（basename のみ利用する）。

        Returns:
            Path: 実際に保存された DB ファイルのパス。

        Raises:
            ValueError: ファイル名が不正、または SQLite DB として不正な場合。
        """
        name = Path(filename).name  # basename のみ（パス注入防止）
        if not name or name in {".", ".."}:
            raise ValueError("ファイル名が不正です")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.data_dir / f".upload-{uuid4().hex}.tmp"
        try:
            tmp_path.write_bytes(content)
            self._validate_uploaded_db(tmp_path)
            self._run_migrations(tmp_path)
            dest = self._install_db(tmp_path, name)
        except Exception:
            LOG.exception("Failed to save uploaded DB %s", name)
            tmp_path.unlink(missing_ok=True)  # 今回作った一時ファイルのみ削除
            raise
        # migration は一時ファイルへ適用済みなので、配置先のパスを設定する
        self._db_path = dest
        LOG.info("db_path -> %s", dest)
        return dest

    def _validate_uploaded_db(self, path: Path) -> None:
        """アップロードされた DB ファイルの妥当性を検証する。

        SQLite ヘッダと必須テーブルの存在を確認する。

        Args:
            path: 検証対象の DB ファイルパス。

        Raises:
            ValueError: SQLite ヘッダが無い、または必須テーブルが存在しない場合。
        """
        with open(path, "rb") as f:
            header = f.read(len(SQLITE_MAGIC))
        if header != SQLITE_MAGIC:
            raise ValueError("SQLite データベースファイルではありません")
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as e:
            raise ValueError(f"データベースを開けません: {e}") from e
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as e:
            raise ValueError(f"データベースを開けません: {e}") from e
        finally:
            conn.close()
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            raise ValueError(f"必須テーブルがありません: {', '.join(missing)}")

    def _install_db(self, tmp_path: Path, name: str) -> Path:
        """一時ファイルを data/ 配下へ配置する（既存ファイルを上書きしない）。

        同名のファイルが既にある場合は連番（``name_1.db`` 等）を付与して保存する。

        Args:
            tmp_path: 配置する一時ファイル。
            name: 保存したいファイル名（basename）。

        Returns:
            Path: 実際に配置されたパス。

        Raises:
            ValueError: 保存先が見つからない場合。
        """
        stem, suffix = Path(name).stem, Path(name).suffix
        for index in range(1000):
            dest = self.data_dir / (name if index == 0 else f"{stem}_{index}{suffix}")
            if dest.exists():
                continue
            try:
                _atomic_install(tmp_path, dest)
            except FileExistsError:
                # 配置直前に同名ファイルが作成された場合は次の連番へ
                continue
            if index:
                LOG.info("Existing DB found, saved with a new name: %s", dest)
            return dest
        raise ValueError(f"保存先が見つかりません: {name}")

    def copy_image(self, filename: str, content: bytes) -> Path:
        """アップロード画像を input_dir へコピーする（案C: コピーのみ・即時処理しない）。

        [中-3] コピー直後〜 structured 生成完了までを「処理待ち」状態として保持し、
        スキャン 1 サイクルで消費されたら解除する。

        Args:
            filename: 元ファイル名（basename のみ利用する）。
            content: ファイル内容。

        Returns:
            Path: コピー先のパス。

        Raises:
            ValueError: 画像ファイルでない場合。
        """
        name = Path(filename).name
        if Path(name).suffix.lower() not in watcher.IMAGE_EXT:
            raise ValueError("画像ファイルのみ指定できます")
        self.input_dir.mkdir(parents=True, exist_ok=True)
        dest = self.input_dir / name
        dest.write_bytes(content)
        self._pending_image = name
        self._pending_process = True
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
            "pending": self.pending_process,
            "input_dir": str(self.input_dir),
            "db_path": self.db_path,
            "has_db": self.has_db,
            "preprocess_mode": self.preprocess_mode,
            "poll_interval": self.poll_interval,
            "last_error": self.last_error,
            "structured_count": len(list(self.output_dir.glob("*-structured_data.json"))),
        }
