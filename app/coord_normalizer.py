"""Coordinate normalizer: convert absolute OCR coordinates to relative positions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def _find_min_coords(ocr_entries: List[Dict[str, Any]]) -> tuple[Optional[int], Optional[int], Optional[float]]:
    """Find the minimum x, minimum y across all boxes, and the confidence of the topmost element.

    Args:
        ocr_entries: List of OCR result dicts with keys 'text', 'confidence', 'box'.

    Returns:
        Tuple of (min_x, min_y, topmost_confidence).
        min_x/min_y are None if no valid boxes found.
        topmost_confidence is None if no valid entry found.
    """
    min_x: Optional[int] = None
    min_y: Optional[int] = None
    topmost_confidence: Optional[float] = None
    topmost_y: Optional[float] = None

    for entry in ocr_entries:
        if not isinstance(entry, dict):
            continue
        box = entry.get("box")
        if not box or not isinstance(box, list):
            continue

        """
        無効な形式の座標点（リストやタプルではない、または要素数が2未満のもの）が含まれている場合、\n
        非辞書型の要素が ocr_entries に混入した場合の防御的プログラミングも考慮し、事前に有効な座標点（valid_points）を抽出して処理する
        """
        valid_points = [p for p in box if isinstance(p, (list, tuple)) and len(p) >= 2]
        if not valid_points:
            continue

        # Find this box's min x and min y across all points
        for x, y in valid_points:
            if min_x is None or x < min_x:
                min_x = x
            if min_y is None or y < min_y:
                min_y = y

        # Track topmost element's confidence
        box_min_y = min(p[1] for p in valid_points)
        if topmost_y is None or box_min_y < topmost_y:
            topmost_y = box_min_y
            topmost_confidence = entry.get("confidence")

    return min_x, min_y, topmost_confidence


def _subtract_offset(box: List[List[int]], offset_x: int, offset_y: int) -> List[List[int]]:
    """Subtract offset from all points in a box."""
    # box 内に無効な座標点（None や空リストなど）が含まれている場合、スキップ
    return [[p[0] - offset_x, p[1] - offset_y] for p in box if isinstance(p, (list, tuple)) and len(p) >= 2]


def normalize_coordinates(raw_data_path: Path) -> Dict[str, Any]:
    """Normalize OCR coordinates to relative positions.

    Finds the topmost element (min y) and leftmost element (min x),
    then subtracts these offsets from all box coordinates.

    If the topmost element's confidence < 0.8, normalization is skipped.

    Args:
        raw_data_path: Path to the raw_data.json file.

    Returns:
        Dict with keys:
            - normalized: bool — whether normalization was performed
            - low_confidence: bool — whether topmost element had confidence < 0.8
            - topmost_confidence: float or None — confidence of the topmost element
            - offset_x: int — x offset subtracted (0 if skipped)
            - offset_y: int — y offset subtracted (0 if skipped)
    """
    import json

    if not raw_data_path.exists():
        raise FileNotFoundError(f"raw_data not found: {raw_data_path}")

    with open(raw_data_path, encoding="utf-8") as f:
        ocr_entries = json.load(f)

    if not isinstance(ocr_entries, list) or not ocr_entries:
        return {
            "normalized": False,
            "low_confidence": False,
            "topmost_confidence": None,
            "offset_x": 0,
            "offset_y": 0,
        }

    min_x, min_y, topmost_confidence = _find_min_coords(ocr_entries)

    if min_x is None or min_y is None:
        return {
            "normalized": False,
            "low_confidence": False,
            "topmost_confidence": topmost_confidence,
            "offset_x": 0,
            "offset_y": 0,
        }

    # Check confidence: treat None as low confidence (safe side)
    low_confidence = topmost_confidence is None or topmost_confidence < 0.8

    if low_confidence:
        return {
            "normalized": False,
            "low_confidence": True,
            "topmost_confidence": topmost_confidence,
            "offset_x": 0,
            "offset_y": 0,
        }

    # Normalize coordinates: subtract offset from all box points
    normalized_entries = []
    for entry in ocr_entries:
        box = entry.get("box")
        if box and isinstance(box, list):
            entry["box"] = _subtract_offset(box, min_x, min_y)
        normalized_entries.append(entry)

    # Write back atomically
    from app.output import write_json_atomic

    write_json_atomic(raw_data_path, normalized_entries)

    return {
        "normalized": True,
        "low_confidence": False,
        "topmost_confidence": topmost_confidence,
        "offset_x": min_x,
        "offset_y": min_y,
    }


def get_topmost_confidence(raw_data_path: Path, threshold: float = 0.8) -> tuple[bool, Optional[float]]:
    """読み取り専用で topmost 要素の Confidence がしきい値未満かを判定する。

    Note:
        normalize_coordinates と異なり、box 座標の書き換えは行わない。
        前処理再試行の判定（Issue #36）で使用する。

    Args:
        raw_data_path: raw_data.json のパス。
        threshold: 未満なら低 Confidence とみなすしきい値。

    Returns:
        (low_confidence, topmost_confidence) のタプル。
    """
    import json

    with open(raw_data_path, encoding="utf-8") as f:
        ocr_entries = json.load(f)

    _, _, topmost_confidence = _find_min_coords(ocr_entries)
    low_confidence = topmost_confidence is None or topmost_confidence < threshold
    return low_confidence, topmost_confidence


def normalize_coordinates_by_anchor(raw_data_path: Path, anchor_box: List[List[int]]) -> Dict[str, Any]:
    """Normalize all OCR coordinates relative to a given anchor box's top-left.

    Unlike normalize_coordinates(), which uses the global topmost/leftmost point,
    this uses the top-left (min x, min y) of ``anchor_box`` as the origin. This is
    used by date-based normalization (Issue #39): when a reliable date anchor box
    is found, all boxes are shifted so the date box's top-left becomes (0, 0).

    Args:
        raw_data_path: Path to the raw_data.json file.
        anchor_box: The 4-point polygon whose top-left becomes the new origin.

    Returns:
        Dict with keys:
            - normalized: bool — whether normalization was performed
            - low_confidence: bool — always False here (caller gates this)
            - offset_x: int — x offset subtracted
            - offset_y: int — y offset subtracted
    """
    import json

    if not raw_data_path.exists():
        raise FileNotFoundError(f"raw_data not found: {raw_data_path}")

    with open(raw_data_path, encoding="utf-8") as f:
        ocr_entries = json.load(f)

    valid_points = [p for p in anchor_box if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not valid_points:
        return {"normalized": False, "low_confidence": False, "offset_x": 0, "offset_y": 0}

    offset_x = min(p[0] for p in valid_points)
    offset_y = min(p[1] for p in valid_points)

    normalized_entries = []
    for entry in ocr_entries:
        box = entry.get("box")
        if box and isinstance(box, list):
            entry["box"] = _subtract_offset(box, offset_x, offset_y)
        normalized_entries.append(entry)

    from app.output import write_json_atomic

    write_json_atomic(raw_data_path, normalized_entries)

    return {
        "normalized": True,
        "low_confidence": False,
        "offset_x": offset_x,
        "offset_y": offset_y,
    }


def shift_template_coords(
    coords: Dict[str, Any],
    offset_x: int,
    offset_y: int,
) -> Dict[str, Any]:
    """Shift all template coordinate boxes by a given offset.

    Shifts every box in ``coords_corrections`` so the template adopts the new
    coordinate basis (e.g. topmost -> date anchor). Supports both single-box
    (4-point polygon) and multi-box (list of polygons) field values.

    Args:
        coords: Template coords_corrections dict (field -> box or list of boxes).
        offset_x: X offset subtract.
        offset_y: Y offset subtract.

    Returns:
        A new dict with all boxes shifted.
    """
    shifted: Dict[str, Any] = {}
    for field_name, box in coords.items():
        if box is None:
            shifted[field_name] = None
        elif _is_multi_box(box):
            shifted[field_name] = [_subtract_offset(sub_box, offset_x, offset_y) for sub_box in box]
        else:
            shifted[field_name] = _subtract_offset(box, offset_x, offset_y)
    return shifted


def _is_multi_box(value: Any) -> bool:
    """Check if a field value is a multi-box (list of boxes) format.

    Multi-box: [[[x1,y1],[x2,y2],...], [[x1,y1],[x2,y2],...]]
    Single-box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    """
    if not isinstance(value, list) or not value:
        return False
    if not isinstance(value[0], list) or not value[0]:
        return False
    return isinstance(value[0][0], list)


def _is_numeric_point(p: Any) -> bool:
    """Return True if ``p`` is a 2+ element list/tuple with numeric lead coords."""
    if not isinstance(p, (list, tuple)) or len(p) < 2:
        return False
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p[:2])


def shift_template_to_date_basis(coords: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Shift template coords so the template's own date box becomes the origin.

    Shared by apply_date_anchor_normalization (auto migration, Issue #39) and
    the Web admin manual toggle (Issue #42). Returns None when the date box is
    unlearned or malformed.

    Args:
        coords: Template coords_corrections dict.

    Returns:
        A new dict shifted by the date box's (min-x, min-y), or None.
    """
    date_box = coords.get("date")
    if not isinstance(date_box, (list, tuple)) or not date_box:
        return None
    valid_points = [p for p in date_box if _is_numeric_point(p)]
    if not valid_points:
        return None
    offset_x = min(p[0] for p in valid_points)
    offset_y = min(p[1] for p in valid_points)
    return shift_template_coords(coords, offset_x, offset_y)
