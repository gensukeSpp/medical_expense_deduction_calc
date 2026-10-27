"""Date anchor resolution for date-based OCR coordinate normalization.

This module resolves the structured date extracted from a receipt to the
specific OCR box that produced it, using the clinic's template date
coordinates as a positional hint. When a reliable unique anchor box is
found, the OCR coordinates can be re-normalized relative to that box's
top-left corner rather than the global topmost/leftmost point.

The normalizer shift logic lives in app.coord_normalizer; this module only
performs the anchor *resolution* (candidate matching + ambiguity checks).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.normalization import parse_date

logger = logging.getLogger(__name__)


def _is_valid_calendar_date(iso_date: Optional[str]) -> bool:
    """Return True if ``iso_date`` is a real calendar date (YYYY-MM-DD).

    parse_date() only validates digit ranges loosely (e.g. 2026-99-99 passes),
    so we confirm the value is an actually-existing date before trusting it as
    an anchor (Issue #39: only reliable candidates may anchor).
    """
    if not iso_date:
        return False
    try:
        date.fromisoformat(iso_date)
        return True
    except ValueError:
        return False


def find_date_candidates(
    ocr_entries: List[Dict[str, Any]],
    structured_date: str,
) -> List[Dict[str, Any]]:
    """Return OCR entries whose parsed text equals the structured date.

    Both the OCR text and the structured date are normalized through
    parse_date() so that format variants ('2026/01/15' vs '2026年1月15日')
    compare equal.

    Args:
        ocr_entries: List of OCR entry dicts with 'text', 'box' keys.
        structured_date: The structured date value (any parse_date-supported form).

    Returns:
        List of OCR entries (with valid box) matching the date, or [].
    """
    if not structured_date:
        return []
    target = parse_date(structured_date)
    if target is None or not _is_valid_calendar_date(target):
        return []

    candidates = []
    for entry in ocr_entries:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        box = entry.get("box")
        if not text or not box:
            continue
        parsed = parse_date(text)
        if parsed is None or not _is_valid_calendar_date(parsed):
            continue
        if parsed == target:
            candidates.append(entry)
    return candidates


def resolve_date_anchor(
    ocr_entries: List[Dict[str, Any]],
    structured_date: str,
    template_date_box: Optional[List[List[int]]] = None,
    template_basis: str = "topmost",
    proximity_threshold: float = 50.0,
) -> Optional[List[List[int]]]:
    """Resolve the date anchor box, or None if not reliably unique.

    Selection logic (respects existing text-match then proximity priority):
    1. If no template date box is present (unlearned), return None — fallback
       to the legacy topmost/leftmost normalization.
    2. If exactly one OCR text candidate matches the structured date, use it
       regardless of template basis.
    3. If multiple candidates, a unique candidate can only be selected when
       the template is in 'topmost' basis (the basis the raw coordinates were
       normalized into). The candidate whose box center is the only one within
       ``proximity_threshold`` of the template date box center is adopted.
    4. Otherwise (no match, ambiguous, date-basis template with duplicates)
       return None.

    Args:
        ocr_entries: OCR entries list.
        structured_date: Structured date value.
        template_date_box: Optional template date coordinates (None = unlearned).
        template_basis: 'topmost' or 'date' (the template's coordinate basis).
        proximity_threshold: Max center distance (px) to accept a unique candidate.

    Returns:
        The adopted anchor box (4-point polygon) or None.
    """
    from app.coord_search import _calculate_box_center

    candidates = find_date_candidates(ocr_entries, structured_date)
    if not candidates or template_date_box is None:
        return None

    if len(candidates) == 1:
        return candidates[0].get("box")

    # Multiple candidates: only disambiguate when template is still in the
    # same basis the raw data is normalized into (topmost).
    if template_basis != "topmost":
        return None

    target_center = _calculate_box_center(template_date_box)
    if target_center is None:
        return None

    scored = []
    for candidate in candidates:
        box = candidate.get("box")
        if not box:
            continue
        center = _calculate_box_center(box)
        if center is None:
            continue
        distance = ((center[0] - target_center[0]) ** 2 + (center[1] - target_center[1]) ** 2) ** 0.5
        scored.append((distance, box))

    within = [box for distance, box in scored if distance <= proximity_threshold]
    if len(within) == 1:
        return within[0]
    return None


def apply_date_anchor_normalization(
    raw_path: Path,
    structured: Dict[str, Any],
    db_path: Optional[str | Path],
    proximity_threshold: float = 50.0,
) -> Optional[tuple[int, int]]:
    """Re-normalize a raw_data file to date-anchor basis using a learned template.

    High-level orchestration used by both the image-processing and watcher paths:
    1. Resolve the date anchor box from the structured date + clinic template.
    2. If a reliable anchor is found, re-normalize ``raw_path`` relative to it
       and (if the template is still in 'topmost' basis) migrate the template to
       'date' basis, preserving the old coords in ``template_history``.

    Returns the applied (offset_x, offset_y) on success, or None if the anchor
    could not be reliably resolved (caller keeps the legacy normalization).

    Args:
        raw_path: Path to the raw_data.json (already topmost-normalized).
        structured: Structured data with 'date' and 'clinic'.
        db_path: Path to the SQLite database.
        proximity_threshold: Max center distance (px) for anchor disambiguation.

    Returns:
        (offset_x, offset_y) if re-normalized, else None.
    """
    raw_path = Path(raw_path)

    if not db_path:
        return None

    structured_date = structured.get("date")
    clinic_name = structured.get("clinic")
    if not structured_date or not clinic_name:
        return None

    from app.db import get_latest_template_by_clinic, get_or_create_clinic

    clinic_id = get_or_create_clinic(db_path, str(clinic_name).strip())
    template = get_latest_template_by_clinic(db_path, clinic_id)
    if not template:
        return None

    coords = template.get("coords_corrections") or {}
    template_date_box = coords.get("date")
    if template_date_box is None:
        return None  # date 未学習 → 従来方式

    from app.input import read_json
    from app.coord_normalizer import normalize_coordinates_by_anchor

    ocr_entries = _to_ocr_entries(read_json(raw_path))
    if not ocr_entries:
        return None
    template_basis = template.get("coord_basis") or "topmost"
    anchor_box = resolve_date_anchor(
        ocr_entries,
        structured_date,
        template_date_box,
        template_basis=template_basis,
        proximity_threshold=proximity_threshold,
    )
    if anchor_box is None:
        return None

    result = normalize_coordinates_by_anchor(raw_path, anchor_box)
    if not result.get("normalized"):
        return None

    offset_x = result["offset_x"]
    offset_y = result["offset_y"]

    # 一度だけ topmost → date 基準へ移行（既に date なら no-op）
    if template_basis == "topmost":
        from app.db import update_template_basis as _utb
        from app.coord_normalizer import shift_template_to_date_basis

        # template は topmost 基準で学習されているため、date 基準へ変換するには
        # template 自身の date box 位置（topmost 座標系）でシフトする（Issue #39-4）。
        new_coords = shift_template_to_date_basis(coords)
        if new_coords is None:
            return None  # date 未学習 → 従来方式
        _utb(db_path, clinic_id, new_coords, changed_fields=list(coords.keys()))
        logger.info(
            "Migrated template for clinic %s to date basis (raw_offset=(%d, %d))",
            clinic_id,
            offset_x,
            offset_y,
        )
    else:
        logger.info(
            "Template already in date basis; re-normalized raw to date anchor (offset=(%d, %d))",
            offset_x,
            offset_y,
        )

    # 受け入れ条件 6: DB に保存する OCR box を新基準で更新する（二重登録はしない）
    from app.db import update_receipt_ocr_json_by_source

    updated_entries = _to_ocr_entries(read_json(raw_path))
    if updated_entries:
        update_receipt_ocr_json_by_source(db_path, str(raw_path), updated_entries)

    return (offset_x, offset_y)


def _to_ocr_entries(ocr_json: Any) -> List[Dict[str, Any]]:
    """Normalize various raw_data formats into a list of OCR entry dicts."""
    if isinstance(ocr_json, list):
        return [e for e in ocr_json if isinstance(e, dict)]
    if isinstance(ocr_json, dict):
        words = ocr_json.get("words")
        if isinstance(words, list):
            return [e for e in words if isinstance(e, dict)]
        entries = ocr_json.get("ocr_entries") or ocr_json.get("ocr_json") or ocr_json.get("data")
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)]
    return []
