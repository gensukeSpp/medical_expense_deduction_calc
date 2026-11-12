# Issue #54: テスト計画

## テスト戦略

- **フレームワーク**: pytest（`uv run pytest`）
- **新規テストファイル**: `tests/test_calc.py` — 純粋計算関数 + 一覧サービスの単体テスト
- **既存テスト拡張**: `tests/test_web.py` — `/list` 一覧・補填額保存・氏名フィルタの結合テスト
- **テストデータ**: モック structured_data JSON を `tmp_path` に配置
- **カバレッジ補完**: `uvx --python 3.11 --with pytest --with pytest-asyncio coverage run --source=app.calc,app.services.receipt_list_service -m pytest tests/test_calc.py -q`

## 受け入れ要件との対応

| 受け入れ要件 | テスト |
|-------------|--------|
| 1. 氏名選択で合計金額が動的に変わる | `test_get_page_context_name_filter_changes_total` / `test_list_name_filter_updates_total` |
| 2. 補填額は行単位で控除し、一括マイナスしない | `test_contribution_floor_at_zero_when_compensation_exceeds_amount` / `test_total_is_per_row_not_global_subtraction` / `test_list_acceptance_row_level_compensation` |

---

## テストケース一覧

### `tests/test_calc.py`

#### 純粋関数: `compute_contribution`

| TC | テスト名 | 内容 |
|----|---------|------|
| 01 | `test_contribution_subtracts_compensation` | 補填 < 金額のとき `amount - compensation` |
| 02 | `test_contribution_floor_at_zero_when_compensation_exceeds_amount` | 補填 > 金額のとき 0（負にしない） |
| 03 | `test_contribution_exact_equal_is_zero` | 補填 = 金額のとき 0 |
| 04 | `test_contribution_no_compensation` | 補填 0 のとき金額そのまま |

#### 純粋関数: `compute_total`

| TC | テスト名 | 内容 |
|----|---------|------|
| 05 | `test_total_is_per_row_not_global_subtraction` | **受け入れ要件2**。行A 5000/補填6000→0 + 行B 3000/補填0→3000 = 3000。8000-6000=2000 にならない |
| 06 | `test_total_empty_rows` | 行 0 件で合計 0 |

#### 純粋関数: `month_of` / `collect_months` / `available_names`

| TC | テスト名 | 内容 |
|----|---------|------|
| 07 | `test_month_of_extracts_yyyy_mm` | `"2026-01-15"` → `"2026-01"` |
| 08 | `test_month_of_empty` | 空文字は空文字 |
| 09 | `test_collect_months_sorted_unique` | 月がソート・重複除去される |
| 10 | `test_available_names_sorted_unique` | 氏名がソート・重複除去される |

#### 純粋関数: `filter_by_names` / `sort_by_date`

| TC | テスト名 | 内容 |
|----|---------|------|
| 11 | `test_filter_by_names_empty_returns_all` | names 空リストは全件 |
| 12 | `test_filter_by_names_matches_selected` | 選択氏名のみ |
| 13 | `test_sort_by_date_ascending` | 昇順 |
| 14 | `test_sort_by_date_descending` | 降順 |

#### サービス: `ReceiptListService`

| TC | テスト名 | 内容 |
|----|---------|------|
| 15 | `test_load_rows_reads_structured_files` | structured_data を ReceiptRow に読取 |
| 16 | `test_get_page_context_filters_and_totals` | 月絞り込み + 行単位合計 + months 一覧 |
| 17 | `test_get_page_context_name_filter_changes_total` | **受け入れ要件1**。氏名選択で合計が変わる |
| 18 | `test_save_compensation_persists_to_json` | 補填額が JSON の compensation に保存される |
| 19 | `test_save_compensation_blank_becomes_zero` | 空入力は 0 として保存 |

### `tests/test_web.py`（結合テスト）

| TC | テスト名 | 内容 |
|----|---------|------|
| 20 | `test_list_month_total_matches_fixture` | 既存フィクスチャで 2026-01 の合計 3800 円を表示 |
| 21 | `test_list_acceptance_row_level_compensation` | **受け入れ要件2**。行A に 6000 補填→行は 0、合計は行B の 3000 のまま（2000 にならない） |
| 22 | `test_list_name_filter_updates_total` | **受け入れ要件1**。断片 `/list/table` で氏名絞り込み→合計が 1000 円に変わる |

## エッジケース一覧

| ケース | 対応方針 |
|--------|---------|
| 補填額が金額を超える（負） | `max(0, ...)` で 0（`test_contribution_floor_...`） |
| 補填額が空・非数値 | `_to_amount` で 0（`test_save_compensation_blank_becomes_zero`） |
| 月にデータなし | 空の表 + 合計 0 円（`get_page_context` が空 rows を返す） |
| structured_data 読み込み失敗 | `load_rows()` でスキップ（既存 `get_all_receipts` と同様） |
| compensation 対象ファイルなし | `FileNotFoundError` → 404 |
| 既存詳細修正 `PUT /{stem}` が compensation を上書きしない | `normalize()` が `{**old_data, **updates}` のため保持（結合で確認） |

## テスト実行方法

```bash
# 純粋計算 + 一覧サービスの単体テスト
uv run pytest tests/test_calc.py -v

# Web UI 結合テスト
uv run pytest tests/test_web.py -v

# 全テスト（既存との回帰確認）
uv run pytest -q

# フォーマット確認
black --check .
```
