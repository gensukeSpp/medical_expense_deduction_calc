"""Pure calculation functions for the monthly receipt list (Issue #54)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ReceiptRow:
    """一覧表示用の領収書 1 行分のデータ。"""

    file_stem: str
    date: str = ""
    clinic: str = ""
    name: str = ""
    amount: int = 0
    compensation: int = 0  # 保険で補填された額（ユーザー入力）


def compute_contribution(amount: int, compensation: int) -> int:
    """その行の控除対象額。補填額は行単位で控除し、下限 0。

    受け入れ要件2: 補填額はあくまでその診察・治療・処方にのみ適用するため、
    行の金額を超える補填は行を 0 にする（負にしない）。月合計へ逆算しない。
    """
    return max(0, amount - compensation)


def compute_total(rows: List[ReceiptRow]) -> int:
    """表示対象行の合計 = Σ 行ごとの控除対象額。"""
    return sum(compute_contribution(r.amount, r.compensation) for r in rows)


def month_of(date_str: str) -> str:
    """'YYYY-MM-DD' -> 'YYYY-MM'。不正・空は空文字。"""
    return date_str[:7]


def collect_months(rows: List[ReceiptRow]) -> List[str]:
    """データ中に存在する月（'YYYY-MM'）のソート済みユニーク一覧。"""
    return sorted({month_of(r.date) for r in rows if month_of(r.date)})


def available_names(rows: List[ReceiptRow]) -> List[str]:
    """データ中に存在する氏名のソート済みユニーク一覧。"""
    return sorted({r.name for r in rows if r.name})


def filter_by_names(rows: List[ReceiptRow], names: Optional[List[str]]) -> List[ReceiptRow]:
    """選択された氏名のみに絞り込み。names が空リスト/None なら全件返す。"""
    if not names:
        return rows
    name_set = set(names)
    return [r for r in rows if r.name in name_set]


def sort_by_date(rows: List[ReceiptRow], ascending: bool = True) -> List[ReceiptRow]:
    """日付文字列（ISO）でソート。ascending=False で降順。"""
    return sorted(rows, key=lambda r: r.date, reverse=not ascending)
