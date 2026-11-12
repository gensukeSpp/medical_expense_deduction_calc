"""Tests for app.calc pure calculation functions (Issue #54)."""

from __future__ import annotations

from app.calc import (
    ReceiptRow,
    available_names,
    collect_months,
    compute_contribution,
    compute_total,
    filter_by_names,
    month_of,
    sort_by_date,
)

# --- compute_contribution (受け入れ要件2の核心) ---


def test_contribution_subtracts_compensation():
    """補填額が金額以下のとき amount - compensation"""
    assert compute_contribution(5000, 1000) == 4000


def test_contribution_floor_at_zero_when_compensation_exceeds_amount():
    """補填額が金額を超える場合、負にせず 0 を返す"""
    assert compute_contribution(5000, 6000) == 0


def test_contribution_exact_equal_is_zero():
    """補填額 = 金額 なら 0"""
    assert compute_contribution(5000, 5000) == 0


def test_contribution_no_compensation():
    """補填額なし(0)は金額そのまま"""
    assert compute_contribution(3000, 0) == 3000


# --- compute_total: 行ごとに控除し、月合計へ逆算しない ---


def test_total_is_per_row_not_global_subtraction():
    """
    受け入れ要件2: 月合計は「全金額合計 - 全補填額合計」ではない。
    行A 5000/補填6000 -> 0、行B 3000/補填0 -> 3000 のとき合計は 3000。
    8000 - 6000 = 2000 にはならない。
    """
    rows = [
        ReceiptRow(file_stem="a", amount=5000, compensation=6000, date="2026-01-01"),
        ReceiptRow(file_stem="b", amount=3000, compensation=0, date="2026-01-02"),
    ]
    assert compute_total(rows) == 3000


def test_total_empty_rows():
    """行が 0 件のとき合計は 0"""
    assert compute_total([]) == 0


# --- month_of / collect_months / available_names ---


def test_month_of_extracts_yyyy_mm():
    assert month_of("2026-01-15") == "2026-01"


def test_month_of_empty():
    assert month_of("") == ""


def test_collect_months_sorted_unique():
    rows = [
        ReceiptRow(file_stem="1", date="2026-03-01"),
        ReceiptRow(file_stem="2", date="2026-01-05"),
        ReceiptRow(file_stem="3", date="2026-03-20"),
    ]
    assert collect_months(rows) == ["2026-01", "2026-03"]


def test_available_names_sorted_unique():
    rows = [
        ReceiptRow(file_stem="1", name="山田 太郎"),
        ReceiptRow(file_stem="2", name="鈴木 花子"),
        ReceiptRow(file_stem="3", name="山田 太郎"),
    ]
    assert available_names(rows) == ["山田 太郎", "鈴木 花子"]


# --- filter_by_names / sort_by_date ---


def test_filter_by_names_empty_returns_all():
    rows = [
        ReceiptRow(file_stem="a", name="山田"),
        ReceiptRow(file_stem="b", name="鈴木"),
    ]
    assert filter_by_names(rows, []) == rows


def test_filter_by_names_matches_selected():
    rows = [
        ReceiptRow(file_stem="a", name="山田"),
        ReceiptRow(file_stem="b", name="鈴木"),
    ]
    result = filter_by_names(rows, ["山田"])
    assert [r.file_stem for r in result] == ["a"]


def test_sort_by_date_ascending():
    rows = [
        ReceiptRow(file_stem="b", date="2026-02-01"),
        ReceiptRow(file_stem="a", date="2026-01-01"),
    ]
    result = sort_by_date(rows, ascending=True)
    assert [r.file_stem for r in result] == ["a", "b"]


def test_sort_by_date_descending():
    rows = [
        ReceiptRow(file_stem="a", date="2026-01-01"),
        ReceiptRow(file_stem="b", date="2026-02-01"),
    ]
    result = sort_by_date(rows, ascending=False)
    assert [r.file_stem for r in result] == ["b", "a"]
