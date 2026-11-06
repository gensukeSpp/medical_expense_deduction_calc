# Issue #51: coord_basis の値の調整（Web フォーム修正後の不整合解消）

## 目的

Web 管理画面のトグルで topmost → date にした後、同クリニックのレシートを Web フォームで修正すると、
テンプレート内の座標基準と `templates.coord_basis` が食い違う**矛盾**を解消する。
Date anchor による補正の強制（次 Issue #50）の前提となる座標系整合性を担保する。

> **方針（レビュー P1 後・案ii 採用）**: 当初「修正で `coord_basis` を `topmost` へ戻す（案i）」だったが、
> レビュー P1（`.github/reports/pr-52-review.md`）の「基底だけを切り替えると未修正フィールドが混在し、
> 保存済み OCR が date 基準なら修正フィールド自体も topmost とは限らない」を受け、**date 方向へ統一**する
> 案(ii) に変更した。`topmost` への逆変換はシフト後の絶対位置が失われ一意でないため不採用。

## 参照

- [Issue #51](https://github.com/gensukeSpp/medical_expense_deducation_calc/issues/51)
- [Issue #39 実装](../../tasks/issue_39/overview.md) — date 基準正規化・coord_basis 導入
- [Issue #42 実装](../../tasks/issue_42/overview.md) — Web admin 手動トグル（topmost→date）
- [coordinate-normalization ルール](../../.hermes/rules/coordinate-normalization.md) — coord_basis / 前方補正ガード
- [レビュー指摘 P1](../../.github/reports/pr-52-review.md) — 本仕様の案(ii) 採用理由
- `app/template_feedback.py` — `process_correction_feedback()`（修正フィードバック）＋ `_shift_correction_to_date_basis()`
- `app/db.py` — `upsert_template()` / `update_template_basis()` / `update_receipt_ocr_json_by_source()`
- `app/date_anchor.py` — `apply_date_anchor_normalization()`（`receipts.ocr_json` を date 基準で保存: acceptance 6）
- `app/services/ocr_coordinate_service.py` — Web 修正時の座標解決
- `app/web/templates/admin_templates.html` — テンプレート管理画面

## 現状の問題（ルートコーズ）

Web 修正経路（`OCRCoordinateService.process_feedback` → `process_correction_feedback`）は:

1. 修正対象レシートの `ocr_json` からフィールド座標を決める（`_resolve_coord_results`）。この座標の基準は
   レシート `ocr_json` の基準に依存し、date anchor 実行時に `receipts.ocr_json` は **date 基準で保存**される
   （`apply_date_anchor_normalization` → acceptance 6）。
2. `template_feedback.py::process_correction_feedback()` が `upsert_template()` を呼び、修正座標で
   `coords_corrections` を上書きする。
3. `coord_basis` を座標の実基準に一致させないと、テンプレート内に基準が混在し以後の抽出を誤らせる。

## スコープ

### 含むもの
- Web フォーム修正で、**date 基準テンプレートでは修正座標を date 基準へ変換**し、`coord_basis` を `date` のまま維持
- `upsert_template()` の `coord_basis` 対応（任意引数で指定可能に）
- 安全側フォールバック（date anchor 未解決時は座標を書き込まず基底も変えない）
- 変更履歴（`template_history`）への追記
- 単体テスト・結合テスト（管理画面表示の反映確認）

### 含まないもの
- Date anchor 補正の強制（#50、別 Issue）
- date → topmost の管理画面トグル UI / 逆変換基盤（本 Issue では不要。逆変換はシフト後に一意に決まらない）
- `coord_basis` を `topmost` へ戻す設計（案i。レビュー P1 により不採用）

## 受入条件

1. `coord_basis='date'` のクリニックのレシートを Web フォームで修正すると、`coords_corrections` 全フィールドが
   **date 基準で一致**し、`coord_basis` は `date` のまま（矛盾解消）。
2. 修正後に「テンプレート管理」画面（`GET /admin/templates`）へ行くと、当該クリニックが `date` 表示のまま。
3. 既存テストが全てパスする（回帰なし）。
4. date anchor 未解決（基準判定不能）時は、座標を書き込まず基底も変更しない（安全側フォールバック）。

## 設計判断

| 項目 | 決定 | 理由 |
|------|------|------|
| 修正時の基準 | Web フォーム修正は**レシート ocr_json の基準**に依拠 | `_resolve_coord_results` がレシート由来で座標を決める。ocr_json は anchor 実行時 date 基準保存（acceptance 6） |
| coord_basis の扱い | **既存が `date` なら維持**し、修正座標を date 基準へ変換 | 逆変換（date→topmost）は絶対位置喪失で一意に決まらないため。全座標を同一基準に保つ | 
| 変換方式 | 修正レシート `ocr_json` の一意な date box top-left で修正座標を減算（`shift_template_coords`） | topmost なら date へ変換、date 基準なら原点で no-op。新規基盤・スキーマ変更不要 |
| 安全側フォールバック | anchor 未解決（receipt 無し / date 候補 0・複数）は**座標を書かず基底も変えない** | 基準を判定不能なまま書くと date テンプレを破壊。レビュー P1 の「判別不能なら切り替えない」を date 側で適用 |
| `upsert_template` | 任意引数 `coord_basis` を追加（未指定なら現状維持） | 呼び出し元を壊さず疎結合。INSERT/UPDATE 両ルートで整合 |
| 履歴 | 既存 `insert_template_history`（`change_reason='user_correction'`）を利用 | 新規 SQL を増やさない |
| 管理画面 | 変更不要（`list_templates` が DB の `coord_basis` を読み表示するだけ） | 受入条件2 は DB 値が date のままで自動満たす |

## 案i と案ii の比較（フォーク解消）

| | 案i（topmost へ戻す） | 案ii（date へ統一・採用） |
|--|----------------------|--------------------------|
| 修正座標 | topmost 基準を書き込む | date 基準へ変換して書く |
| 未修正フィールド | date 基準のまま残る（混在） | date 基準のまま（一貫） |
| 保存済み OCR が date 基準の場合 | 修正座標も date 基準なのに topmost 扱い（不整合） | date 基準として一致（no-op 変換） |
| 実現に必要な基盤 | date→topmost 逆変換（絶対位置喪失で困難・スキーマ変更） | 既存の順変換（`shift_template_coords`）のみ。追加不要 |
| レビュー P1 対応 | 部分（未修正フィールドの混在は残る） | 完全（全座標・基底を一致） |

## ファイル変更サマリ（予定）

| ファイル | 変更種別 | 変更規模 |
|----------|---------|---------|
| `app/db.py` | 既存（前コミット `170bf04` で対応済み） | `upsert_template` に `coord_basis` 引数 |
| `app/template_feedback.py` | 修正（date 基準へ変換 + フォールバック） | ~40行 |
| `tests/test_feedback.py` | 改訂（date 変換・no-op・フォールバックのテスト） | ~90行 |
| `tests/test_web.py` | 改訂（管理画面が date 表示のまま） | ~35行 |
| `specs/2026-11-06-1-spec.md` | 改訂（案ii へ） | 全体 |

## 実装順序

`template_feedback.py` の date 変換ロジック → テスト改訂 → 管理画面確認 → 仕様・ルール更新 → 全テスト実行。
詳細は `tasks.md` 参照。