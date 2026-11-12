---
created: 2026-11-12
updated: 2026-11-12
---

# Issue #54 仕様: 抽出データの月別一覧表示と合計計算

医療費控除額の算出（当面は合計額まで）を目的に、抽出データ（`*-structured_data.json`）を
ブラウザ上で月別に一覧表示し、各月の合計金額を計算して表示する。
ユーザーは表の空白マスへ「保険で補填した額」を入力でき、補填額は **その行（診察・治療・処方）単位で**
金額から控除される。

計画・タスクは `tasks/issue_54/`（overview / architecture / tasks / test_plan）。

> ファイル名規約の更新: これまで日付起点（`specs/YYYY-MM-DD-spec.md`）だった spec は、
> 本 Issue 以降 **機能起点**（`specs/{機能}-spec.md`）で作成する。

## 1. 画面と操作

| 要素 | 仕様 |
|------|------|
| トップページ (`/`) | 「月別一覧（計算）」リンク（`/list`）を追加 |
| 一覧ページ (`GET /list`) | 月ナビゲーション + 氏名フィルタ + 表 + 当該月の合計 |
| 月切替 | データ中に存在する月（`YYYY-MM`）のみを列挙。 Prev / Next リンクで前月・翌月へ移動。既定月は今年の1月（データに無い場合はデータ先頭） |
| 日付列の並び替え | 表タイトル部のトグルで昇順 / 降順を切り替える（`sort=asc|desc`） |
| 氏名フィルタ | 氏名ごとのチェックボックス。選択した氏名の行のみを表示し、合計も対象行だけに再計算する |
| 補填額入力 | 金額列の右隣の入力欄。変更（change）時に保存し、合計を非同期再計算する |
| 合計表示 | 「今月の合計: N 円」。表示対象行の控除対象額の総和 |

## 2. 計算仕様（受け入れ要件2の核心）

補填額は **行単位** で控除する。月合計を「全金額の合計 − 全補填額の合計」で求めてはならない。

```
行 contribution = max(0, amount - compensation)
月合計 total     = Σ (行 contribution)
```

- 補填額がその行の金額を超える場合、その行の contribution は **0**（負にならない）。他の行を減額しない。
- 例: 行A `5000 / 補填6000` → 0、行B `3000 / 補填0` → 3000 のとき月合計は **3000**
  （`8000 − 6000 = 2000` にはしない）。
- 補填額の入力が空・非数値の場合は **0** として扱う（`parse_amount` が None → 0）。

## 3. データソースと永続化

- 一覧の読み取りは `*-structured_data.json` のみ（DB は使わない）。
  未修正の領収書は DB に存在しないため、JSON 起点に統一する（`tasks/issue_54/overview.md` の決定を参照）。
- 補填額は `structured_data.json` の **`compensation`** キーに保存する（DB スキーマ変更・マイグレーションはなし）。
  - 既存の詳細修正 `PUT /{file_stem}` は `{**old_data, **updates}` でマージするため `compensation` を保持する。
  - 責務上、補填額は修正履歴（`corrections`）ではなく「計算用の補助値」であり JSON のみに書く。

## 4. モジュール構成

| モジュール | 責務 |
|-----------|------|
| `app/calc.py`（新規） | 純粋計算。`ReceiptRow` dataclass と `compute_contribution` / `compute_total` / `month_of` / `collect_months` / `available_names` / `filter_by_names` / `sort_by_date`。ファイル I/O・DB 非依存 |
| `app/services/receipt_list_service.py`（新規） | `load_rows()`（`ReceiptFileRepository` 経由の読取）、`get_page_context()`（表示文脈の構築）、`save_compensation()`（補填額の永続化） |
| `app/web/server.py`（修正） | `ReceiptListService` の DI 追加と下記 3 ルートの追加 |
| `app/web/templates/list.html`（新規） | 一覧ページ全文（htmx swap 領域を含む） |
| `app/web/templates/list_table.html`（新規） | htmx swap 対象の断片（月ナビ・氏名フィルタ・表・合計） |

### ルーティング

| メソッド / パス | 役割 |
|------------------|------|
| `GET /list?month&sort&names` | 一覧ページ全文 |
| `GET /list/table?month&sort&names` | 一覧断片（htmx swap 対象） |
| `PUT /list/items/{file_stem}/compensation` | 補填額を保存し、一覧断片を返す（htmx） |

- `names` はカンマ区切り文字列と複数パラメータの両方を受け付ける（重複は除去）。
- **PUT では `request.query_params` が空になる**（htmx は include した値をフォーム形式で送る）。
  そのため文脈の構築は GET ではクエリ、PUT では `await request.form()` を読む。
- 補填額の保存対象 JSON が無い場合は 404。

## 5. htmx の配線（HTML 上の制約）

- htmx は CDN（`https://unpkg.com/htmx.org@1.9.12`）を利用（`detail.html` と同じ。新規 Python 依存なし）。
- **HTML は `<form>` の入れ子を禁じる**。そのため氏名フィルタ用 form と表（行ごとの入力）は兄弟要素にし、
  行の入力は form ではなく `hx-put` + `hx-include="#list-state"` で送信する。
- `#list-state`（非表示 div）に `month` / `sort` / `names`（カンマ区切り）を持たせ、行の補填額保存時に include する。
- 並び替えトグルの状態は `hx-vals` で明示的に渡し、古い値が混入しないようにする。

## 6. テスト

- `tests/test_calc.py`（新規）: 純粋計算の単体テスト + `ReceiptListService` の単体テスト（20 件）。
- `tests/test_web.py`（追記）: `/list` 表示、月合計（既存フィクスチャで 3800 円）、断片の独立性、
  氏名フィルタで合計が変わる、**行単位控除**（5000/6000 → 0、行B 3000 → 合計 3000）、補填額の JSON 永続化、
  404、詳細修正が `compensation` を保持すること。

### 受け入れ要件との対応

| 受け入れ要件 | 担保するテスト |
|-------------|---------------|
| 1. 氏名を選択すると合計金額が動的に変更される | `test_get_page_context_name_filter_changes_total` / `test_list_name_filter_updates_total` |
| 2. 補填額は行単位で控除し、一括マイナスしない | `test_contribution_floor_at_zero_when_compensation_exceeds_amount` / `test_total_is_per_row_not_global_subtraction` / `test_list_acceptance_row_level_compensation` |

## 7. エラーハンドリング

| シナリオ | 対応 |
|---------|------|
| structured_data の読み込み失敗 | `load_rows()` で該当ファイルをスキップ |
| 補填額が空・非数値 | 0 として扱い、JSON にも 0 で保存 |
| データが無い月を指定 | 空の表 + 合計 0 円（エラーにしない） |
| 補填額の保存対象が無い | 404 |

## 8. スコープ外

- 年を指定するカレンダー（対象年はデータから導出）
- 年収に応じた控除額の計算（本 Issue は合計額まで）
