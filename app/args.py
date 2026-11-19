import argparse
import ipaddress
import logging
import os
from pathlib import Path
from typing import Iterable


def setup_args(argv: Iterable[str] | None = None):
    """引数を解析し、初期化処理を行う。
    Returns:
        argparse.Namespace: 解析されたコマンドライン引数。
    """
    # Setup logging
    logging.basicConfig(level=logging.INFO)

    # CPU実行をデフォルトにする
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ.get("CUDA_VISIBLE_DEVICES", "-1")

    parser = argparse.ArgumentParser(description="Medical expense OCR runner")
    parser.add_argument("--input-dir", default=str(Path.home() / "Downloads" / "receipts"))
    parser.add_argument("--image-name", default=None, help="Process a single image by name")
    parser.add_argument("--output-dir", default="output_json", help="JSON output directory")
    parser.add_argument("--input-json", default=None, help="Process a single OCR JSON file (path)")
    parser.add_argument("--model", default="mock", help="LLM model name or 'mock' for local heuristic")
    parser.add_argument("--watch", action="store_true", help="Run folder watcher instead of single-file processing")
    parser.add_argument(
        "--use-watchdog",
        action="store_true",
        help="When watching, use watchdog observer (inotify) if available",
    )
    parser.add_argument("--processed-dir", default="processed", help="Directory to move processed images to")
    parser.add_argument("--failed-dir", default="failed", help="Directory to move failed images to")
    parser.add_argument("--poll-interval", type=int, default=10, help="Polling interval for watcher (seconds)")
    parser.add_argument("--run-once", action="store_true", help="Scan once and exit")
    parser.add_argument("--retries", type=int, default=1, help="Retry attempts per file on failure")
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (e.g., 'data/db.sqlite3'). If set, results will be persisted to the DB.",
    )
    parser.add_argument("--serve", action="store_true", help="Start Web UI server")
    parser.add_argument("--host", default="127.0.0.1", help="Web server host")
    parser.add_argument("--port", type=int, default=8000, help="Web server port")
    parser.add_argument(
        "--preprocess-mode",
        default="none",
        choices=["none", "clahe", "adaptive", "clahe+adaptive"],
        help="低Confidence時に適用する前処理 (Issue #36)。default: none",
    )
    parser.add_argument(
        "--preprocess-force",
        action="store_true",
        help="Confidence に関係なく前処理を強制適用する (Issue #36)。--preprocess-mode が none 以外の時のみ有効",
    )
    parser.add_argument(
        "--target-short-side",
        type=int,
        default=960,
        help="リサイズ時、短辺の目標サイズ(px)。default: 960",
    )
    args = parser.parse_args()

    return args


def setup_directories(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    """出力および管理用ディレクトリを作成し、Pathオブジェクトを返す。
    Args:
        args (argparse.Namespace): 解析されたコマンドライン引数。
    Returns:
        tuple[Path, Path, Path, Path]: (input_dir, output_dir, processed_dir, failed_dir) のタプル。
    """
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {input_dir}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    processed_dir = Path(args.processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    failed_dir = Path(args.failed_dir)
    failed_dir.mkdir(parents=True, exist_ok=True)

    return input_dir, output_dir, processed_dir, failed_dir


def is_loopback_host(host: str) -> bool:
    """Web サーバの host がループバック（localhost / 127.0.0.1 / ::1 等）か判定する。

    [中-4] 認証のない変更系エンドポイントを LAN に曝さないための起動ガード。
    IP として解釈できないホスト名は非ループバック扱い（False）とする。

    Args:
        host: 判定するホスト名または IP アドレス。

    Returns:
        bool: ループバックなら True、それ以外 False。
    """
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
