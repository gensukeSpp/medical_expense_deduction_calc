from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Set

LOG = logging.getLogger("file_repository")


class FileRepository:
    """
    ファイルシステム操作を担当するクラス。
    ファイルの移動、退避、クリーンアップ、安定性チェックなどの責務を持つ。
    """

    def __init__(self, output_dir: Path, processed_dir: Path, failed_dir: Path, image_exts: Set[str] | None = None):
        self.output_dir = output_dir
        self.processed_dir = processed_dir
        self.failed_dir = failed_dir
        self.image_exts = image_exts or {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

        # ディレクトリの作成を保証
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def is_image_file(self, p: Path) -> bool:
        """ファイルが画像ファイルであるか判定する。"""
        if "_resized" in p.name or "temp" in p.name:
            return False
        return p.is_file() and p.suffix.lower() in self.image_exts

    def is_file_stable(self, p: Path, interval: float = 0.5, checks: int = 3) -> bool:
        """ファイルサイズが一定期間変化しないことを確認し、書き込み完了を判定する。"""
        try:
            prev = p.stat().st_size
        except Exception:
            return False
        for _ in range(checks):
            time.sleep(interval)
            try:
                curr = p.stat().st_size
            except Exception:
                return False
            if curr != prev:
                return False
            prev = curr
        return True

    def move_to_processed(self, p: Path) -> None:
        """処理済みディレクトリへ移動する（必要に応じて）。"""
        # 現在の watcher.py では processed_dir への移動は明示的に行われていないようだが、
        # 将来的な拡張のために定義。
        dest = self.processed_dir / p.name
        shutil.move(str(p), str(dest))
        LOG.info("Moved %s to processed: %s", p, dest)

    def move_to_failed(self, p: Path) -> None:
        """失敗したファイルを失敗ディレクトリへ退避する（input から取り除く）。"""
        try:
            dest = self.failed_dir / p.name
            if dest.exists():
                dest = self.failed_dir / f"{int(time.time())}_{p.name}"
            shutil.move(str(p), str(dest))
            LOG.info("Moved failed %s -> %s", p, dest)
        except Exception:
            LOG.exception("Failed to move failed file %s", p)

    def cleanup_file(self, p: Path) -> None:
        """一時ファイルなどを削除する。"""
        try:
            if p.exists():
                p.unlink()
                LOG.info("Cleaned up file: %s", p)
        except Exception:
            LOG.exception("Failed to cleanup file: %s", p)
