"""Coverage-completion tests for app/date_anchor.py (PR #53 review P3).

Covers helpers (_top_left_box / _resolve_date_basis_candidate / _to_ocr_entries /
_is_valid_calendar_date / _apply_coordinate_extraction) and the
apply_date_anchor_normalization failure reasons (review P2#1) + force edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.date_anchor import (
    ANCHOR_UNRESOLVED,
    NO_DB,
    NO_DATE_COORDS,
    NO_DATE_OR_CLINIC,
    NO_OCR_ENTRIES,
    NO_TEMPLATE,
    NOT_NORMALIZED,
    _apply_coordinate_extraction,
    _is_valid_calendar_date,
    _resolve_date_basis_candidate,
    _to_ocr_entries,
    _top_left_box,
    apply_date_anchor_normalization,
    apply_date_basis_force,
    find_date_candidates,
)


def _make_db(tmp_path: Path) -> Path:
    from app.db_migrations import run_migrations

    db_path = tmp_path / "test.db"
    run_migrations(db_path, "docs/schema.sql")
    return db_path


# ------------------------------------------------------------------
# _is_valid_calendar_date
# ------------------------------------------------------------------


class TestIsValidCalendarDateExtra:
    def test_falsy_returns_false(self):
        assert _is_valid_calendar_date(None) is False
        assert _is_valid_calendar_date("") is False

    def test_invalid_returns_false(self):
        assert _is_valid_calendar_date("2026-99-99") is False
        assert _is_valid_calendar_date("2026-02-30") is False

    def test_valid_returns_true(self):
        assert _is_valid_calendar_date("2024-02-29") is True  # 2024 is a leap year


# ------------------------------------------------------------------
# find_date_candidates: 非 dict エントリ
# ------------------------------------------------------------------


class TestFindDateCandidatesNonDict:
    def test_skips_non_dict_entries(self):
        entries = ["skip", 42, {"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}]
        result = find_date_candidates(entries, "2026-01-15")
        assert len(result) == 1


# ------------------------------------------------------------------
# _top_left_box
# ------------------------------------------------------------------


class TestTopLeftBox:
    """_top_left_box: box 左上（min-x, min-y）の導出."""

    def test_returns_min_x_min_y(self):
        assert _top_left_box([[10, 20], [30, 25], [25, 40], [5, 35]]) == (5, 20)

    def test_invalid_box_returns_none(self):
        assert _top_left_box([]) is None
        assert _top_left_box(["bad", 3]) is None  # ポイントでない要素のみ → points 空  # type: ignore[list-item]

    def test_partial_points_ok(self):
        # len(p) >= 2 のポイントのみ採用
        assert _top_left_box([[10, 20], [1], [30, 40]]) == (10, 20)


# ------------------------------------------------------------------
# _resolve_date_basis_candidate
# ------------------------------------------------------------------


class TestResolveDateBasisCandidate:
    """_resolve_date_basis_candidate: date 基準の複数候補からの選択."""

    def test_no_template_coords_returns_none(self):
        entries = [{"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}]
        candidates = find_date_candidates(entries, "2026-01-15")
        assert _resolve_date_basis_candidate(entries, candidates, None) is None
        assert _resolve_date_basis_candidate(entries, candidates, {}) is None

    def test_no_raw_centers_returns_none(self):
        # raw entries に有効 box がない → raw_centers 空 → None
        template_coords = {"date": [[0, 0], [10, 0], [10, 10], [0, 10]], "name": [[5, 5], [8, 5], [8, 8], [5, 8]]}
        assert _resolve_date_basis_candidate([], [], template_coords) is None

    def test_no_field_centers_returns_none(self):
        # template に date 以外のフィールドなし → field_centers 空 → None
        entries = [{"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}]
        candidates = find_date_candidates(entries, "2026-01-15")
        template_coords = {"date": [[0, 0], [10, 0], [10, 10], [0, 10]]}
        assert _resolve_date_basis_candidate(entries, candidates, template_coords) is None

    def test_candidate_without_valid_box_is_skipped(self):
        # 全候補の box 無効 → ranked 空 → None
        template_coords = {"date": [[0, 0], [10, 0], [10, 10], [0, 10]], "name": [[5, 5], [8, 5], [8, 8], [5, 8]]}
        entries = [{"text": "2026/01/15", "box": None}]
        candidates = [{"text": "2026/01/15", "box": None}]
        assert _resolve_date_basis_candidate(entries, candidates, template_coords) is None

    def test_unique_best_candidate_adopted(self):
        # 候補2つのうち、片方だけテンプレ配置と整合 → 採用
        template_coords = {
            "date": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "name": [[20, 0], [30, 0], [30, 10], [20, 10]],  # date から +20px
        }
        entries = [
            # 候補A: top-left=(100,100) → name 投影 (120,100) に raw box あり
            {"text": "2026/01/15", "box": [[100, 100], [110, 100], [110, 110], [100, 110]]},
            {"text": "山田 太郎", "box": [[120, 100], [130, 100], [130, 110], [120, 110]]},
            # 候補B: top-left=(500,500) → name 投影 (520,500) に raw box なし
            {"text": "2026/01/15", "box": [[500, 500], [510, 500], [510, 510], [500, 510]]},
        ]
        candidates = find_date_candidates(entries, "2026-01-15")
        result = _resolve_date_basis_candidate(entries, candidates, template_coords)
        assert result == [[100, 100], [110, 100], [110, 110], [100, 110]]

    def test_tied_candidates_return_none(self):
        # 対称配置の2候補 → 同点 → None（安全側）
        template_coords = {
            "date": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "name": [[20, 0], [30, 0], [30, 10], [20, 10]],
        }
        raw_a = {"text": "2026/01/15", "box": [[0, 0], [10, 0], [10, 10], [0, 10]]}
        name_a = {"text": "山田A", "box": [[20, 0], [30, 0], [30, 10], [20, 10]]}
        raw_b = {"text": "2026/01/15", "box": [[100, 0], [110, 0], [110, 10], [100, 10]]}
        name_b = {"text": "山田B", "box": [[120, 0], [130, 0], [130, 10], [120, 10]]}
        entries = [raw_a, name_a, raw_b, name_b]
        candidates = [raw_a, raw_b]
        assert _resolve_date_basis_candidate(entries, candidates, template_coords) is None

    def test_single_ranked_candidate_skips_tie_check(self):
        # ranked が 1 件のみ（他候補は box 不正）→ 同点判定をスキップして採用
        template_coords = {
            "date": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "name": [[20, 0], [30, 0], [30, 10], [20, 10]],
        }
        good = {"text": "2026/01/15", "box": [[0, 0], [10, 0], [10, 10], [0, 10]]}
        name = {"text": "山田", "box": [[20, 0], [30, 0], [30, 10], [20, 10]]}
        bad = {"text": "2026/01/15", "box": None}
        entries = [good, name, bad]
        result = _resolve_date_basis_candidate(entries, [good, bad], template_coords)
        assert result == good["box"]


# ------------------------------------------------------------------
# resolve_date_anchor: topmost 分岐のエッジ
# ------------------------------------------------------------------


class TestResolveDateAnchorTopmostEdge:
    def test_multiple_candidates_within_threshold_unique(self):
        """topmost 分岐: 閾値内の候補が一意なら採用、閾値外は不採用."""
        from app.date_anchor import resolve_date_anchor

        template = [[0, 0], [10, 0], [10, 10], [0, 10]]
        near = {"text": "2026/01/15", "box": [[5, 5], [15, 5], [15, 15], [5, 15]]}  # center (10,10)
        far = {"text": "2026/01/15", "box": [[500, 500], [510, 500], [510, 510], [500, 510]]}
        # 近い方のみ採用される
        assert resolve_date_anchor([far, near], "2026-01-15", template_date_box=template) == near["box"]


# ------------------------------------------------------------------
# apply_date_anchor_normalization: 失敗理由（レビュー P2#1）
# ------------------------------------------------------------------


class TestApplyDateAnchorNormalizationReasons:
    """apply_date_anchor_normalization の各失敗理由."""

    def _clinic_with_template(self, temp_db: Path, coords: dict, coord_basis: str | None = None) -> None:
        import uuid

        from app.db import upsert_clinic, upsert_template

        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "あおばクリニック")
        kwargs = {"coord_basis": coord_basis} if coord_basis else {}
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections=coords,
            **kwargs,
        )

    def test_no_date_or_clinic(self, tmp_path: Path):
        temp_db = _make_db(tmp_path)
        raw_path = tmp_path / "d.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック", "date": None}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) == (False, NO_DATE_OR_CLINIC)
        structured = {"clinic": None, "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) == (False, NO_DATE_OR_CLINIC)

    def test_no_template(self, tmp_path: Path):
        import uuid

        from app.db import upsert_clinic

        temp_db = _make_db(tmp_path)
        upsert_clinic(temp_db, str(uuid.uuid4()), "あおばクリニック")
        raw_path = tmp_path / "e.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) == (False, NO_TEMPLATE)

    def test_no_date_coords(self, tmp_path: Path):
        temp_db = _make_db(tmp_path)
        self._clinic_with_template(temp_db, {"amount": [[10, 10], [20, 10], [20, 20], [10, 20]]})
        raw_path = tmp_path / "f.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) == (False, NO_DATE_COORDS)

    def test_no_ocr_entries(self, tmp_path: Path):
        temp_db = _make_db(tmp_path)
        self._clinic_with_template(temp_db, {"date": [[0, 0], [10, 0], [10, 10], [0, 10]]})
        raw_path = tmp_path / "g.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) == (False, NO_OCR_ENTRIES)

    def test_anchor_unresolved_when_no_date_candidates(self, tmp_path: Path):
        """date 候補 0 件 → (False, ANCHOR_UNRESOLVED)（force 発火対象）."""
        temp_db = _make_db(tmp_path)
        self._clinic_with_template(temp_db, {"date": [[0, 0], [150, 0], [150, 30], [0, 30]]}, coord_basis="date")
        raw_path = tmp_path / "h.json"
        raw_path.write_text(
            json.dumps([{"text": "3,800", "box": [[400, 300], [480, 300], [480, 340], [400, 340]]}]),
            encoding="utf-8",
        )
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) == (False, ANCHOR_UNRESOLVED)

    def test_no_db(self, tmp_path: Path):
        raw_path = tmp_path / "i.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, None) == (False, NO_DB)

    def test_not_normalized(self, tmp_path: Path):
        """normalize_coordinates_by_anchor が normalized=False → (False, NOT_NORMALIZED)."""
        temp_db = _make_db(tmp_path)
        self._clinic_with_template(temp_db, {"date": [[0, 0], [150, 0], [150, 30], [0, 30]]}, coord_basis="date")
        raw_path = tmp_path / "j.json"
        raw_path.write_text(
            json.dumps([{"text": "2026/01/15", "box": [[50, 100], [150, 100], [150, 130], [50, 130]]}]),
            encoding="utf-8",
        )
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        with patch(
            "app.coord_normalizer.normalize_coordinates_by_anchor",
            return_value={"normalized": False, "offset_x": 0, "offset_y": 0},
        ):
            result = apply_date_anchor_normalization(raw_path, structured, temp_db)
        assert result == (False, NOT_NORMALIZED)


# ------------------------------------------------------------------
# apply_date_basis_force: エッジケース
# ------------------------------------------------------------------


class TestApplyDateBasisForceEdgeCases:
    """force のゲート不通過ケース."""

    def _setup(self, tmp_path: Path, coord_basis: str, coords: dict) -> tuple[Path, Path]:
        import uuid

        from app.db import upsert_clinic, upsert_template

        temp_db = _make_db(tmp_path)
        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "あおばクリニック")
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections=coords,
            coord_basis=coord_basis,
        )
        return temp_db, tmp_path / "raw.json"

    def test_no_template_returns_false(self, tmp_path: Path):
        import uuid

        from app.db import upsert_clinic

        temp_db = _make_db(tmp_path)
        upsert_clinic(temp_db, str(uuid.uuid4()), "あおばクリニック")
        raw_path = tmp_path / "raw.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック"}
        assert apply_date_basis_force(raw_path, structured, temp_db) is False

    def test_date_basis_mismatch_returns_false(self, tmp_path: Path):
        temp_db, raw_path = self._setup(tmp_path, "topmost", {"date": [[0, 0], [10, 0], [10, 10], [0, 10]]})
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック"}
        assert apply_date_basis_force(raw_path, structured, temp_db) is False

    def test_no_entries_returns_false(self, tmp_path: Path):
        temp_db, raw_path = self._setup(tmp_path, "date", {"date": [[0, 0], [10, 0], [10, 10], [0, 10]]})
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック"}
        assert apply_date_basis_force(raw_path, structured, temp_db) is False

    def test_date_unlearned_returns_false(self, tmp_path: Path):
        temp_db, raw_path = self._setup(tmp_path, "date", {"name": [[5, 5], [8, 5], [8, 8], [5, 8]]})
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック"}
        assert apply_date_basis_force(raw_path, structured, temp_db) is False


# ------------------------------------------------------------------
# _apply_coordinate_extraction
# ------------------------------------------------------------------


class TestApplyCoordinateExtraction:
    def test_empty_entries_noop(self, tmp_path: Path):
        raw_path = tmp_path / "raw.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured: dict = {"name": None}
        _apply_coordinate_extraction(
            tmp_path / "out", raw_path, structured, {"name": [[0, 0], [1, 0], [1, 1], [0, 1]]}
        )
        assert structured["name"] is None  # 変化なし

    def test_updates_and_writes_json(self, tmp_path: Path):
        raw_path = tmp_path / "x_1-raw_data.json"
        raw_path.write_text(
            json.dumps([{"text": "3,800", "box": [[400, 300], [480, 300], [480, 340], [400, 340]]}]),
            encoding="utf-8",
        )
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        structured: dict = {"amount": None}
        _apply_coordinate_extraction(
            output_dir,
            raw_path,
            structured,
            {"amount": [[400, 300], [480, 300], [480, 340], [400, 340]]},
        )
        assert structured["amount"] == "3,800"
        out = output_dir / "x_1-structured_data.json"
        assert out.exists()
        assert json.loads(out.read_text(encoding="utf-8"))["amount"] == "3,800"


# ------------------------------------------------------------------
# _to_ocr_entries: 残りフォーマット
# ------------------------------------------------------------------


class TestToOcrEntriesExtra:
    """_to_ocr_entries の残りフォーマット（カバレッジ補完）."""

    def test_dict_ocr_entries_key(self):
        assert _to_ocr_entries({"ocr_entries": [{"text": "a"}]}) == [{"text": "a"}]

    def test_dict_ocr_json_key(self):
        assert _to_ocr_entries({"ocr_json": [{"text": "b"}]}) == [{"text": "b"}]

    def test_dict_data_key(self):
        assert _to_ocr_entries({"data": [{"text": "c"}]}) == [{"text": "c"}]

    def test_non_dict_items_filtered(self):
        assert _to_ocr_entries([{"text": "a"}, "skip", 42]) == [{"text": "a"}]
