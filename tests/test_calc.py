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
from app.normalization import parse_amount

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


def test_parse_amount_rejects_negative_values():
    """負の金額は解析不能として扱い、補填額ロジックで 0 に丸める"""
    assert parse_amount("-500円") is None
    assert parse_amount("-500") is None
    assert parse_amount("−500円") is None


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


# --- ReceiptListService: ファイル読取・補填額保存 ---

from app.output import write_json_atomic  # noqa: E402
from app.services.receipt_list_service import ReceiptListService  # noqa: E402


def _write_sample(output_dir, stem, data):
    write_json_atomic(output_dir / f"{stem}-structured_data.json", data)


def test_load_rows_reads_structured_files(tmp_path):
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "r1", {"name": "山田", "clinic": "あおば", "amount": 5000, "date": "2026-01-15"})
    service = ReceiptListService(out)
    normal_rows, date_missing_rows = service.load_rows()
    assert len(normal_rows) == 1
    assert normal_rows[0].file_stem == "r1"
    assert normal_rows[0].amount == 5000
    assert len(date_missing_rows) == 0


def test_get_page_context_filters_and_totals(tmp_path):
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(
        out,
        "a",
        {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01", "compensation": 6000},
    )
    _write_sample(out, "b", {"name": "鈴木", "clinic": "B", "amount": 3000, "date": "2026-02-01"})
    service = ReceiptListService(out)
    ctx = service.get_page_context("2026-01", "asc", [])
    assert ctx["total"] == 0  # A: 5000-6000 -> 0 で、1月分のみ
    assert [r.file_stem for r in ctx["rows"]] == ["a"]
    assert ctx["months"] == ["2026-01", "2026-02"]


def test_get_page_context_name_filter_changes_total(tmp_path):
    """氏名選択で合計が表示対象分に変わる（受け入れ要件1）"""
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "山田", "clinic": "A", "amount": 1000, "date": "2026-01-01"})
    _write_sample(out, "b", {"name": "鈴木", "clinic": "B", "amount": 2000, "date": "2026-01-02"})
    service = ReceiptListService(out)
    all_ctx = service.get_page_context("2026-01", "asc", [])
    assert all_ctx["total"] == 3000
    yamada_ctx = service.get_page_context("2026-01", "asc", ["山田"])
    assert yamada_ctx["total"] == 1000
    assert yamada_ctx["selected_names"] == ["山田"]


def test_save_compensation_persists_to_json(tmp_path):
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01"})
    service = ReceiptListService(out)
    service.save_compensation("a", "6000")
    data = service.file_repo.load_receipt("a")
    assert data["compensation"] == 6000


def test_save_compensation_blank_becomes_zero(tmp_path):
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01"})
    service = ReceiptListService(out)
    service.save_compensation("a", "")
    data = service.file_repo.load_receipt("a")
    assert data["compensation"] == 0


def test_save_compensation_negative_becomes_zero(tmp_path):
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01"})
    service = ReceiptListService(out)
    service.save_compensation("a", "-500")
    data = service.file_repo.load_receipt("a")
    assert data["compensation"] == 0


def test_get_page_context_filters_comma_names_without_split(tmp_path):
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "鈴木, 花子", "clinic": "A", "amount": 1200, "date": "2026-01-03"})
    _write_sample(out, "b", {"name": "山田 太郎", "clinic": "A", "amount": 2000, "date": "2026-01-04"})
    service = ReceiptListService(out)
    ctx = service.get_page_context("2026-01", "asc", ["鈴木, 花子"])
    assert ctx["selected_names"] == ["鈴木, 花子"]
    assert [r.file_stem for r in ctx["rows"]] == ["a"]
    assert ctx["total"] == 1200


def test_save_compensation_keeps_other_fields(tmp_path):
    """補填額保存は他のフィールドを壊さない"""
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01"})
    service = ReceiptListService(out)
    service.save_compensation("a", "1000")
    data = service.file_repo.load_receipt("a")
    assert data["name"] == "山田"
    assert data["amount"] == 5000
    assert data["date"] == "2026-01-01"


def test_get_page_context_date_missing_count(tmp_path):
    """日付不明の行があるとき、date_missing_count がセットされること"""
    out = tmp_path / "output_json"
    out.mkdir()
    _write_sample(out, "a", {"name": "山田", "clinic": "A", "amount": 5000, "date": "2026-01-01"})
    _write_sample(out, "b", {"name": "鈴木", "clinic": "B", "amount": 3000, "date": ""})  # date missing
    service = ReceiptListService(out)
    ctx = service.get_page_context("2026-01", "asc", [])
    assert ctx["total"] == 5000  # only 山田 is in 2026-01, compensation=0
    assert ctx["date_missing_count"] == 1  # 鈴木の日付不明
    assert len(ctx["rows"]) == 1
    assert ctx["rows"][0].file_stem == "a"
