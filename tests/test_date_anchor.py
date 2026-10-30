"""Tests for app/date_anchor.py — date anchor resolution and re-normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.date_anchor import (
    find_date_candidates,
    resolve_date_anchor,
    apply_date_anchor_normalization,
    _to_ocr_entries,
)

# ------------------------------------------------------------------
# find_date_candidates
# ------------------------------------------------------------------


class TestFindDateCandidates:
    def test_matches_iso_format(self):
        entries = [
            {"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
            {"text": "3,800円", "box": [[30, 30], [40, 30], [40, 40], [30, 40]]},
        ]
        result = find_date_candidates(entries, "2026-01-15")
        assert len(result) == 1
        assert result[0]["text"] == "2026/01/15"

    def test_matches_kanji_format(self):
        entries = [
            {"text": "2026年1月15日", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
        ]
        result = find_date_candidates(entries, "2026/01/15")
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        entries = [{"text": "2026/02/01", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}]
        assert find_date_candidates(entries, "2026-01-15") == []

    def test_missing_date_returns_empty(self):
        assert find_date_candidates([{"text": "2026/01/15"}], "") == []
        assert find_date_candidates([{"text": "2026/01/15"}], "invalid") == []

    def test_skips_entries_without_box(self):
        entries = [
            {"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
            {"text": "2026/01/15"},  # no box -> skipped
        ]
        assert len(find_date_candidates(entries, "2026-01-15")) == 1


# ------------------------------------------------------------------
# resolve_date_anchor
# ------------------------------------------------------------------


class TestResolveDateAnchor:
    def test_single_candidate_returns_box(self):
        box = [[10, 10], [20, 10], [20, 20], [10, 20]]
        entries = [
            {"text": "2026/01/15", "box": box},
            {"text": "診療", "box": [[0, 0], [5, 0], [5, 5], [0, 5]]},
        ]
        result = resolve_date_anchor(entries, "2026-01-15", template_date_box=box)
        assert result == box

    def test_no_template_box_returns_none(self):
        entries = [{"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}]
        assert resolve_date_anchor(entries, "2026-01-15", template_date_box=None) is None

    def test_no_text_match_returns_none(self):
        entries = [{"text": "2026/02/01", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}]
        box = [[10, 10], [20, 10], [20, 20], [10, 20]]
        assert resolve_date_anchor(entries, "2026-01-15", template_date_box=box) is None

    def test_duplicate_unique_by_proximity(self):
        # Two date boxes; template points near the second.
        box_a = [[400, 400], [420, 400], [420, 410], [400, 410]]
        box_b = [[50, 60], [70, 60], [70, 70], [50, 70]]
        template = [[50, 60], [80, 60], [80, 70], [50, 70]]
        entries = [
            {"text": "2026/01/15", "box": box_a},
            {"text": "2026/01/15", "box": box_b},
        ]
        result = resolve_date_anchor(entries, "2026-01-15", template_date_box=template)
        assert result == box_b

    def test_duplicate_ambiguous_returns_none(self):
        # Both candidates near template -> ambiguous.
        box_a = [[48, 60], [68, 60], [68, 70], [48, 70]]
        box_b = [[52, 60], [72, 60], [72, 70], [52, 70]]
        template = [[50, 60], [80, 60], [80, 70], [50, 70]]
        entries = [
            {"text": "2026/01/15", "box": box_a},
            {"text": "2026/01/15", "box": box_b},
        ]
        assert resolve_date_anchor(entries, "2026-01-15", template_date_box=template) is None

    def test_duplicate_date_basis_picks_by_layout(self):
        # date 基準テンプレート（date box 原点）で 2 候補のうち、name/clinic/amount との
        # レイアウト整合が良い候補（調剤日）が選ばれる（ハイポテーシス検証）。
        entries = [
            {"text": "調剤日：2026/09/02", "box": [[132, 269], [453, 269], [453, 300], [132, 300]]},
            {"text": "発行日：2026/09/02", "box": [[127, 298], [454, 297], [454, 331], [127, 332]]},
            {"text": "なの花薬局札幌二十四軒店", "box": [[142, 72], [620, 73], [620, 101], [142, 100]]},
            {"text": "3,200", "box": [[638, 710], [700, 710], [700, 748], [638, 748]]},
        ]
        # date 基準テンプレート: date box 原点、name/clinic/amount は date box 左上起点の相対座標
        template_coords = {
            "date": [[0, 0], [357, 4], [357, 35], [0, 31]],
            "name": [[32, -175], [210, -171], [209, -111], [31, -115]],
            "clinic": [[-23, 710], [484, 706], [484, 743], [-23, 747]],
            "amount": [[506, 441], [648, 448], [646, 483], [504, 476]],
        }
        result = resolve_date_anchor(
            entries,
            "2026-09-02",
            template_date_box=template_coords["date"],
            template_basis="date",
            template_coords=template_coords,
        )
        assert result is not None
        # 調剤日（先頭候補）が採用される
        assert result[0] == [132, 269]

    def test_duplicate_date_basis_tie_returns_none(self):
        # date 基準で同一点数の候補が 2 つ以上ある場合は安全側で None。
        entries = [
            {"text": "2026/09/02", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
            {"text": "2026/09/02", "box": [[30, 30], [40, 30], [40, 40], [30, 40]]},
        ]
        # name/clinic/amount を一切持たない（date のみ）→ field_centers 空 → None
        template_coords = {"date": [[0, 0], [10, 0], [10, 10], [0, 10]]}
        result = resolve_date_anchor(
            entries,
            "2026-09-02",
            template_date_box=template_coords["date"],
            template_basis="date",
            template_coords=template_coords,
        )
        assert result is None

    def test_invalid_template_box_returns_none(self):
        entries = [
            {"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
            {"text": "2026/01/15", "box": [[30, 30], [40, 30], [40, 40], [30, 40]]},
        ]
        assert resolve_date_anchor(entries, "2026-01-15", template_date_box=[]) is None


# ------------------------------------------------------------------
# _to_ocr_entries
# ------------------------------------------------------------------


class TestToOcrEntries:
    def test_list(self):
        assert _to_ocr_entries([{"text": "a"}]) == [{"text": "a"}]

    def test_dict_words(self):
        assert _to_ocr_entries({"words": [{"text": "a"}]}) == [{"text": "a"}]

    def test_empty(self):
        assert _to_ocr_entries(None) == []
        assert _to_ocr_entries({}) == []


class TestDateValidity:
    """[P2] parse_date の暦妥当性未検証により無効日付が anchor になるのを防ぐ。"""

    def test_invalid_month_day_rejected(self):
        # 2026-99-99 は parse_date を通るが実日付ではない → 候補にならない
        entries = [
            {"text": "2026/99/99", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
        ]
        assert find_date_candidates(entries, "2026-99-99") == []

    def test_leap_day_valid(self):
        # うるう日 2024-02-29 は有効
        entries = [
            {"text": "2024/02/29", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
        ]
        result = find_date_candidates(entries, "2024-02-29")
        assert len(result) == 1

    def test_non_leap_feb29_rejected(self):
        # 2023-02-29 は実在しない → 候補にならない
        entries = [
            {"text": "2023/02/29", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
        ]
        assert find_date_candidates(entries, "2023-02-29") == []

    def test_month_boundary_rejected(self):
        # 2026-13-01 は実在しない月 → 候補にならない
        entries = [
            {"text": "2026/13/01", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
        ]
        assert find_date_candidates(entries, "2026-13-01") == []

    def test_day_boundary_rejected(self):
        # 2026-01-32 は実在しない日 → 候補にならない
        entries = [
            {"text": "2026/01/32", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
        ]
        assert find_date_candidates(entries, "2026-01-32") == []

    def test_valid_date_still_matches(self):
        # 有効な日付は従来通りマッチする
        entries = [
            {"text": "2026/01/15", "box": [[10, 10], [20, 10], [20, 20], [10, 20]]},
            {"text": "3,800円", "box": [[30, 30], [40, 30], [40, 40], [30, 40]]},
        ]
        result = find_date_candidates(entries, "2026-01-15")
        assert len(result) == 1
        assert result[0]["text"] == "2026/01/15"


# ------------------------------------------------------------------
# apply_date_anchor_normalization (integration with DB)
# ------------------------------------------------------------------


class TestApplyDateAnchorNormalization:
    @pytest.fixture
    def temp_db(self, tmp_path: Path) -> Path:
        from app.db_migrations import run_migrations

        db_file = tmp_path / "test_db.sqlite3"
        run_migrations(db_file, "docs/schema.sql")
        return db_file

    def test_renormalizes_raw_and_updates_db(
        self,
        tmp_path: Path,
        temp_db: Path,
    ):
        """学習済み date template がある場合、raw が date 基準へ再正規化され DB ocr_json が更新される。"""
        import uuid

        from app.db import (
            upsert_clinic,
            upsert_template,
            insert_receipt,
            get_latest_template_by_clinic,
            get_db_connection,
        )

        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "あおばクリニック")
        # date template を topmost 基準で学習済み
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections={
                "date": [[50, 100], [150, 100], [150, 130], [50, 130]],
                "amount": [[300, 200], [400, 200], [400, 230], [300, 230]],
            },
        )

        # raw_data.json（topmost 正規化後）と、posts に保存済みの同一レシート
        raw_path = tmp_path / "a_123-raw_data.json"
        entries = [
            {"text": "2026/01/15", "box": [[50, 100], [150, 100], [150, 130], [50, 130]]},
            {"text": "3,800", "box": [[300, 200], [400, 200], [400, 230], [300, 230]]},
        ]
        raw_path.write_text(json.dumps(entries), encoding="utf-8")

        insert_receipt(
            temp_db,
            str(uuid.uuid4()),
            str(raw_path),
            [{"text": "old", "box": []}],
            {"clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"},
            clinic_id,
        )

        structured = {"clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"}
        result = apply_date_anchor_normalization(raw_path, structured, temp_db)

        assert result == (50, 100)

        # raw が date 基準へ再正規化されている（date box top-left -> (0,0)）
        reraw = json.loads(raw_path.read_text(encoding="utf-8"))
        assert reraw[0]["box"][0] == [0, 0]

        # template が date 基準へ移行している
        template = get_latest_template_by_clinic(temp_db, clinic_id)
        assert template["coord_basis"] == "date"
        assert template["coords_corrections"]["date"][0] == [0, 0]

        # DB の ocr_json が新基準で更新されている
        conn = get_db_connection(temp_db)
        try:
            row = conn.execute("SELECT ocr_json FROM receipts").fetchone()
            stored = json.loads(row["ocr_json"])
        finally:
            conn.close()
        assert stored[0]["box"][0] == [0, 0]

    def test_unlearned_template_returns_none(self, tmp_path: Path, temp_db: Path):
        """date 未学習の clinic では従来方式を維持し、何もしない。"""
        import uuid

        from app.db import upsert_clinic, upsert_template

        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "あおばクリニック")
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections={"amount": [[300, 200], [400, 200], [400, 230], [300, 230]]},
        )

        raw_path = tmp_path / "b_2-raw_data.json"
        raw_path.write_text(
            json.dumps([{"text": "2026/01/15", "box": [[50, 100], [150, 100], [150, 130], [50, 130]]}]),
            encoding="utf-8",
        )

        structured = {"clinic": "あおばクリニック", "amount": 3800, "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, temp_db) is None

        # raw は未変更
        assert json.loads(raw_path.read_text(encoding="utf-8"))[0]["box"][0] == [50, 100]

    def test_no_db_returns_none(self, tmp_path: Path):
        raw_path = tmp_path / "c.json"
        raw_path.write_text("[]", encoding="utf-8")
        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        assert apply_date_anchor_normalization(raw_path, structured, None) is None

    def test_anchor_offset_mismatch_template_date_at_origin(
        self,
        tmp_path: Path,
        temp_db: Path,
    ):
        """[P1] anchor 位置が template の date box と一致しない場合でも、

        template の date box が (0,0) に揃うことを検証する（Issue #39-4）。
        template date box=(50,100)、raw date box=(80,120) の例。
        修正前は template date box が (-30,-20) になっていた。
        """
        import uuid

        from app.db import (
            upsert_clinic,
            upsert_template,
            insert_receipt,
            get_latest_template_by_clinic,
            get_db_connection,
        )

        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "みどり病院")
        # template date box は (50,100) 基準で学習済み
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections={
                "date": [[50, 100], [150, 100], [150, 130], [50, 130]],
                "amount": [[300, 200], [400, 200], [400, 230], [300, 230]],
            },
        )

        # raw date box は (80,120) と、template と一致しない位置
        raw_path = tmp_path / "d_5-raw_data.json"
        entries = [
            {"text": "2026/01/15", "box": [[80, 120], [180, 120], [180, 150], [80, 150]]},
            {"text": "3,800", "box": [[330, 220], [430, 220], [430, 250], [330, 250]]},
        ]
        raw_path.write_text(json.dumps(entries), encoding="utf-8")

        insert_receipt(
            temp_db,
            str(uuid.uuid4()),
            str(raw_path),
            [{"text": "old", "box": []}],
            {"clinic": "みどり病院", "amount": 3800, "date": "2026-01-15"},
            clinic_id,
        )

        structured = {"clinic": "みどり病院", "amount": 3800, "date": "2026-01-15"}
        result = apply_date_anchor_normalization(raw_path, structured, temp_db)

        # raw は date 基準へ再正規化（raw date box top-left -> (0,0)）
        assert result == (80, 120)
        reraw = json.loads(raw_path.read_text(encoding="utf-8"))
        assert reraw[0]["box"][0] == [0, 0]

        # template の date box も (0,0) に揃う（P1 修正の核心）
        template = get_latest_template_by_clinic(temp_db, clinic_id)
        assert template["coord_basis"] == "date"
        assert template["coords_corrections"]["date"][0] == [0, 0]

        # DB の ocr_json も新基準で更新
        conn = get_db_connection(temp_db)
        try:
            row = conn.execute("SELECT ocr_json FROM receipts").fetchone()
            stored = json.loads(row["ocr_json"])
        finally:
            conn.close()
        assert stored[0]["box"][0] == [0, 0]

    def test_writes_coordinate_extraction_to_output_dir(
        self,
        tmp_path: Path,
        temp_db: Path,
    ):
        """date 基準再正規化後に、date 基準テンプレートで座標抽出し structured_data.json へ反映する。

        Issue #42 [中-1] 対応: 手動切替後（coord_basis='date'）のテンプレートでも、raw が date 基準へ
        再正規化され、座標ベースの抽出が structured と structured_data.json へ反映されること。
        """
        import uuid
        from app.db import (
            upsert_clinic,
            upsert_template,
            insert_receipt,
            update_template_basis,
        )
        from app.coord_normalizer import shift_template_to_date_basis

        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "なの花薬局札幌二十四軒店")
        # topmost 基準のテンプレートと raw は同じ座標（topmost 正規化済み相当）。
        # date box 左上 = (132, 269) を anchor として date 基準へシフトすれば、各フィールドが
        # 再正規化後の raw と一致する coherent な geometry を作る。
        topmost_coords = {
            "date": [[132, 269], [453, 269], [453, 300], [132, 300]],
            "name": [[142, 72], [620, 73], [620, 101], [142, 100]],
            "clinic": [[109, 979], [616, 975], [616, 1012], [109, 1016]],
            "amount": [[638, 710], [700, 710], [700, 748], [638, 748]],
        }
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections=topmost_coords,
        )
        # 手動切替: topmost -> date（upsert_template は coord_basis 引数を持たないため update_template_basis を使う）
        date_coords = shift_template_to_date_basis(topmost_coords)
        assert date_coords is not None
        assert (
            update_template_basis(
                temp_db,
                clinic_id,
                date_coords,
                changed_fields=list(topmost_coords.keys()),
                history_reason="manual_basis_toggle",
            )
            is True
        )

        raw_path = tmp_path / "e_6-raw_data.json"
        entries = [
            {"text": "調剤日：2026/09/02", "box": [[132, 269], [453, 269], [453, 300], [132, 300]]},
            {"text": "山田太郎 様", "box": [[142, 72], [620, 73], [620, 101], [142, 100]]},
            {"text": "なの花薬局札幌二十四軒店", "box": [[109, 979], [616, 975], [616, 1012], [109, 1016]]},
            {"text": "3,200", "box": [[638, 710], [700, 710], [700, 748], [638, 748]]},
        ]
        raw_path.write_text(json.dumps(entries), encoding="utf-8")

        insert_receipt(
            temp_db,
            str(uuid.uuid4()),
            str(raw_path),
            entries,
            {"clinic": "なの花薬局札幌二十四軒店", "amount": 3200, "date": "2026-09-02"},
            clinic_id,
        )

        output_dir = tmp_path / "out"
        output_dir.mkdir()
        structured = {
            "clinic": "なの花薬局札幌二十四軒店",
            "amount": None,
            "date": "2026-09-02",
            "name": None,
        }
        result = apply_date_anchor_normalization(raw_path, structured, temp_db, output_dir=output_dir)

        # date 基準へ再正規化された
        assert result is not None
        reraw = json.loads(raw_path.read_text(encoding="utf-8"))
        assert reraw[0]["box"][0] == [0, 0]

        # 座標抽出が structured に反映され、structured_data.json に書かれる
        out = output_dir / "e_6-structured_data.json"
        assert out.exists()
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["name"] not in (None, "")
