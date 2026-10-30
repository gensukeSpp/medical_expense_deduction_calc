"""Tests for schema migrations and template coordinate-basis switching (Issue #39)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.db import (
    get_db_connection,
    upsert_clinic,
    upsert_template,
    get_latest_template_by_clinic,
    update_template_basis,
    update_receipt_ocr_json_by_source,
    insert_receipt,
)
from app.db_migrations import run_migrations, _add_coord_basis_column_if_missing

SCHEMA_PATH = Path("docs/schema.sql")


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_db.sqlite3"
    run_migrations(db_file, SCHEMA_PATH)
    return db_file


# ------------------------------------------------------------------
# Migration / schema
# ------------------------------------------------------------------


class TestCoordBasisMigration:
    def test_schema_has_coord_basis_column(self, temp_db: Path):
        conn = get_db_connection(temp_db)
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(templates)")}
        finally:
            conn.close()
        assert "coord_basis" in cols

    def test_new_template_defaults_to_topmost(self, temp_db: Path):
        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "テスト病院")
        upsert_template(temp_db, str(uuid.uuid4()), clinic_id, version=1, coords_corrections={})

        template = get_latest_template_by_clinic(temp_db, clinic_id)
        assert template["coord_basis"] == "topmost"

    def test_migration_adds_column_to_legacy_db(self, tmp_path: Path):
        """A DB without coord_basis (simulating pre-Issue#39) gets migrated idempotently."""
        db_file = tmp_path / "legacy.sqlite3"
        conn = sqlite3.connect(str(db_file))
        try:
            conn.execute("""
                CREATE TABLE templates (
                    id TEXT PRIMARY KEY,
                    clinic_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    coords_corrections TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """)
            conn.commit()
        finally:
            conn.close()

        # run_migrations should add coord_basis to the existing templates table.
        run_migrations(db_file, SCHEMA_PATH)
        conn = get_db_connection(db_file)
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(templates)")}
        finally:
            conn.close()
        assert "coord_basis" in cols

    def test_migration_is_idempotent(self, tmp_path: Path):
        db_file = tmp_path / "legacy.sqlite3"
        conn = sqlite3.connect(str(db_file))
        try:
            conn.execute("""
                CREATE TABLE templates (
                    id TEXT PRIMARY KEY,
                    clinic_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    coords_corrections TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """)
            conn.commit()
        finally:
            conn.close()

        run_migrations(db_file, SCHEMA_PATH)
        run_migrations(db_file, SCHEMA_PATH)  # second run must be a no-op
        # No exception raised => idempotent.

    def test_add_coord_basis_column_helper_not_duplicated(self, tmp_path: Path):
        db_file = tmp_path / "db.sqlite3"
        run_migrations(db_file, SCHEMA_PATH)
        conn = get_db_connection(db_file)
        try:
            _add_coord_basis_column_if_missing(conn)
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(templates)")]
        finally:
            conn.close()
        assert cols.count("coord_basis") == 1

    def test_concurrent_initialization_no_race(self, tmp_path: Path):
        """[P2] 複数プロセスが同時初期化しても duplicate column エラーにならない。

        BEGIN EXCLUSIVE TRANSACTION により write lock を取得してから列有無を確認するため、
        並行して走る run_migrations は直列化され、2つ目の ALTER TABLE が失敗しない。
        """
        import threading

        db_file = tmp_path / "concurrent.sqlite3"
        errors: list = []

        def init():
            try:
                run_migrations(db_file, SCHEMA_PATH)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=init) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent migration raised: {errors}"

        # 最終状態で coord_basis 列は1つだけ存在する
        conn = get_db_connection(db_file)
        try:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(templates)")]
        finally:
            conn.close()
        assert cols.count("coord_basis") == 1


# ------------------------------------------------------------------
# update_template_basis
# ------------------------------------------------------------------


class TestUpdateTemplateBasis:
    def test_migrates_template_to_date_basis(self, temp_db: Path):
        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "クリニックA")
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections={"date": [[50, 100], [150, 100], [150, 130], [50, 130]]},
        )

        new_coords = {"date": [[0, 0], [100, 0], [100, 30], [0, 30]]}
        migrated = update_template_basis(temp_db, clinic_id, new_coords, changed_fields=["date"])

        assert migrated is True
        template = get_latest_template_by_clinic(temp_db, clinic_id)
        assert template["coord_basis"] == "date"
        assert template["coords_corrections"]["date"] == new_coords["date"]

        # history snapshot preserved with old coords
        conn = get_db_connection(temp_db)
        try:
            row = conn.execute(
                "SELECT coords_corrections, change_reason FROM template_history WHERE clinic_id = ?",
                (clinic_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["change_reason"] == "basis_migration"
        assert json.loads(row["coords_corrections"]) == {"date": [[50, 100], [150, 100], [150, 130], [50, 130]]}

    def test_noop_when_already_date_basis(self, temp_db: Path):
        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "クリニックB")
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections={"date": [[0, 0], [100, 0], [100, 30], [0, 30]]},
        )
        # Simulate already-migrated state.
        conn = get_db_connection(temp_db)
        try:
            conn.execute("UPDATE templates SET coord_basis = 'date'")
            conn.commit()
        finally:
            conn.close()

        migrated = update_template_basis(temp_db, clinic_id, {"date": [[5, 5], [105, 5], [105, 35], [5, 35]]})
        assert migrated is False

        template = get_latest_template_by_clinic(temp_db, clinic_id)
        assert template["coord_basis"] == "date"
        # coords unchanged (no double conversion)
        assert template["coords_corrections"]["date"] == [[0, 0], [100, 0], [100, 30], [0, 30]]

    def test_noop_when_no_template(self, temp_db: Path):
        assert update_template_basis(temp_db, "missing-clinic", {"date": []}) is False


# ------------------------------------------------------------------
# update_receipt_ocr_json_by_source
# ------------------------------------------------------------------


class TestUpdateReceiptOcrJsonBySource:
    def test_updates_most_recent_matching_receipt(self, temp_db: Path):
        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "クリニックC")
        insert_receipt(
            temp_db,
            str(uuid.uuid4()),
            "/img/a.jpg",
            {"words": [{"text": "old"}]},
            {},
            clinic_id,
        )

        updated = update_receipt_ocr_json_by_source(temp_db, "/img/a.jpg", [{"text": "new", "box": [[0, 0]]}])
        assert updated is True

        conn = get_db_connection(temp_db)
        try:
            row = conn.execute("SELECT ocr_json FROM receipts").fetchone()
        finally:
            conn.close()
        assert row is not None
        assert json.loads(row["ocr_json"]) == [{"text": "new", "box": [[0, 0]]}]

    def test_no_match_returns_false(self, temp_db: Path):
        assert update_receipt_ocr_json_by_source(temp_db, "/nonexistent.jpg", []) is False
