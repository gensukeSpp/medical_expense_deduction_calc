"""Unit tests for SQLite database operations and schema migration."""

from __future__ import annotations
import json
import pytest
from pathlib import Path

from app.db import (
    get_db_connection,
    insert_receipt,
    get_receipt,
    upsert_clinic,
    get_clinic_by_name,
    get_clinic_by_id,
    get_or_create_clinic,
    get_all_clinics,
    upsert_template,
    get_all_templates_with_names,
    add_correction,
    insert_user,
)
from app.db_migrations import run_migrations

SCHEMA_PATH = "docs/schema.sql"


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Fixture to initialize a temporary DB file with schema applied."""
    db_file = tmp_path / "test_db.sqlite3"
    run_migrations(db_file, SCHEMA_PATH)
    return db_file


def test_schema_creation(temp_db: Path) -> None:
    """Verify that all required tables are successfully created by migrations."""
    conn = get_db_connection(temp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row["name"] for row in cursor.fetchall()]
    conn.close()

    assert "users" in tables
    assert "clinics" in tables
    assert "receipts" in tables
    assert "templates" in tables
    assert "corrections" in tables


def test_insert_and_get_receipt(temp_db: Path) -> None:
    """Verify inserting and retrieving a receipt with JSON structures and clinic reference."""
    receipt_id = "test-receipt-uuid-1"
    source_path = "test/path/to/image.png"
    ocr_json = {"words": [{"text": "clinic name"}]}
    normalized_json = {"clinic": "clinic name", "amount": 1000, "date": "2026-06-19"}

    # Insert clinic first to satisfy FOREIGN KEY constraint
    clinic_id = "test-clinic-uuid"
    upsert_clinic(temp_db, clinic_id, "clinic name")

    insert_receipt(
        temp_db,
        receipt_id,
        source_path,
        ocr_json,
        normalized_json,
        clinic_id,
    )

    receipt = get_receipt(temp_db, receipt_id)
    assert receipt is not None
    assert receipt["id"] == receipt_id
    assert receipt["source_path"] == source_path
    assert receipt["ocr_json"] == ocr_json
    assert receipt["normalized_json"] == normalized_json
    assert receipt["clinic_id"] == clinic_id


def test_clinic_upsert(temp_db: Path) -> None:
    """Verify upsert operations on clinics, including handling unique constraints."""
    clinic_id = "clinic-1"
    upsert_clinic(temp_db, clinic_id, "Test Clinic")

    clinic = get_clinic_by_name(temp_db, "Test Clinic")
    assert clinic is not None
    assert clinic["id"] == clinic_id
    assert clinic["name"] == "Test Clinic"

    # Conflicted name should result in update (keeping unique constraint satisfied)
    new_clinic_id = "clinic-2"
    upsert_clinic(temp_db, new_clinic_id, "Test Clinic")
    clinic = get_clinic_by_name(temp_db, "Test Clinic")
    assert clinic is not None
    assert clinic["id"] == new_clinic_id


def test_upsert_template(temp_db: Path) -> None:
    """Verify templates upsert updates the correction data successfully."""
    clinic_id = "clinic-1"
    upsert_clinic(temp_db, clinic_id, "Clinic 1")

    template_id = "tpl-1"
    coords_corrections = {"amount": {"x_offset": 10, "y_offset": 5}}
    upsert_template(temp_db, template_id, clinic_id, 1, coords_corrections)

    # Query templates directly
    with get_db_connection(temp_db) as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        assert row is not None
        assert row["clinic_id"] == clinic_id
        assert row["version"] == 1
        assert json.loads(row["coords_corrections"]) == coords_corrections

    # Update templated config
    coords_corrections_updated = {"amount": {"x_offset": 15, "y_offset": -2}}
    upsert_template(temp_db, template_id, clinic_id, 2, coords_corrections_updated)

    with get_db_connection(temp_db) as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        assert row is not None
        assert row["version"] == 2
        assert json.loads(row["coords_corrections"]) == coords_corrections_updated


def test_add_correction(temp_db: Path) -> None:
    """Verify add_correction logs correction AND updates receipt's normalized_json in transaction."""
    clinic_id = "clinic-1"
    upsert_clinic(temp_db, clinic_id, "Clinic 1")

    user_id = "user-1"
    insert_user(temp_db, user_id, "Alice")

    receipt_id = "receipt-1"
    normalized_json = {"amount": 1000, "date": "2026-06-18"}
    insert_receipt(temp_db, receipt_id, "img.png", {}, normalized_json, clinic_id)

    add_correction(
        db_path=temp_db,
        receipt_id=receipt_id,
        field_name="amount",
        old_value="1000",
        new_value="1500",
        user_id=user_id,
    )

    # Check correction insertion
    with get_db_connection(temp_db) as conn:
        # Since correction_id is generated internally, get the last inserted correction
        row = conn.execute("SELECT * FROM corrections ORDER BY rowid DESC LIMIT 1").fetchone()
        assert row is not None
        assert row["receipt_id"] == receipt_id
        assert row["field_name"] == "amount"
        assert row["old_value"] == "1000"
        assert row["new_value"] == "1500"
        assert row["user_id"] == user_id

    # Check receipt update (amount must be changed to '1500')
    receipt = get_receipt(temp_db, receipt_id)
    assert receipt is not None
    assert receipt["normalized_json"]["amount"] == "1500"


def test_add_correction_nonexistent_receipt(temp_db: Path) -> None:
    """Verify add_correction raises ValueError if receipt doesn't exist."""
    with pytest.raises(ValueError, match="Receipt with ID .* does not exist"):
        add_correction(
            db_path=temp_db,
            receipt_id="nonexistent-receipt",
            field_name="amount",
            old_value="1000",
            new_value="1200",
        )


