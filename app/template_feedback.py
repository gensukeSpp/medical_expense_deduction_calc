"""Template feedback: update clinic template coordinates based on user corrections."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.db import (
    get_db_connection,
    get_latest_template_by_clinic,
    insert_template_history,
    upsert_template,
)


def process_correction_feedback(
    db_path: str | Path,
    clinic_id: str,
    field_coords_map: Dict[str, Optional[List[List[int]] | List[List[List[int]]]]],
    receipt_id: str | None = None,
) -> Dict[str, Any]:
    """Update clinic template coordinates based on user correction feedback.

    For each field in field_coords_map that has valid box coordinates,
    the function updates the clinic's template coords_corrections.
    Before updating, the current state is saved to template_history.

    Args:
        db_path: Path to the SQLite database.
        clinic_id: The clinic ID whose template should be updated.
        field_coords_map: Mapping of field names to box coordinates.
            - None: coordinate not found for this field.
            - List[List[int]]: single 4-point polygon (backward compatible).
            - List[List[List[int]]]: multi-box for split fields (e.g., split name).
        receipt_id: Optional receipt ID associated with the correction.

    Returns:
        Dict with keys:
            - updated_fields: list of field names that were updated
            - not_found_fields: list of field names where coordinates were None
            - history_id: the UUID of the created history record, or None
    """
    updated_fields: List[str] = []
    not_found_fields: List[str] = []

    # Collect fields with valid coordinates
    for field_name, box in field_coords_map.items():
        if box is not None:
            updated_fields.append(field_name)
        else:
            not_found_fields.append(field_name)

    if not updated_fields:
        return {
            "updated_fields": [],
            "not_found_fields": not_found_fields,
            "history_id": None,
        }

    # Get current template or prepare for new one
    existing = get_latest_template_by_clinic(db_path, clinic_id)
    if existing:
        current_coords: Dict[str, Any] = existing.get("coords_corrections") or {}
        template_id: str = existing["id"]
        new_version: int = existing["version"] + 1
    else:
        current_coords = {}
        template_id = str(uuid.uuid4())
        new_version = 1

    # Update only the fields that have coordinates
    updated_coords = dict(current_coords)
    for field_name in updated_fields:
        updated_coords[field_name] = field_coords_map[field_name]

    # 修正座標はレシート ocr_json（topmost 基準）由来。既存 basis が 'date' なら
    # 整合のため 'topmost' へ戻す（Issue #51）。
    existing_basis = (existing or {}).get("coord_basis")
    coord_basis = "topmost" if existing_basis == "date" else existing_basis

    # Upsert the template first to ensure the row exists for FK constraint
    upsert_template(
        db_path=db_path,
        template_id=template_id,
        clinic_id=clinic_id,
        version=new_version,
        coords_corrections=updated_coords,
        coord_basis=coord_basis,
    )

    # Save history snapshot after template upsert (FK safety)
    history_id = str(uuid.uuid4())
    changed_fields_str = json.dumps(updated_fields, ensure_ascii=False)
    insert_template_history(
        db_path=db_path,
        history_id=history_id,
        template_id=template_id,
        clinic_id=clinic_id,
        version=existing["version"] if existing else 0,
        coords_corrections=current_coords if current_coords else None,
        changed_fields=changed_fields_str,
        change_reason="user_correction",
        receipt_id=receipt_id,
    )

    return {
        "updated_fields": updated_fields,
        "not_found_fields": not_found_fields,
        "history_id": history_id,
    }
