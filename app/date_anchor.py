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

# apply_date_anchor_normalization の戻り値理由定数（Issue #50 レビュー P2#1）。
# 成功時は (True, (offset_x, offset_y))、失敗時は (False, <REASON>) を返す。
# ANCHOR_UNRESOLVED（date 候補 0 件 / 曖昧で解決不能）のみが force 発火対象。
ANCHOR_OK = "ok"
NO_DB = "no_db"
NO_DATE_OR_CLINIC = "no_date_or_clinic"
NO_TEMPLATE = "no_template"
NO_DATE_COORDS = "no_date_coords"
NO_OCR_ENTRIES = "no_ocr_entries"
ANCHOR_UNRESOLVED = "anchor_unresolved"
NOT_NORMALIZED = "not_normalized"


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
    template_coords: Optional[Dict[str, Any]] = None,
    proximity_threshold: float = 50.0,
) -> Optional[List[List[int]]]:
    """Resolve the date anchor box, or None if not reliably unique.

    Selection logic (respects existing text-match then proximity priority):
    1. If no template date box is present (unlearned), return None — fallback
       to the legacy topmost/leftmost normalization.
    2. If exactly one OCR text candidate matches the structured date, use it
       regardless of template basis.
    3. Multiple candidates are disambiguated by basis:
       - 'topmost': the candidate whose box center is the only one within
         ``proximity_threshold`` of the template date box center is adopted.
       - 'date': the template date box sits at the origin, so center-proximity
         is meaningless; the candidate is chosen by layout consistency — for
         each candidate as a hypothetical anchor, the date-basis template's
         non-date field boxes are projected onto the raw (field center +
         candidate top-left) and scored by distance to the nearest raw box.
         The candidate with the best (fewest out-of-threshold fields, then
         min distance sum) score is adopted. Ties fall back to None.
    4. Otherwise (no match, unresolved ambiguity) return None.

    Args:
        ocr_entries: OCR entries list.
        structured_date: Structured date value.
        template_date_box: Optional template date coordinates (None = unlearned).
        template_basis: 'topmost' or 'date' (the template's coordinate basis).
        template_coords: Full coords_corrections of the template. Required to
            disambiguate multiple candidates in 'date' basis (Issue #42 [中-1]).
        proximity_threshold: Max center distance (px) to accept a candidate.

    Returns:
        The adopted anchor box (4-point polygon) or None.
    """
    from app.coord_search import _calculate_box_center

    candidates = find_date_candidates(ocr_entries, structured_date)
    if not candidates or template_date_box is None:
        return None

    if len(candidates) == 1:
        return candidates[0].get("box")

    # Multiple candidates: disambiguation depends on the template basis.
    if template_basis == "topmost":
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
        return within[0] if len(within) == 1 else None

    # 'date' basis: use layout consistency against the full template coords.
    return _resolve_date_basis_candidate(
        ocr_entries,
        candidates,
        template_coords,
        proximity_threshold=proximity_threshold,
    )


