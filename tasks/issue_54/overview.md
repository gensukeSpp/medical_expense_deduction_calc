# Issue #54: 抽出データの月別一覧表示と合計計算

## 目的

医療費控除額の算出（本実装では **合計額まで**）を目的に、抽出データ（`*-structured_data.json`）をブラウザ上で **月別** 一覧表示し、各月の合計金額を計算して表示する。ユーザーは表の空白マスへ「保険で補填した額」を入力でき、補填額は **その行（診察・治療・処方）単位で** 金額から控除される。

## 機能要求（Issue 本文より）

- トップページ (`/`) から「一覧ページ」のリンクで遷移
- 表形式で左から **日付 / クリニック名(調剤薬局名) / 氏名 / 金額 / 空白（保険補填額入力欄）**
- **日付** で昇順・降順を切り替え
- **氏名** で絞り込み
- **月別** にページ表示（1月から始まり、ページネーションで切り替え）
- 各月ページ下部に **その月の合計金額**（補填分は対象(左隣)からマイナス。負になる行は 0 扱い）
- 選択した氏名に応じて合計金額を変更

## 機能要件

- `*-structured_data.json` を読み込み各値を一覧ページへ表示
- 表タイトル部（日付・氏名）に昇順/降順トグル、氏名選択チェックボックス
- 氏名選択で合計額を表示分の金額に更新（**htmx による非同期**）
- 金額右隣の空白マスは金額入力ボックス（保険補填額の永続化）
- 1月から始まり、月ページネーションで切り替え

## 受け入れ要件

1. 氏名を選択すると合計金額が動的に変更される
2. **保険で補填した額は、あくまでその診察・治療・処方（= その行）に対して計算する**。
   **単純に月合計から一括マイナスしない** 計算とテストを実装する。
   → 行ごとに `対象額 = max(0, 金額 - 補填額)` を求め、月合計はそれらの総和。
   ※ 例: `行A 5000/補填6000 → 0`, `行B 3000/補填0 → 3000` のとき月合計は **3000**（`8000-6000=2000` ではない=一括引きしない）。

## スコープ外

- 年を指定するカレンダー（今回は「今年の1月〜12月」のみ対象）。→ 対象年はデータから導出
- 年収に応じた控除額の計算（本 Issue は合計額まで）

## 参照

- [要件定義書.md](../../要件定義書.md)
- `app/web/server.py` — FastAPI ルーティング（編集）
- `app/web/templates/index.html` — トップページ（一覧リンク追加）
- `app/services/receipt_file_repository.py` — JSON I/O（再利用）
- `app/output.py` — `write_json_atomic`（再利用）
- `app/normalization.py` — `parse_amount` / `parse_date`（再利用）
- `app/services/receipt_service.py` — Web UI 用オーケストレーター（参考）
- `tasks/issue_32/` — 兄弟タスクの 4 ファイル構成テンプレート

---

## 設計方針（対策の取捨選択）