def test_add_correction_same_field_twice(temp_db: Path) -> None:
    """Verify add_correction works correctly for sequential corrections on the same field."""
    clinic_id = "clinic-twice"
    upsert_clinic(temp_db, clinic_id, "Test Clinic Twice")

    receipt_id = "receipt-twice"
    normalized_json = {"amount": 1000, "date": "2026-06-18"}
    insert_receipt(temp_db, receipt_id, "img.png", {}, normalized_json, clinic_id)

    # First correction: amount 1000 -> 2000
    add_correction(
        db_path=temp_db,
        receipt_id=receipt_id,
        field_name="amount",
        old_value="1000",
        new_value="2000",
    )

    # Verify first correction
    conn = get_db_connection(temp_db)
    try:
        row = conn.execute("SELECT * FROM corrections ORDER BY rowid DESC LIMIT 1").fetchone()
        assert row["field_name"] == "amount"
        assert row["old_value"] == "1000"
        assert row["new_value"] == "2000"
    finally:
        conn.close()

    receipt = get_receipt(temp_db, receipt_id)
    assert receipt is not None
    assert receipt["normalized_json"]["amount"] == "2000"

    # Second correction: amount 2000 -> 3000 (same field, sequential)
    add_correction(
        db_path=temp_db,
        receipt_id=receipt_id,
        field_name="amount",
        old_value="2000",
        new_value="3000",
    )

    # Verify second correction
    conn = get_db_connection(temp_db)
    try:
        rows = conn.execute(
            "SELECT * FROM corrections WHERE receipt_id = ? ORDER BY rowid",
            (receipt_id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[1]["field_name"] == "amount"
        assert rows[1]["old_value"] == "2000"
        assert rows[1]["new_value"] == "3000"
    finally:
        conn.close()

    receipt = get_receipt(temp_db, receipt_id)
    assert receipt is not None
    assert receipt["normalized_json"]["amount"] == "3000"


def test_add_correction_conflict_resolved(temp_db: Path) -> None:
    """Verify add_correction resolves old_value mismatch by using current value."""
    clinic_id = "clinic-conflict"
    upsert_clinic(temp_db, clinic_id, "Test Clinic Conflict")

    receipt_id = "receipt-conflict"
    normalized_json = {"amount": 1000}
    insert_receipt(temp_db, receipt_id, "img.png", {}, normalized_json, clinic_id)

    # Correct amount from 1000 -> 2000
    add_correction(temp_db, receipt_id, "amount", "1000", "2000")

    # Now normalized_json has amount="2000"
    # Try correction with wrong old_value "1000" (should auto-resolve)
    add_correction(temp_db, receipt_id, "amount", "1000", "3000")

    receipt = get_receipt(temp_db, receipt_id)
    assert receipt is not None
    assert receipt["normalized_json"]["amount"] == "3000"


def test_get_all_clinics(temp_db: Path) -> None:
    """複数クリニック登録後、全件が取得できる"""
    upsert_clinic(temp_db, "uuid-1", "ABCクリニック")
    upsert_clinic(temp_db, "uuid-2", "あおばクリニック")
    upsert_clinic(temp_db, "uuid-3", "デンタルクリニック")

    clinics = get_all_clinics(temp_db)
    assert len(clinics) == 3
    names = [c["name"] for c in clinics]
    assert "ABCクリニック" in names
    assert "あおばクリニック" in names
    assert "デンタルクリニック" in names


def test_get_all_clinics_empty(temp_db: Path) -> None:
    """クリニック未登録時に空リストが返る"""
    clinics = get_all_clinics(temp_db)
    assert clinics == []


def test_get_all_templates_with_names(temp_db: Path) -> None:
    """テンプレートがクリニック名付きで取得できる"""
    upsert_clinic(temp_db, "uuid-1", "ABCクリニック")
    upsert_template(
        temp_db,
        "tmpl-1",
        "uuid-1",
        version=1,
        coords_corrections={"amount": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    )

    templates = get_all_templates_with_names(temp_db)
    assert len(templates) == 1
    assert templates[0]["clinic_name"] == "ABCクリニック"
    assert templates[0]["coords_corrections"] == {"amount": [[0, 0], [10, 0], [10, 10], [0, 10]]}


def test_get_all_templates_with_names_only_latest(temp_db: Path) -> None:
    """複数バージョンがある場合、最新版のみ取得される"""
    upsert_clinic(temp_db, "uuid-1", "ABCクリニック")
    upsert_template(
        temp_db,
        "tmpl-1",
        "uuid-1",
        version=1,
        coords_corrections={"amount": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    )
    upsert_template(
        temp_db,
        "tmpl-1",
        "uuid-1",
        version=2,
        coords_corrections={"amount": [[5, 5], [15, 5], [15, 15], [5, 15]]},
    )

    templates = get_all_templates_with_names(temp_db)
    assert len(templates) == 1
    assert templates[0]["version"] == 2  # 最新版のみ


def test_get_all_templates_with_names_empty(temp_db: Path) -> None:
    """テンプレート未登録時に空リストが返る"""
    templates = get_all_templates_with_names(temp_db)
    assert templates == []


class TestGetClinicById:
    def test_returns_clinic_dict(self, temp_db: Path):
        clinic_id = get_or_create_clinic(temp_db, "あおばクリニック")
        row = get_clinic_by_id(temp_db, clinic_id)
        assert row is not None
        assert row["id"] == clinic_id
        assert row["name"] == "あおばクリニック"

    def test_returns_none_for_unknown_id(self, temp_db: Path):
        assert get_clinic_by_id(temp_db, "no-such-id") is None
