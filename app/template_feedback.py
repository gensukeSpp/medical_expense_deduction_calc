"""Template feedback: update clinic template coordinates based on user corrections."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.db import (
    get_db_connection,
    get_latest_template_by_clinic,
    insert_template_history,
    upsert_template,
)

logger = logging.getLogger(__name__)


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
    existing_basis = (existing or {}).get("coord_basis")

    if existing_basis == "date":
        # Issue #51: date 基準テンプレートでは、修正座標を date 基準へ変換して
        # coord_basis を date のまま維持する（topmost へ戻さない。座標全体と基底を一致させる）。
        # 修正座標の基準は修正レシート ocr_json の基準と同じ。date box top-left で減算すれば
        #   - ocr_json が date 基準（anchor 実行済み）: date box は原点 → シフトは no-op
        #   - ocr_json が topmost 基準: date box の絶対位置で減算 → date 基準へ変換
        # の 2 ケースを一括処理できる。
        shifted = _shift_correction_to_date_basis(db_path, receipt_id, field_coords_map)
        if shifted is None:
            # 修正座標の基準を安全に判別できない（date anchor 未解決）ため、date テンプレート
            # を破壊しないよう座標を書き込まない（基底も変更しない）。
            logger.warning(
                "Template for clinic %s is date-basis but date anchor is unresolvable "
                "for receipt %s; skipping coordinate feedback",
                clinic_id,
                receipt_id,
            )
            return {
                "updated_fields": [],
                "not_found_fields": not_found_fields,
                "history_id": None,
            }
        for field_name in updated_fields:
            updated_coords[field_name] = shifted[field_name]
        coord_basis = "date"
    else:
        for field_name in updated_fields:
            updated_coords[field_name] = field_coords_map[field_name]
        coord_basis = existing_basis

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


def _shift_correction_to_date_basis(
    db_path: str | Path,
    receipt_id: Optional[str],
    field_coords_map: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Shift correction coordinates into date basis using the corrected receipt's date box.

    Corrected coordinates derive from the corrected receipt's ocr_json, whose basis is
    either 'topmost' (date anchor did not run) or 'date' (date-anchor re-normalization
    rewrote it). Both cases are reconciled by subtracting the receipt's own date-box
    top-left: for a topmost ocr_json this performs the topmost -> date shift, while for a
    date-basis ocr_json the date box sits at the origin so the subtraction is a no-op.

    Args:
        db_path: Path to the SQLite database.
        receipt_id: The corrected receipt's ID (needed to read its ocr_json).
        field_coords_map: Mapping of the corrected field boxes (in the receipt's basis).

    Returns:
        The corrected boxes shifted to date basis, or None when the date anchor cannot be
        resolved (receipt missing / no ocr_json / date text absent / zero or multiple
        date candidates). The caller must not write boxes into a date-basis template
        when this returns None.
    """
    if receipt_id is None:
        return None

    from app.coord_normalizer import shift_template_coords
    from app.date_anchor import _top_left_box, find_date_candidates
    from app.db import get_receipt

    receipt = get_receipt(db_path, receipt_id)
    ocr_json = (receipt or {}).get("ocr_json")
    structured_date = ((receipt or {}).get("normalized_json") or {}).get("date")
    if not ocr_json or not structured_date:
        return None

    candidates = find_date_candidates(ocr_json, structured_date)
    if len(candidates) != 1:
        return None

    box = candidates[0].get("box")
    if not isinstance(box, list):
        return None
    top_left = _top_left_box(box)
    if top_left is None:
        return None

    return shift_template_coords(field_coords_map, top_left[0], top_left[1])
