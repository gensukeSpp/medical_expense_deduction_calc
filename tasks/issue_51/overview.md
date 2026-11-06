# Issue #51: coord_basis の値の調整（Web フォーム修正後の不整合解消）

## 目的

Web 管理画面のトグルで topmost → date にした後、同クリニックのレシートを Web フォームで修正すると、
`templates.coords_corrections` がトグル前（topmost）基準の座標で上書きされるのに、`templates.coord_basis`
は `date` のまま残る **矛盾** を解消する。Date anchor による補正の強制（次 Issue #50）の前提となる座標系整合性を担保する。

## 参照

- [Issue #51](https://github.com/gensukeSpp/medical_expense_deducation_calc/issues/51)
- [Issue #39 実装](../../tasks/issue_39/overview.md) — date 基準正規化・coord_basis 導入
- [Issue #42 実装](../../tasks/issue_42/overview.md) — Web admin 手動トグル（topmost→date）
- [coordinate-normalization ルール](../../.hermes/rules/coordinate-normalization.md) — coord_basis / 前方補正ガード
- `app/template_feedback.py` — `process_correction_feedback()`（修正フィードバック）
- `app/db.py` — `upsert_template()` / `update_template_basis()`
- `app/services/ocr_coordinate_service.py` — Web 修正時の座標解決
- `app/web/templates/admin_templates.html` — テンプレート管理画面

## 現状の問題（ルートコーズ）

Web 修正経路（`OCRCoordinateService.process_feedback` → `process_correction_feedback`）は:

1. 修正対象レシートの `ocr_json`（通常 topmost 基準）からフィールド座標を決める（`_resolve_coord_results`）。
2. `template_feedback.py::process_correction_feedback()` が `upsert_template()` を呼び、
   `coords_corrections` をその topmost 基準座標で上書きする。
3. **しかし `upsert_template()` の `UPDATE` は `coord_basis` に一切触れない**（db.py:284-296）。

その結果、`coord_basis='date'` のまま `coords_corrections` が topmost 座標に置き換わる。
データベース内部で座標基準と `coord_basis` が食い違う = 「前方補正が topmost 基準なのに date 扱い」の基底不一致を生む。

## スコープ

### 含むもの
- Web フォーム修正で topmost 基準座標を書き込む際、`coord_basis` を `topmost` へ戻す（トグル一方通行を実質解除）
- `upsert_template()` の `coord_basis` 対応（任意引数で指定可能に）
- 変更履歴（`template_history`）への追記
- 単体テスト・結合テスト（管理画面表示の反映確認）

### 含まないもの
- Date anchor 補正の強制（#50、別 Issue）
- `coord_basis` を `date` に保ったままの修正（date 基準レシートを問題にする場合の設計は #50 側で扱う）
- 逆方向（date → topmost）の管理画面トグル UI

## 受入条件

1. `coord_basis='date'` のクリニックのレシートを Web フォームで修正すると、修正後 `coord_basis` が `topmost` になり、
   `coords_corrections` の基準と一致する（矛盾解消）。
2. 修正後に「テンプレート管理」画面（`GET /admin/templates`）へ行くと、当該クリニックが `topmost` 表示になる。
3. 既存テストが全てパスする（回帰なし）。

## 設計判断

| 項目 | 決定 | 理由 |
|------|------|------|
| 修正時の基準 | Web フォーム修正は**レシートの ocr_json 基準（実質 topmost）**に依拠 | 修正座標は `_resolve_coord_results` がレシート由来で決める。date anchor が機能しない現状では topmost が実勢 |
| coord_basis の扱い | 既存が `date` のとき**上書きで `topmost` に戻す** | 修正で書き込む座標の実基準に整合させる。片方向トグルを「修正時のみ実質解除」 |
| `upsert_template` | 任意引数 `coord_basis` を追加（未指定なら現状維持） | 呼び出し元を壊さず疎結合。INSERT/UPDATE 両ルートで整合 |
| 履歴 | 既存 `insert_template_history`（`change_reason='user_correction'`）を利用 | 新規 SQL を増やさない。トグル時と別 reason は false になるため今回は既定のまま |
| 管理画面 | 変更不要（`list_templates` が DB の `coord_basis` を読み表示するだけ） | 受け入れ条件2は DB 値を直せば自動的に満たす |

## 悩みどころ（設計フォーク）

### Q1: coord_basis の決定基準をどうするか
- **案A（採用）**: 既存 `coord_basis=='date'` のとき修正で `topmost` へ戻す。日付 anchor が効かない現状を前提に、
  修正座標を topmost とみなす（Issue の機能要件どおり）。
- 案B: 修正座標の基準をレシートの ocr_json から都度判定する。正確だが ocr_json に基準フラグが無く複雑。
- 案C: 常に `topmost` で強制（既存が topmost なら不要）。date 基準レシートを修正するケースを壊す恐れ。

→ **案A で進める**。実測で date anchor が機能していない現状では、修正座標＝topmost が安全かつ Issue要件に合致する。

## ファイル変更サマリ（予定）

| ファイル | 変更種別 | 変更規模 |
|----------|---------|---------|
| `app/db.py` | 修正（`upsert_template` に `coord_basis` 引数） | ~5行 |
| `app/template_feedback.py` | 修正（既存 basis が date なら topmost へ戻す） | ~8行 |
| `tests/test_feedback.py` | 修正（`coord_basis` リセットのテスト追加） | ~40行 |
| `tests/test_web.py` | 修正（管理画面表示反映のテスト追加） | ~30行 |

## 実装順序

`db.py` の `upsert_template` 拡張 → `template_feedback.py` で回帰 → テスト → 管理画面確認 → 全テスト実行。
詳細は `tasks.md` 参照。
