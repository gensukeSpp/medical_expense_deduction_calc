"""Branch-completion tests for app/date_anchor.py (lines 149/152/240/344, PR #53 review P3)."""

from __future__ import annotations

import json
from pathlib import Path

from app.date_anchor import (
    NO_DATE_COORDS,
    _resolve_date_basis_candidate,
    apply_date_anchor_normalization,
)


def _make_db(tmp_path: Path) -> Path:
    from app.db_migrations import run_migrations

    db_path = tmp_path / "test.db"
    run_migrations(db_path, "docs/schema.sql")
    return db_path


class TestResolveDateAnchorTopmostBoxEdge:
    """resolve_date_anchor topmost 分岐: box 無効 / center 計算不能の候補はスキップ."""

    def test_skips_invalid_and_centerless_candidates(self):
        from app.date_anchor import resolve_date_anchor

        template = [[0, 0], [10, 0], [10, 10], [0, 10]]
        entries = [
            # box なし → スキップ（line 149）
            {"text": "2026/01/15", "box": None},
            {"text": "2026/01/15", "box": [[5, 5], [15, 5], [15, 15], [5, 15]]},  # 有効・near → 採用
            {"text": "2026/01/15", "box": [[100, 100]]},  # center 計算不能 → スキップ（line 152）
        ]
        # box なしと center 不能はスキップされ、有効な near 候補のみ within 1 件 → 採用
        assert resolve_date_anchor(entries, "2026-01-15", template_date_box=template) == [
            [5, 5],
            [15, 5],
            [15, 15],
            [5, 15],
        ]

    def test_invalid_center_candidate_skipped_keeps_valid(self):
        from app.date_anchor import resolve_date_anchor

        template = [[0, 0], [10, 0], [10, 10], [0, 10]]
        entries = [
            {"text": "2026/01/15", "box": [[100, 100]]},  # center 不能 → スキップ
            {"text": "2026/01/15", "box": [[5, 5], [15, 5], [15, 15], [5, 15]]},  # near → 採用
        ]
        assert resolve_date_anchor(entries, "2026-01-15", template_date_box=template) == [
            [5, 5],
            [15, 5],
            [15, 15],
            [5, 15],
        ]


class TestResolveDateBasisCandidateEmptyRanked:
    """_resolve_date_basis_candidate: ranked 空は既存テストと重複しない別経路でカバー."""

    def test_candidates_without_boxes_yields_empty_ranked(self):
        template_coords = {
            "date": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "name": [[5, 5], [8, 5], [8, 8], [5, 8]],
        }
        # 候補 box が空リスト → _top_left_box は有効ポイントなし → None → ranked 空
        empty_box_candidate = {"text": "2026/01/15", "box": []}
        raw_entries = [{"text": "other", "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}]
        result = _resolve_date_basis_candidate(raw_entries, [empty_box_candidate], template_coords)
        assert result is None


class TestApplyDateAnchorNormalizationMigrationFailure:
    """topmost → date 移行で shift_template_to_date_basis が None → (False, NO_DATE_COORDS)."""

    def test_migration_failure_returns_no_date_coords(self, tmp_path: Path):
        import uuid

        from app.db import upsert_clinic, upsert_template, insert_receipt

        temp_db = _make_db(tmp_path)
        clinic_id = str(uuid.uuid4())
        upsert_clinic(temp_db, clinic_id, "あおばクリニック")
        # date box に有効な数値ポイントがない → shift_template_to_date_basis が None
        upsert_template(
            temp_db,
            str(uuid.uuid4()),
            clinic_id,
            version=1,
            coords_corrections={
                "date": [],
                "amount": [[300, 200], [400, 200], [400, 230], [300, 230]],
            },
        )
        raw_path = tmp_path / "k.json"
        entries = [
            {"text": "2026/01/15", "box": [[50, 100], [150, 100], [150, 130], [50, 130]]},
            {"text": "3,800", "box": [[300, 200], [400, 200], [400, 230], [300, 230]]},
        ]
        raw_path.write_text(json.dumps(entries), encoding="utf-8")
        insert_receipt(
            temp_db,
            str(uuid.uuid4()),
            str(raw_path),
            entries,
            {"clinic": "あおばクリニック", "date": "2026-01-15"},
            clinic_id,
        )

        structured = {"clinic": "あおばクリニック", "date": "2026-01-15"}
        result = apply_date_anchor_normalization(raw_path, structured, temp_db)
        assert result == (False, NO_DATE_COORDS)
