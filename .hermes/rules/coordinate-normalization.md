# 座標正規化 (Coordinate Normalization) と date anchor

> AGENTS.md から切り出した doc。Issue #39 (日付基準による OCR box 座標正規化) で追加。
> 座標正規化の基準・日付 anchor の判定条件・DB coord_basis・閾値が変わるたびに更新する。

## 概要

OCR 後、raw_data.json の box 座標を正規化する。既定は**topmost/leftmost 基準**（全 box の最小 x/y を原点）。
clinic のテンプレートに `date` 座標が学習済みで、同一 clinic/layout の処理時に日付候補が**信頼できれば**、
**選択した date box の左上を基準**に全 box を再正規化する（date 基準）。

## 正規化の判定順

```
normalize_coordinates()          … topmost/leftmost 基準（従来・常に実行）
  -> 構造化 parse (date/clinic)
  -> apply_date_anchor_normalization() (画像OCR/watcher 経路のみ)
```

- **date 基準へ切替るのは** 画像OCR経路（`ImageProcessingService.process`）と watcher 経路
  （`ReceiptProcessor._sync_process`）のみ。
- **`process_input_json`（`--input-json` 単体）は無変更**（従来 topmost 基準）。後方互換。
- low-confidence gate（0.8）と前処理 retry は既存挙動を維持。anchor は低Confidence判定**後**に実行。

## date anchor の解決条件（`date_anchor.resolve_date_anchor`）

1. `parse_date()` で構造化 date を正規化。`None` なら解決しない。
2. `parse_date(text) == structured_date` の OCR 候補を列挙。
   - 候補 0 件 → 従来方式。
3. **template に date coords が未学習 → 従来方式**（受入条件1）。
4. 候補が 1 件 → 採用（template basis が date でも可）。
5. 候補が複数 → template が `topmost` 基準のときのみ、中心距離が
   `proximity_threshold`（50px）以内で**一意に**近い box を採用。競合・曖昧は従来方式。

## 使用する閾値

| 項目 | 値 | 出典 |
|------|-----|------|
| proximity（候補絞り込み） | 50.0 px | `DEFAULT_PROXIMITY_THRESHOLD` / `resolve_date_anchor` |
| テキスト照合 | `parse_date()` ISO 一致 | 表記ゆれ（`/`・`年`・`−`・和暦）を同一比較 |
| 低 Confidence ゲート | 0.8（既存） | anchor は低Confidence判定後 |

## DB: `templates.coord_basis`

`templates` に座標基準を識別する列 `coord_basis TEXT NOT NULL DEFAULT 'topmost'` を持つ。

- `'topmost'`: 従来基準（date 未学習・学習直後）。
- `'date'`: 移行済み（date 基準）。

| 動作 | 関数 | 備考 |
|------|------|------|
| 移行前 coords を履歴保存 + date 基準へ更新 | `db.update_template_basis` | 同一トランザクション。`change_reason='basis_migration'` |
| 二重変換防止 | `update_template_basis` | `coord_basis=='date'` なら no-op |
| DB 保存 box を date 基準へ同期 | `db.update_receipt_ocr_json_by_source` | 二重登録なし（最も一致する row のみ更新）|

### 既存 DB の移行

`db_migrations` が `PRAGMA table_info(templates)` で `coord_basis` の有無を確認し、
無ければ `ALTER TABLE templates ADD COLUMN coord_basis TEXT NOT NULL DEFAULT 'topmost'` を実行（idempotent）。

## 手動切替（Web admin, Issue #42）

自動遷移が実データで効かない場合（`resolve_date_anchor()` が同日付候補 2 件の曖昧で None → 遷移しない）の
**検証手段**として、Web 管理画面から `coord_basis` を topmost → date へ**手動で一方向切替**できる。

- 画面: `GET /admin/templates`（clinic と最新 template 一覧。coord_basis / date box 学習有無を表示）
- 切替: `POST /admin/templates/{clinic_id}/basis`（body `{"basis":"date"}`）
- ロジック: `app/services/template_admin_service.py::TemplateAdminService`（server.py は薄いルーティングのみ）

### シフト計算の共通化（Q2 案A）

`app/coord_normalizer.shift_template_to_date_basis(coords) -> Optional[dict]` が、**template 自身の date box の
min-x / min-y を原点**に全 box をシフトする。**自動遷移（`date_anchor.apply_date_anchor_normalization`）と
手動切替（admin）の両方**がこの関数を呼ぶため、両方式で coords が一致する（検証用途で重要な保証）。

- date box 未学習 / 不正（2D 座標でない）→ `None`（→ 呼び出し側で 409）。
- 不正座標への防御: 点が数値（int/float、bool 除く）であることを検証（`_is_numeric_point`）。