| 観点 | 決定 | 理由 |
|------|------|------|
| htmx | **CDN 読み込みを流用**（`detail.html` が `https://unpkg.com/htmx.org@1.9.12` を使用） | 新規依存なし。非同期更新は `hx-include="closest form"` + 断片テンプレートで実現 |
| データソース | `*-structured_data.json` のみ読み取り（**DB は使わない**） | 機能要件どおり。Web UI の表示は既存フローでも JSON が起点。DB 読み込みは不採用: ① `receipts` 行は処理時か Web 修正時のみ作られるため**未修正・JSON のみの領収書は DB に存在せず一覧から漏れる**、② `normalized_json`（DB）と JSON ファイルは別々に更新され**同期が保証されない**、③ 月・氏名で絞るには `normalized_json` を全行 `json.loads` して Python で filter するしかなく、SQLite 検索の恩恵が無い。領収書規模ではファイル読みの方が簡単・整合的 |
| 補填額の永続化 | structured_data.json に `compensation` キーを追加し保存（**`normalized_json` へは書かない**） | 既存 `ReceiptNormalizer.normalize` が `{**old, **updates}` なので通常更新と共存。DB の `normalized_json` へ書くと①DB 行が無い領収書で書けず JSON と乖離する、②補填額は計算用の補助データであり修正履歴（`corrections` と紐づく記録）の責務でない。JSON のみに書くのが責務としても安全 |
| 計算ロジック | 新規 `app/calc.py` に **純粋関数** で実装 | SRP・単体テスト容易。`compute_contribution` = `max(0, amount - compensation)`（受け入れ要件2の核心） |
| 一覧データ整形 | 新規 `app/services/receipt_list_service.py`（SRP） | ファイル読取 + 補填額保存の責務を分離。`ReceiptService` には触れない（影響を限定） |
| ルーティング | `server.py` に `/list`（全文）/ `/list/table`（断片）/ `PUT /list/items/{stem}/compensation` を追加 | 既存 `/`, `/admin/templates`, `/{stem}`, `PUT /{stem}` は不変 |
| 月の範囲 | データ中に存在する月（`YYYY-MM`）を収集し、デフォルトは **今年1月** | スコープ外「年カレンダー」を踏まえ年選択は作らない。1月開始の旨に沿う |
| 表の値 | 日付はそのまま表示（`data["date"]`）、金額は `parse_amount`、補填額は `parse_amount` | 既存正規化関数を再利用し新規マッチャを発明しない |

---

## ファイル変更表

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `app/calc.py` | **新規** | `ReceiptRow` dataclass + 純粋計算関数（contribution / total / filter / sort / month 系） |
| `app/services/receipt_list_service.py` | **新規** | 一覧ページ向けデータ構築 + 補填額保存（SRP） |
| `app/web/templates/list.html` | **新規** | 一覧ページ全文（月ナビゲーション + 表領域） |
| `app/web/templates/list_table.html` | **新規** | 一覧断片（氏名フィルタ + 表 + 合計）※ htmx swap 対象 |
| `app/web/server.py` | 修正 | 3 ルート追加 + `ReceiptListService` DI |
| `app/web/templates/index.html` | 修正 | 「月別一覧（計算）」リンク追加 |
| `tests/test_calc.py` | **新規** | 純粋計算関数の単体テスト |
| `tests/test_web.py` | 修正 | `/list` 表示・氏名フィルタ・補填額保存・合計の結合テスト |

## 実装順序（推奨）

Task 1（calc.py）→ Task 2（list_service）→ Task 3（server ルート）→ Task 4（テンプレート群）→
Task 5（index リンク）→ Task 6（テスト）→ 全テスト実行

詳細は `tasks.md` を参照。

## リスク・論点

- 入力欄の補填額は `parse_amount` で整数化。空欄・不正は 0 扱い（単体テストで担保）。
- htmx の補填額 PUT は `hx-include="closest form"` で月・氏名・全補填額を送るため、フィルタ状態を保ったまま合計を再描画できる（`list_table.html` を swap）。
- 既存 PUT `/{stem}`（詳細修正）は `ReceiptNormalizer.normalize` が `{**old_data, **updates}` のため `compensation` は保持される（上書きしない）。単体/結合テストで確認。
- 新規 `tests/test_calc.py` は .gitignore の `test_*.py` の影響を受ける可能性があるため、`git status --short` で確認してから `git add`（必要なら `-f`）。
- **DB 読み込みは採用しない（決定）**: 一覧・詳細を含む既存 Web 表示は JSON 起点であり、DB 移行は整合性リスク（未修正領収書の漏れ・`normalized_json` と JSON の乖離）を増やすだけで効率化にはならない。SQLite は TEXT の JSON ブロブ内をインデックスで絞れないため、月・氏名フィルタの恩恵が無い。最適化が必要なら `get_page_context` の毎回全件読み直しへの対処（リクエスト内キャッシュ等）で十分。
