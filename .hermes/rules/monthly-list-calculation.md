# 月別一覧と合計計算（Issue #54）

医療費控除額の算出（当面は合計額まで）用の Web 一覧ページ。
詳細仕様は `specs/monthly-list-total-spec.md`、計画は `tasks/issue_54/`。

## 画面

| パス | 内容 |
|------|------|
| `GET /` | トップページ。「月別一覧（計算）」リンク（`/list`） |
| `GET /list?month&sort&names` | 一覧ページ全文（月ナビ + 氏名フィルタ + 表 + 合計） |
| `GET /list/table?month&sort&names` | 一覧断片（htmx swap 対象） |
| `PUT /list/items/{file_stem}/compensation` | 補填額を保存し、一覧断片を返す（htmx） |

- htmx は CDN（`https://unpkg.com/htmx.org@1.9.12`）。Python 依存は増やさない。
- 月はデータ中に存在する `YYYY-MM` のみ列挙する。`month` 未指定時は今年の1月（データに無ければデータ先頭）。
- `sort` は `asc`（既定）/ `desc`。`names` はカンマ区切り文字列と複数パラメータの両方を受け付ける。

## 計算（重要）

補填額は **行単位** で控除する。月合計を「金額の合計 − 補填額の合計」で求めてはならない。

```
行 contribution = max(0, amount - compensation)
月合計 total     = Σ contribution
```

例: 行A 5000/補填6000 → 0、行B 3000/補填0 → 3000 のとき月合計は **3000**（2000 ではない）。
実装は `app/calc.py` の `compute_contribution` / `compute_total`（純粋関数）。

## データソースと永続化

- 読み取りは `*-structured_data.json` のみ（**DB は使わない**）。
  未修正の領収書は DB 行が存在しないため、JSON 起点に統一している。
- 補填額は JSON の `compensation` キーに保存する（DB スキーマ変更・マイグレーションなし）。
  既存の詳細修正 `PUT /{file_stem}` は `{**old_data, **updates}` でマージするため `compensation` を保持する。
- 空・非数値の補填額は 0 扱い（`parse_amount` が None → 0）。

## モジュール

| モジュール | 責務 |
|-----------|------|
| `app/calc.py` | 純粋計算（I/O・DB 非依存） |
| `app/services/receipt_list_service.py` | 行の読取・表示文脈の構築・補填額の保存 |
| `app/web/server.py` | `ReceiptListService` の DI と 3 ルート |
| `app/web/templates/list.html` | 全文ページ |
| `app/web/templates/list_table.html` | htmx swap 対象の断片 |

## htmx 実装上の注意

- **HTML は `<form>` の入れ子を禁じる**。氏名フィルタ用 form と表（行ごとの入力）は兄弟要素にする。
  行の入力は form ではなく `hx-put` + `hx-include="#list-state"` で送信する。
- **htmx の PUT では `request.query_params` が空**（include した値はフォーム形式で送られる）。
  文脈の構築は GET ではクエリ、PUT では `await request.form()` を読む。
- `#list-state`（非表示 div）に `month` / `sort` / `names`（カンマ区切り）を持たせる。
  並び替えトグルの状態は `hx-vals` で明示的に渡し、古い値が混入しないようにする。

## spec ファイル命名

spec は **機能起点** で命名する（`specs/{機能}-spec.md`）。日付起点（`specs/YYYY-MM-DD-spec.md`）は
Issue #54 以前のもの。