### ガード（HTTP マトリクス）

| 条件 | HTTP | レスポンス |
|------|------|-----------|
| `db_path` 未設定 | 503 | "DB が設定されていません"（管理は DB 必須） |
| `basis != 'date'` | 400 | InvalidBasisError |
| clinic_id 不明 | 404 | `get_clinic_by_id()`（db.py・repository に今回追加） |
| clinic 存在・template 未学習 | 409 | "テンプレート未学習のため切替できません" |
| 既に `coord_basis=='date'` | 200 | `{"status":"noop","reason":"already_date"}`（冪等・history 追記なし） |
| date box 未学習 / coords 不正 | 409 | "date box 未学習のため切替できません"（**必須ガード** — 未学習を強制 date 化すると前方補正が崩れる: Issue #40） |
| 移行成功 | 200 | `{"status":"updated","coord_basis":"date"}`（`change_reason='manual_basis_toggle'` で history 1 件） |
| 競合（他経路で移行済み） | 200 | `update_template_basis()` が False → no-op として扱う |

- 移行処理は既存 `db.update_template_basis()` を再利用（history 保存 + coords 差し替え + `coord_basis='date'` を 1 トランザクション。
  既に date なら False＝二重変換ガード内蔵）。**新規 SQL を書かない**。
- 移行は **template のみ**変更。既存 receipt / `receipts.ocr_json` の再正規化は行わない（切替後に画像を再投入して
  date 基準の補正を確認する運用）。
- date → topmost の逆方向切替は対象外（一方向のみ）。曖昧性判定の緩和も後続 Issue。

## 設計判断（重要な制約）

- **anchor 後は構造化 parse を再実行しない。** `process_input_json` は呼び出しごとに新しい receipt 行を
  insert するため、再解析すると**同一画像のレシートが二重登録**される。構造化値はテキスト抽出に由来し
  座標基準に非依存のため、再解析は不要。DB の座標同期は `update_receipt_ocr_json_by_source` で行う。
- 基準切替は一度だけ（`coord_basis=='date'` ガード）。旧座標は `template_history` に残る。

## 前方テンプレート補正と coord_basis（Issue #40）

`ExtractionService._apply_template_corrections()`（`structural_parser.py`）は、受領時点の raw は
**topmost 基準**なのに、`coord_basis=='date'` の template 座標を照合する基底不一致がある。これを防ぐため:

- `coord_basis = template.get("coord_basis") or "topmost"` を取得。
- `coord_basis == 'date'` のとき、**座標ベースのフィールド上書き**（`search_fields_by_proximity` による
  各フィールド値の引き直し）を**スキップ**する（誤上書き防止が目的。値はテキスト抽出（LLM）にフォールバック）。
- clinic 名の正しい名への上書きと新規 clinic 作成は、coord_basis に関係なく**常に**実行。
- `coord_basis != 'date'`（`'topmost'` または旧データ）なら従来どおり座標上書きを実行。

※ `coord_basis` は `NOT NULL DEFAULT 'topmost'` のためスキーマ上 NULL は入らない。`or "topmost"` は防御用。

| 処理 | coord 使用 | date 基準での扱い |
|------|-----------|-----------------|
| ① clinic 特定（完全一致 / テキスト類似度） | 部分（レイアウト除く） | 残す |
| ② 座標ベースのフィールド上書き（`search_fields_by_proximity`） | あり | **スキップ** |
| ③ clinic 名の正しい名への上書き | なし | 残す（常に実行） |
| ④ 新規 clinic 作成 | なし | 残す |

- レイアウトマッチング（`match_template_by_layout`）も `coords_corrections` を使うが、name 解決で
  template が見つからない場合のみ走る分岐のため、移行済み clinic では name 解決で得られれば入らない（実質回避）。
- 詳細は `specs/2026-10-08-spec.md`（Issue #40）。

## 座標系の一貫性

| 保存先 | 基準 | 備考 |
|--------|------|------|
| `raw_data.json` | date 基準（anchor 確定時） | `normalize_coordinates_by_anchor` で上書き |
| `receipts.ocr_json`（DB） | date 基準 | `update_receipt_ocr_json_by_source` で更新 |
| `structured_data.json` | 座標基準に非依存（テキスト抽出値） | 再解析なし |
| `templates.coords_corrections` | date 基準（切替済み） | `coord_basis` で識別 |
| OCR 未学習 clinic / `--input-json` | topmost 基準（従来） | anchor 解決しない |

## 実装場所

- 候補照合・anchor 解決・適用: `app/date_anchor.py`
- box 基準再正規化・座標シフト: `app/coord_normalizer.py`
- template 基準切替・DB 同期: `app/db.py`
- 経路統合: `app/services/image_processing_service.py`, `app/services/receipt_processor.py`
