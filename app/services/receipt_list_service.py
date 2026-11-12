"""Receipt list service: builds list-page context and persists compensation (Issue #54)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.calc import (
    ReceiptRow,
    available_names,
    collect_months,
    compute_total,
    filter_by_names,
    month_of,
    sort_by_date,
)
from app.normalization import parse_amount, parse_date
from app.services.receipt_file_repository import ReceiptFileRepository


def _to_amount(value: Any) -> int:
    """値を整数金額へ変換する。空・不正は 0 扱い。"""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    parsed = parse_amount(str(value))
    return parsed if parsed is not None else 0


class ReceiptListService:
    """一覧ページ向けのデータ読取・整形・補填額保存を担当する（SRP）。"""

    def __init__(self, output_dir: Path) -> None:
        """Args:
        output_dir: structured_data.json が置かれるディレクトリ。
        """
        self.output_dir = Path(output_dir)
        self.file_repo = ReceiptFileRepository(self.output_dir)

    def load_rows(self) -> List[ReceiptRow]:
        """全 structured_data を読み込み ReceiptRow リストにする。

        1ファイルの読み込み失敗はスキップする（既存 get_all_receipts と同様）。
        """
        rows: List[ReceiptRow] = []
        for path in self.file_repo.find_all_receipt_files():
            stem = path.stem.replace("-structured_data", "")
            try:
                data = self.file_repo.load_receipt(stem)
            except Exception:
                continue
            date_str = str(data.get("date") or "")
            if parse_date(date_str) is None:
                date_str = ""
            rows.append(
                ReceiptRow(
                    file_stem=stem,
                    date=date_str,
                    clinic=str(data.get("clinic") or ""),
                    name=str(data.get("name") or ""),
                    amount=_to_amount(data.get("amount")),
                    compensation=_to_amount(data.get("compensation")),
                )
            )
        return rows

    def get_page_context(
        self,
        month: str,
        sort: str = "asc",
        names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """一覧断片を描画するための表示文脈を構築する。

        Args:
            month: 'YYYY-MM'。月別ページ全体。
            sort: 'asc' or 'desc'。
            names: 選択された氏名リスト（空なら全件）。

        Returns:
            rows / total / months / available_names / selected_names / month / sort /
            prev_month / next_month を含む dict。
        """
        rows = self.load_rows()
        months = collect_months(rows)
        current_month = self._resolve_month(month, months)
        month_rows = [r for r in rows if month_of(r.date) == current_month]
        month_rows = filter_by_names(month_rows, names)
        month_rows = sort_by_date(month_rows, ascending=(sort != "desc"))
        return {
            "month": current_month,
            "sort": sort,
            "rows": month_rows,
            "total": compute_total(month_rows),
            "months": months,
            "available_names": available_names(rows),
            "selected_names": list(names) if names else [],
            "prev_month": self._neighbor_month(months, current_month, -1),
            "next_month": self._neighbor_month(months, current_month, 1),
        }

    def save_compensation(self, file_stem: str, value: Any) -> None:
        """補填額を structured_data.json の compensation キーに永続化する。

        Raises:
            FileNotFoundError: 対象レシートの JSON が存在しない場合。
        """
        data = self.file_repo.load_receipt(file_stem)  # FileNotFoundError をそのまま伝播
        data["compensation"] = _to_amount(value)
        self.file_repo.save_receipt(file_stem, data)

    @staticmethod
    def _resolve_month(month: str, months: List[str]) -> str:
        """表示する月を返す。

        明示された月はそのまま使う（データが無い月は空の表 + 合計 0）。
        月が未指定なら既定月（今年の1月 → データ先頭）を使う。
        """
        if month:
            return month
        current_year_month = f"{date.today().year}-01"
        if current_year_month in months:
            return current_year_month
        return months[0] if months else current_year_month

    @staticmethod
    def _neighbor_month(months: List[str], current: str, offset: int) -> str:
        """月リスト上で隣接する月を返す。無ければ空文字。"""
        if current not in months:
            return ""
        index = months.index(current) + offset
        if 0 <= index < len(months):
            return months[index]
        return ""