def _top_left_box(box: List[List[int]]) -> Optional[tuple[int, int]]:
    """Return the (min-x, min-y) of a 4-point box, or None if invalid."""
    points = [p for p in box if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not points:
        return None
    return (min(p[0] for p in points), min(p[1] for p in points))


def _resolve_date_basis_candidate(
    ocr_entries: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    template_coords: Optional[Dict[str, Any]],
    proximity_threshold: float = 50.0,
) -> Optional[List[List[int]]]:
    """Pick the date anchor among multiple candidates in 'date' basis.

    Each candidate's top-left is treated as the would-be anchor. The date-basis
    template's non-date field boxes are projected into the raw coordinate frame
    as ``field_center + candidate_top_left``, and each is scored by its distance
    to the nearest raw box center. Candidates are ranked lexicographically by
    (number of fields beyond ``proximity_threshold``, sum of distances). The
    unique best candidate is adopted; a tie returns None.

    Args:
        ocr_entries: OCR entries (raw coordinate frame).
        candidates: Date candidate OCR entries.
        template_coords: The clinic template's full coords_corrections ('date' basis).
        proximity_threshold: Max center distance (px) to count a field as matched.

    Returns:
        The adopted anchor box, or None when undecidable.
    """
    from app.coord_search import _calculate_box_center

    if not template_coords:
        return None

    raw_centers: List[tuple[float, float]] = []
    for entry in ocr_entries:
        box = entry.get("box")
        center = _calculate_box_center(box) if box else None
        if center is not None:
            raw_centers.append(center)
    if not raw_centers:
        return None

    field_centers: Dict[str, tuple[float, float]] = {}
    for field, box in template_coords.items():
        if field == "date" or box is None:
            continue
        center = _calculate_box_center(box)
        if center is not None:
            field_centers[field] = center
    if not field_centers:
        return None

    ranked: List[tuple[tuple[int, float], List[List[int]]]] = []
    for candidate in candidates:
        box = candidate.get("box")
        top_left = _top_left_box(box) if box else None
        if top_left is None or box is None:
            continue
        outside = 0
        total_dist = 0.0
        for center in field_centers.values():
            projected = (center[0] + top_left[0], center[1] + top_left[1])
            nearest = min(((rc[0] - projected[0]) ** 2 + (rc[1] - projected[1]) ** 2) ** 0.5 for rc in raw_centers)
            total_dist += nearest
            if nearest > proximity_threshold:
                outside += 1
        ranked.append(((outside, round(total_dist, 3)), box))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    if len(ranked) >= 2 and ranked[0][0] == ranked[1][0]:
        return None  # tying candidates → safe fallback
    return ranked[0][1]


def apply_date_anchor_normalization(
    raw_path: Path,
    structured: Dict[str, Any],
    db_path: Optional[str | Path],
    proximity_threshold: float = 50.0,
    output_dir: Optional[Path | str] = None,
) -> tuple[bool, str | tuple[int, int]]:
    """Re-normalize a raw_data file to date-anchor basis using a learned template.

    High-level orchestration used by both the image-processing and watcher paths:
    1. Resolve the date anchor box from the structured date + clinic template.
    2. If a reliable anchor is found, re-normalize ``raw_path`` relative to it
       and (if the template is still in 'topmost' basis) migrate the template to
       'date' basis, preserving the old coords in ``template_history``.
    3. When ``output_dir`` is set, re-apply coordinate-based field extraction to
       the (now date-basis) raw and rewrite ``*-structured_data.json``, so the
       date-basis correction genuinely affects extracted fields (Issue #42 [中-1]).

    Returns the applied (offset_x, offset_y) on success, or None if the anchor
    could not be reliably resolved (caller keeps the legacy normalization).

    Args:
        raw_path: Path to the raw_data.json (already topmost-normalized).
        structured: Structured data with 'date' and 'clinic' (mutated in place
            with coordinate-derived field values when ``output_dir`` is set).
        db_path: Path to the SQLite database.
        proximity_threshold: Max center distance (px) for anchor disambiguation.
        output_dir: Optional output directory to (re)write the structured_data.json
            with coordinate-extracted field values.

    Returns:
        Tuple of (ok, detail):
        - (True, (offset_x, offset_y)) when re-normalized successfully.
        - (False, <REASON>) when the anchor could not be applied (caller keeps
          the legacy normalization). REASON is one of the module constants:
          NO_DB / NO_DATE_OR_CLINIC / NO_TEMPLATE / NO_DATE_COORDS /
          NO_OCR_ENTRIES / ANCHOR_UNRESOLVED / NOT_NORMALIZED.
          Only ANCHOR_UNRESOLVED (zero or ambiguous date candidates) is a
          force-extraction target (Issue #50).
    """
    raw_path = Path(raw_path)

    if not db_path:
        return (False, NO_DB)

    structured_date = structured.get("date")
    clinic_name = structured.get("clinic")
    if not structured_date or not clinic_name:
        return (False, NO_DATE_OR_CLINIC)

    from app.db import get_latest_template_by_clinic, get_or_create_clinic

    clinic_id = get_or_create_clinic(db_path, str(clinic_name).strip())
    template = get_latest_template_by_clinic(db_path, clinic_id)
    if not template:
        return (False, NO_TEMPLATE)

    coords = template.get("coords_corrections") or {}
    template_date_box = coords.get("date")
    if template_date_box is None:
        return (False, NO_DATE_COORDS)  # date 未学習 → 従来方式

    from app.input import read_json
    from app.coord_normalizer import normalize_coordinates_by_anchor

    ocr_entries = _to_ocr_entries(read_json(raw_path))
    if not ocr_entries:
        return (False, NO_OCR_ENTRIES)
    template_basis = template.get("coord_basis") or "topmost"
    anchor_box = resolve_date_anchor(
        ocr_entries,
        structured_date,
        template_date_box,
        template_basis=template_basis,
        template_coords=coords,
        proximity_threshold=proximity_threshold,
    )
    if anchor_box is None:
        return (False, ANCHOR_UNRESOLVED)

    result = normalize_coordinates_by_anchor(raw_path, anchor_box)
    if not result.get("normalized"):
        return (False, NOT_NORMALIZED)

    offset_x = result["offset_x"]
    offset_y = result["offset_y"]

    # 一度だけ topmost → date 基準へ移行（既に date なら no-op）
    date_basis_coords = coords
    if template_basis == "topmost":
        from app.db import update_template_basis as _utb
        from app.coord_normalizer import shift_template_to_date_basis

        # template は topmost 基準で学習されているため、date 基準へ変換するには
        # template 自身の date box 位置（topmost 座標系）でシフトする（Issue #39-4）。
        new_coords = shift_template_to_date_basis(coords)
        if new_coords is None:
            return (False, NO_DATE_COORDS)  # date 未学習 → 従来方式
        _utb(db_path, clinic_id, new_coords, changed_fields=list(coords.keys()))
        date_basis_coords = new_coords
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

    # 手動切替後（date 基準 template）: 座標ベースのフィールド抽出を適用して
    # structured（と structured_data.json）へ反映する（Issue #42 [中-1]）。
    # レシートは insert しない。値は座標抽出優先で上書きする（topmost テンプレートの
    # 前方補正と同挙動）。
    if output_dir is not None:
        _apply_coordinate_extraction(
            output_dir,
            raw_path,
            structured,
            date_basis_coords,
            proximity_threshold=proximity_threshold,
        )

    return (True, (offset_x, offset_y))


def apply_date_basis_force(
    raw_path: Path,
    structured: Dict[str, Any],
    db_path: Optional[str | Path],
    proximity_threshold: float = 50.0,
    output_dir: Optional[Path | str] = None,
) -> bool:
    """Force date-basis coordinate extraction without resolving the date anchor (Issue #50).

    When ``apply_date_anchor_normalization()`` cannot resolve the date anchor
    (e.g. zero date candidates), this fallback applies the clinic's date-basis
    template ``coords_corrections`` directly with ``search_fields_by_proximity``
    to extract name / amount from the raw OCR boxes.

    Gate (Issue #50 acceptance 3): runs only when
    - ``db_path`` is provided,
    - ``structured['clinic']`` is non-empty (clinic name extracted),
    - the latest template has ``coord_basis == 'date'``,
    - and ``coords_corrections['date']`` is learned.

    No re-normalization happens; the raw stays in its original basis. Fields
    are only taken from boxes found within ``proximity_threshold`` of the
    template coords (fields with no nearby box are never candidates). A field
    that already has a non-empty structured value is preserved — the extraction
    only fills empty/None fields (Issue #50 review P1#2: a shifted frame must
    not overwrite an existing value with a wrong one). The structured dict is
    updated in place and, when ``output_dir`` is given,
    ``*-structured_data.json`` is rewritten via OutputWriter. The DB receipt's
    stored ocr_json is synced via ``update_receipt_ocr_json_by_source`` (no
    insert — no duplicate receipts).

    Returns:
        True when at least one structured field was updated, False otherwise
        (gate not passed, nothing extracted, or nothing changed).
    """
    raw_path = Path(raw_path)

    if not db_path:
        return False
    clinic_name = (structured.get("clinic") or "").strip()
    if not clinic_name:
        return False

    from app.db import get_latest_template_by_clinic, get_or_create_clinic

    clinic_id = get_or_create_clinic(db_path, clinic_name)
    template = get_latest_template_by_clinic(db_path, clinic_id)
    if not template:
        return False
    if (template.get("coord_basis") or "topmost") != "date":
        return False
    coords = template.get("coords_corrections") or {}
    if coords.get("date") is None:
        return False  # date 未学習 → force 対象外（#40 ガードと整合）

    from app.coord_search import search_fields_by_proximity
    from app.input import read_json

    entries = _to_ocr_entries(read_json(raw_path))
    if not entries:
        return False

    proximity = search_fields_by_proximity(entries, coords, threshold=proximity_threshold)
    changed = False
    for field_name, text in proximity.items():
        if field_name == "date":
            continue
        if not text:
            continue
        existing = structured.get(field_name)
        if existing and existing != text:
            # 既存の非空値は保守（位置ずれ時の誤上書き防止 / Issue #50 レビュー P1#2）
            continue
        if existing != text:
            structured[field_name] = text
            changed = True
    if not changed:
        return False

    if output_dir is not None:
        from app.structural_parser import OutputWriter

        OutputWriter().write(Path(output_dir), raw_path, structured)

    from app.db import update_receipt_ocr_json_by_source

    update_receipt_ocr_json_by_source(db_path, str(raw_path), entries)
    return True


def _apply_coordinate_extraction(
    output_dir: Path | str,
    raw_path: Path,
    structured: Dict[str, Any],
    template_coords: Dict[str, Any],
    proximity_threshold: float = 50.0,
) -> None:
    """Apply date-basis coordinate extraction to a re-normalized raw and update structured.

    Runs ``search_fields_by_proximity`` over the (date-basis) raw using the
    template's date-basis coords, overwrites non-empty field values into
    ``structured``, and rewrites the ``*-structured_data.json`` via OutputWriter.
    Is a no-op when nothing changed.
    """
    from app.coord_search import search_fields_by_proximity
    from app.structural_parser import OutputWriter
    from app.input import read_json

    entries = _to_ocr_entries(read_json(raw_path))
    if not entries:
        return
    proximity = search_fields_by_proximity(entries, template_coords, threshold=proximity_threshold)
    changed = False
    for field_name, text in proximity.items():
        if field_name == "date":
            continue
        if text and structured.get(field_name) != text:
            structured[field_name] = text
            changed = True
    if changed:
        OutputWriter().write(output_dir, raw_path, structured)


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
