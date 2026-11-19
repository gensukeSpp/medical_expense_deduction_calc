"""Tests for app.args (Issue #58 [中-4])."""

from pathlib import Path

import pytest

from app.args import is_loopback_host, validate_input_dir


def test_is_loopback_host_accepts_loopback():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")


def test_is_loopback_host_rejects_non_loopback():
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")
    assert not is_loopback_host("mypc")


def test_validate_input_dir_accepts_absolute_path_under_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    target = home / "receipts"
    assert validate_input_dir(target) == target


@pytest.mark.parametrize("path", ["relative/receipts", "/outside/receipts"])
def test_validate_input_dir_rejects_paths_outside_home(path, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    with pytest.raises(ValueError):
        validate_input_dir(path)


def test_serve_rejects_input_dir_outside_home_before_creating_directories(tmp_path, monkeypatch):
    import main
    import sys

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    outside = tmp_path / "outside"
    output = tmp_path / "output"
    processed = tmp_path / "processed"
    failed = tmp_path / "failed"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--serve",
            "--input-dir",
            str(outside),
            "--output-dir",
            str(output),
            "--processed-dir",
            str(processed),
            "--failed-dir",
            str(failed),
        ],
    )

    with pytest.raises(SystemExit, match="ホームディレクトリ配下") as exc:
        main.main()

    assert not outside.exists()
    assert not output.exists()
    assert not processed.exists()
    assert not failed.exists()
    assert str(exc.value).startswith("エラー: --input-dir")
