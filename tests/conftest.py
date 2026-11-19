"""pytest 共通設定・フィクスチャ。

テストがリポジトリ直下の ``data/``（実データの置き場）を書き換えないことを保証する。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

packagedir = pathlib.Path(__file__).resolve().parent.parent
print(packagedir)
sys.path.append(str(packagedir))

# リポジトリ直下の data/（実データ置き場）。テストはここへ書き込んではいけない。
REPO_DATA_DIR = packagedir / "data"


def _snapshot(directory: pathlib.Path) -> dict[str, tuple[int, int]]:
    """ディレクトリ配下のファイルごとの (サイズ, mtime_ns) を取得する。

    Args:
        directory: スナップショット対象のディレクトリ。

    Returns:
        dict[str, tuple[int, int]]: ファイルパスをキー、サイズと更新時刻を値とする辞書。
    """
    if not directory.is_dir():
        return {}
    return {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path_factory, monkeypatch):
    """既定の data/ を tmp 配下へ向け、リポジトリ直下の data/ 改変を検出する。

    - ``app.services.watch_controller.DATA_DIR`` を tmp 配下へ差し替えることで、
      ``data_dir`` を明示しないコントローラ／アプリもリポジトリ直下の ``data/``
      を参照しないようにする（[低-1] 対応）。
    - テスト中にリポジトリ直下の ``data/`` が変更された場合はテストを失敗させる。

    Yields:
        pathlib.Path: 隔離された data/ ディレクトリ。
    """
    from app.services import watch_controller

    isolated = tmp_path_factory.mktemp("isolated_data")
    monkeypatch.setattr(watch_controller, "DATA_DIR", isolated)

    before = _snapshot(REPO_DATA_DIR)
    yield isolated
    after = _snapshot(REPO_DATA_DIR)
    if before != after:
        changed = sorted(
            set(before) ^ set(after) | {path for path in set(before) & set(after) if before[path] != after[path]}
        )
        pytest.fail(f"テストがリポジトリの data/ を変更しました: {changed}")
