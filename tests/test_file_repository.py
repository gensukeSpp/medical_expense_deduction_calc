"""Tests for app/services/file_repository.py — FileRepository class."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services.file_repository import FileRepository


@pytest.fixture
def repo(tmp_path: Path) -> FileRepository:
    return FileRepository(
        output_dir=tmp_path / "output",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
    )


class TestIsImageFile:
    def test_accepts_jpg(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "test.jpg"
        p.write_text("dummy")
        assert repo.is_image_file(p) is True

    def test_accepts_png(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "test.png"
        p.write_text("dummy")
        assert repo.is_image_file(p) is True

    def test_rejects_non_image(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "test.txt"
        p.write_text("dummy")
        assert repo.is_image_file(p) is False

    def test_rejects_resized_suffix(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "test_resized.jpg"
        p.write_text("dummy")
        assert repo.is_image_file(p) is False

    def test_rejects_temp_prefix(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "temp_scan.jpg"
        p.write_text("dummy")
        assert repo.is_image_file(p) is False

    def test_rejects_directory(self, repo: FileRepository, tmp_path: Path):
        d = tmp_path / "not_a_file.jpg"
        d.mkdir()
        assert repo.is_image_file(d) is False


class TestIsFileStable:
    def test_returns_true_for_stable_file(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "stable.jpg"
        p.write_text("content")
        assert repo.is_file_stable(p, interval=0.05, checks=2) is True

    def test_returns_false_for_nonexistent(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "nonexistent.jpg"
        assert repo.is_file_stable(p) is False

    def test_returns_false_when_file_grows(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "growing.jpg"
        p.write_text("a")

        import threading

        def grow():
            time.sleep(0.15)
            p.write_text("ab")

        t = threading.Thread(target=grow, daemon=True)
        t.start()
        result = repo.is_file_stable(p, interval=0.1, checks=3)
        assert result is False


class TestMoveToFailed:
    def test_moves_file_to_failed_dir(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "fail.jpg"
        p.write_text("data")
        repo.move_to_failed(p)
        assert (repo.failed_dir / "fail.jpg").exists()
        # original should be removed from input (move, not copy)
        assert not p.exists()

    def test_handles_duplicate_name(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "fail.jpg"
        p.write_text("data")
        # create a file with same name in failed dir
        (repo.failed_dir / "fail.jpg").write_text("old")
        repo.move_to_failed(p)
        # should create a timestamped variant
        failed_files = list(repo.failed_dir.glob("*_fail.jpg"))
        assert len(failed_files) == 1


class TestCleanupFile:
    def test_removes_existing_file(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "temp.txt"
        p.write_text("temp")
        repo.cleanup_file(p)
        assert not p.exists()

    def test_does_not_raise_for_missing_file(self, repo: FileRepository, tmp_path: Path):
        p = tmp_path / "missing.txt"
        # should not raise
        repo.cleanup_file(p)


class TestDirectoryCreation:
    def test_creates_directories_on_init(self, tmp_path: Path):
        repo = FileRepository(
            output_dir=tmp_path / "a" / "output",
            processed_dir=tmp_path / "b" / "processed",
            failed_dir=tmp_path / "c" / "failed",
        )
        assert repo.output_dir.exists()
        assert repo.processed_dir.exists()
        assert repo.failed_dir.exists()
