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

from app import watcher
from app.args import validate_input_dir

LOG = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = str(Path.home() / "Downloads" / "receipts")
PREPROCESS_MODES = ["none", "clahe", "adaptive", "clahe+adaptive"]
DATA_DIR = Path("data")
SCHEMA_PATH = Path("docs/schema.sql")

# SQLite ファイルヘッダ（16 バイト）
SQLITE_MAGIC = b"SQLite format 3\x00"
# アップロードされた DB に必須のテーブル（存在しない場合は拒否する）
REQUIRED_TABLES = ("receipts", "clinics", "templates", "corrections")

# [PR59-中3] スキャン処理中の変更拒否メッセージ（/images・/config の 409 detail と同じ）
BUSY_MESSAGE = "処理中です。完了までお待ちください"


class WatchBusyError(RuntimeError):
    """スキャン処理中で状態確認＋変更を原子的に実行できない場合に送出する。

    [PR59-中3] 状態確認と変更処理を _scan_lock 内で一連化するため、
    スキャン中（または処理待ち）の変更要求はこの例外で拒否する。
    サーバ側（app/web/server.py）は 409 にマッピングする。
    """


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
        # [PR59-中1] コピー時スナップショット（その画像固有の成果物を特定する基準）
        self._pending_mtime: int | None = None
        self._pending_size: int | None = None
        self.last_error: str | None = None
        self.set_preprocess_mode(preprocess_mode)
        if db_path:
            self.set_db_path(db_path)

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------

    def set_input_dir(self, path: str | Path) -> Path:
        """input_dir を更新し、存在しなければ作成する。

        [中-4] 認証のない変更系エンドポイントの任意パス操作を防ぐため、
        絶対パスかつユーザーのホームディレクトリ配下のみ許可する。

        [PR59-中3] 設定更新を _scan_lock 内で行い、スキャンの設定スナップショット
        （_run_scan が input_dir 等を読み取る瞬間）との競合を防ぐ。
        スキャン中は WatchBusyError で拒否する。

        Args:
            path: 指定する input_dir のパス。

        Returns:
            Path: 更新後の input_dir。

        Raises:
            ValueError: 相対パスの場合、またはホームディレクトリ配下でない場合。
            WatchBusyError: スキャン処理中の場合。
        """
        p = validate_input_dir(path)
        if not self._scan_lock.acquire(blocking=False):
            raise WatchBusyError(BUSY_MESSAGE)
        try:
            p.mkdir(parents=True, exist_ok=True)
            self.input_dir = p
        finally:
            self._scan_lock.release()
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
        """前処理モードを検証して設定する。未知の値は ValueError。

        [PR59-中3] 設定更新を _scan_lock 内で行い、スキャンの設定スナップショット
        との競合を防ぐ。スキャン中は WatchBusyError で拒否する。

        Raises:
            ValueError: 未知のモードを指定した場合。
            WatchBusyError: スキャン処理中の場合。
        """
        if mode not in PREPROCESS_MODES:
            raise ValueError(f"unknown preprocess_mode: {mode}")
        if not self._scan_lock.acquire(blocking=False):
            raise WatchBusyError(BUSY_MESSAGE)
        try:
            self._preprocess_mode = mode
        finally:
            self._scan_lock.release()

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
            # [PR59-中3] pending 解除も _scan_lock 内で行い、copy_image との
            # 状態遷移を直列化する（解除とコピーの競合を防ぐ）
            self._clear_pending_if_consumed()
            self._scan_lock.release()

    def _reset_pending(self) -> None:
        """pending 状態とスナップショットをまとめてクリアする。"""
        self._pending_image = None
        self._pending_process = False
        self._pending_mtime = None
        self._pending_size = None

    def _failed_copy_exists(self) -> bool:
        """pending 画像の失敗退避（mtime+size 一致）が failed_dir にあるか判定する。

        move_to_failed は shutil.copy2 のため mtime が保存される。同名 failed が既に
        ある場合は {timestamp}_ プレフィクスが付くため、名前一致ではなく mtime+size で
        特定する。
        """
        if self._pending_mtime is None or self._pending_size is None:
            return False
        if not self.failed_dir.is_dir():
            return False
        for f in self.failed_dir.iterdir():
            if not f.is_file():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            if int(st.st_mtime) == self._pending_mtime and st.st_size == self._pending_size:
                return True
        return False

    def _clear_pending_if_consumed(self) -> None:
        """処理待ち画像が処理済み（出力 JSON 生成 or failed 退避）なら pending を解除する。

        [中-3] 読込不能画像（raw が生成されない）でも failed/ への退避があれば解除し、
        画像指定が永久ロックされないようにする（既存の問題 1 との相互作用対策）。

        [PR59-中1] ファイル名（stem）の glob 一致ではなく、コピー時に記録した
        mtime+size スナップショットで「その画像」の成果物を特定する。過去の同名出力や
        過去の failed コピーでは解除されない。
        """
        if not self._pending_process or self._pending_image is None:
            return
        if self._pending_mtime is None or self._pending_size is None:
            return  # スナップショット未記録（旧状態）は解除しない
        stem = Path(self._pending_image).stem
        mtime = self._pending_mtime
        consumed = (
            (self.output_dir / f"{stem}_{mtime}-raw_data.json").exists()
            or (self.output_dir / f"{stem}_{mtime}-structured_data.json").exists()
            or self._failed_copy_exists()
        )
        if consumed:
            LOG.info("Pending image processed: %s", self._pending_image)
            self._reset_pending()

    def _get_ocr(self):
        """OCR エンジンの遅延初期化（スレッドセーフ）。テストでは ocr_factory を注入。"""
        if self._ocr is None:
            with self._ocr_lock:
                if self._ocr is None:
                    self._ocr = self._ocr_factory()
        return self._ocr

    # ------------------------------------------------------------------
    # DB パス選択 / 新規作成 / 画像コピー
    # ------------------------------------------------------------------

    def select_db_path(self, path: str | Path) -> Path:
        """既存の DB パスを参照対象として固定する（案A: コピー・連番を作らない）。

        存在チェック・SQLite ヘッダ/必須テーブル検証・migration を実行し、_db_path に
        設定する。既存ファイルをそのまま使用するため data/ への保存は行わない。

        [PR59-中3] 処理中確認と DB 置換を _scan_lock 内で一連化する。

        Args:
            path: 参照する DB ファイルの絶対パス。

        Returns:
            Path: 設定された DB ファイルのパス。

        Raises:
            ValueError: 相対パス・存在しない・SQLite として不正な場合。
            WatchBusyError: スキャン処理中の場合。
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ValueError("絶対パスを指定してください")
        if not p.exists():
            raise ValueError(f"ファイルが存在しません: {p}")
        if not self._scan_lock.acquire(blocking=False):
            raise WatchBusyError(BUSY_MESSAGE)
        try:
            if self._is_processing:
                raise WatchBusyError(BUSY_MESSAGE)
            self._validate_db_file(p)
            self._run_migrations(p)
            self._db_path = p
            LOG.info("db_path -> %s", p)
            return p
        finally:
            self._scan_lock.release()

    def _validate_db_file(self, path: Path) -> None:
        """DB ファイルの SQLite ヘッダと必須テーブルの存在を検証する。

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

    def create_db_path(self, path: str | Path) -> Path:
        """存在しないパスへ空 DB を新規作成し migration を適用して参照対象にする。

        初回起動（参照する DB が未だ無い）で新規作成するための経路（案A 補足）。
        既存ファイル／ディレクトリを指定した場合は ValueError で拒否し、既存 DB を
        上書き・変更しない（PR #63 レビュー [P2]#1）。作成時の競合も防ぐため、
        空ファイルの作成は copy_image と同じ O_EXCL で原子的に行う。
        migration 失敗時は作成したばかりのファイルを削除して副作用を残さない。

        [PR59-中3] 処理中確認と DB 置換を _scan_lock 内で一連化する。

        Args:
            path: 作成する DB ファイルの絶対パス。

        Returns:
            Path: 作成された DB ファイルのパス。

        Raises:
            ValueError: 相対パス・既存ファイル／ディレクトリを指定した場合・
                スキーマ関連の失敗の場合。
            WatchBusyError: スキャン処理中の場合。
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ValueError("絶対パスを指定してください")
        if not self._scan_lock.acquire(blocking=False):
            raise WatchBusyError(BUSY_MESSAGE)
        try:
            if self._is_processing:
                raise WatchBusyError(BUSY_MESSAGE)
            if p.exists():
                raise ValueError(f"ファイルが既に存在します: {p}")
            p.parent.mkdir(parents=True, exist_ok=True)
            # [PR63-P2-1] 存在確認と作成の間にファイルが割り込んでも上書きしない
            # （copy_image と同じ O_EXCL による原子的な作成）
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ValueError(f"ファイルが既に存在します: {p}") from None
            os.close(fd)
            try:
                self._run_migrations(p)
            except Exception:
                # migration 失敗時は作成したばかりの空ファイルを残さない
                p.unlink(missing_ok=True)
                raise
            self._db_path = p
            LOG.info("db_path -> %s (created)", p)
            return p
        finally:
            self._scan_lock.release()

    def copy_image(self, filename: str, content: bytes) -> Path:
        """アップロード画像を input_dir へコピーする（案C: コピーのみ・即時処理しない）。

        [中-3] コピー直後〜 structured 生成完了までを「処理待ち」状態として保持し、
        スキャン 1 サイクルで消費されたら解除する。

        [中-5] 同名の既存画像は無確認で上書きせず FileExistsError にする。

        [PR59-中3] 状態確認（処理中/処理待ち）とコピーを _scan_lock 内で一連化し、
        並行リクエストやスキャン開始との競合を防ぐ。ファイル作成は O_EXCL で
        原子的に行い、存在確認と作成の間に同名ファイルが割り込めないようにする。

        Args:
            filename: 元ファイル名（basename のみ利用する）。
            content: ファイル内容。

        Returns:
            Path: コピー先のパス。

        Raises:
            ValueError: 画像ファイルでない場合。
            WatchBusyError: スキャン処理中または処理待ちの場合。
            FileExistsError: 同名の既存画像がある場合。
        """
        name = Path(filename).name
        if Path(name).suffix.lower() not in watcher.IMAGE_EXT:
            raise ValueError("画像ファイルのみ指定できます")
        if not self._scan_lock.acquire(blocking=False):
            raise WatchBusyError(BUSY_MESSAGE)
        try:
            if self._is_processing or self._pending_process:
                raise WatchBusyError(BUSY_MESSAGE)
            self.input_dir.mkdir(parents=True, exist_ok=True)
            dest = self.input_dir / name
            # [PR59-中3] O_EXCL で作成を予約し、存在確認と作成を原子的に行う
            try:
                fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                # [中-5] 同名の既存画像は無確認で上書きしない（連番は再処理による重複登録を招くため不可）
                raise FileExistsError(f"同名のファイルが既に存在します: {name}") from None
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(content)
            except Exception:
                dest.unlink(missing_ok=True)  # 書き込み失敗時は部分ファイルを残さない
                raise
            self._pending_image = name
            self._pending_process = True
            # [PR59-中1] コピー時スナップショットを記録する（解除判定の基準）
            st = dest.stat()
            self._pending_mtime = int(st.st_mtime)
            self._pending_size = st.st_size
            LOG.info("Copied uploaded image to %s", dest)
            return dest
        finally:
            self._scan_lock.release()

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
