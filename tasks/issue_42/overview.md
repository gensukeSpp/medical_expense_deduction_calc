# Issue #42: coord_basis の手動切替(topmost -> date) を Web UI に追加

## 目的

Date 基準による座標正規化（#39）の自動遷移は、実データ検証の結果 **`coord_basis` が常に
`'topmost'` のままで `'date'` に遷移しない** ことが確認された（`data/verify-01.db` の
`なの花薬局札幌二十四軒店`: date box 学習済みなのに 1 レシート内に date 候補が 2 件あり
`resolve_date_anchor()` が曖昧と判定 → 自動遷移が常に None）。

検証段階のアプリであるため、**Web フォームから手動で coord_basis を topmost → date へ切り替え**、
date 基準による補正（前方補正・座標検索）の挙動を検証可能にするのが本 Issue の目的。

## 参照

- [Issue #42](https://github.com/gensukeSpp/medical_expense_deducation_calc/issues/42)
- [Issue #39](../../tasks/issue_39/overview.md) — date 基準正規化の導入（`coord_basis` 列・自動遷移）
- [Issue #40](../../tasks/issue_40/overview.md) — date 基準移行後の前方補正の基底不一致（coord_basis ガード）
- `.hermes/rules/coordinate-normalization.md`
- `app/db.py` — `update_template_basis()`（既存の一括移行関数、再利用）・`get_latest_template_by_clinic()`・`get_all_templates_with_names()`
- `app/coord_normalizer.py` — `shift_template_coords()`（再利用）
- `app/date_anchor.py` — `apply_date_anchor_normalization()`（template シフト計算の参照実装、行 226-236）
- `app/web/server.py` + `app/web/templates/` — Web 層（FastAPI + Jinja2）

## スコープ

### 含むもの
- `GET /admin/templates` : clinic と最新 template の一覧（coord_basis / date box 学習有無を表示）。
- `POST /admin/templates/{clinic_id}/basis` : body `{"basis": "date"}` で最新 template の
  `coord_basis` を date へ更新（`template_history` 記録 + coords シフト + `coord_basis='date'`）。
- `index.html` からの管理画面リンク追加。
- ガード: date box **学習済み**の template のみ切替許可（未学習は 409）。既に date なら冪等 no-op。
- Web 統合テスト（`tests/test_web.py` 追記）とシフト補助関数の単体テスト（`tests/test_coord_normalizer.py` 追記）。

### 含まないもの（後続 TODO）
- date 基準の**逆方向**（date → topmost）切替（本 Issue は topmost → date の一方向のみ）。
- `resolve_date_anchor()` の曖昧性判定の緩和（自動遷移が効かない根本原因の修正は別 Issue とする。
  本 Issue は検証手段の提供が目的）。
- 既存 receipt / `receipts.ocr_json` の再正規化・更新（手動切替は **template のみ**変更。既存 raw に影響しない）。
- フル parse 再実行・DB スキーマ変更（`coord_basis` 列は #39 で存在済み。migration 不要）。

## 対応方針（確定）と相談事項

### 対応方針（確定）

1. **ガード**: `templates` の最新 template が date box（`coords_corrections['date']`）を
   **学習済み（is not None）**の場合のみ切替許可。未学習・不正座標は 409。
2. **座標シフト**: template 自身の date box 左上（min-x / min-y）を原点に
   `shift_template_coords(coords, offset_x, offset_y)` で全 box をシフト
   （`apply_date_anchor_normalization()` 行 226-236 と同一ロジック。template 基準のシフトであり
   raw の anchor offset ではシフトしない — Issue #39-4）。
3. **移行処理**: 既存 `app/db.update_template_basis(db_path, clinic_id, new_coords,
   changed_fields=list(coords.keys()), history_reason="manual_basis_toggle")` をそのまま使用
   （`template_history` に移行前 coords 保存 → coords 差し替え → `coord_basis='date'` を一括。
   既に date なら no-op＝二重変換ガードは関数内蔵）。
4. **冪等性**: 既に date の template へ POST した場合はエラーにせず
   `{"status": "noop", "reason": "already_date"}` を返す（受入条件 4）。
5. **管理画面一覧**: `app/db.get_all_templates_with_names()` を利用（clinic 名 join・最新版のみ・
   coords JSON デコード済み）。date box 学習有無と coord_basis を表示し、切替フォームは
   「date box 学習済み かつ 未移行」の clinic のみ表示する。

### 相談事項（実装前に確認）

#### Q1: 切替ビジネスロジック（ガード + シフト計算 + 移行呼び出し）の置き場所

- **案A（推奨）**: `app/services/template_admin_service.py` に新設の `TemplateAdminService` へ。
  server.py は薄いルーティングのみになり、SRP（AGENTS.md の Service layer / DI 規約）に沿う。
  単体テストもしやすい。ファイル 1 つ増えるが ~60 行。
- **案B**: `app/web/server.py` のルート内に直接実装。変更ファイルは減るが、ビジネスロジックが
  Web 層に混入する（既存 `ReceiptService` への委譲パターンと不整合）。

#### Q2: template シフト計算の共通化

- **案A（推奨）**: `shift_template_to_date_basis(coords)` を `app/coord_normalizer.py` に追加し、
  `date_anchor.py` の自動移行と admin 手動切替の**両方**から呼ぶ。シフト意味論（「template 自身の
  date box を原点に」）が 1 箇所に集約され、手動切替と自動遷移で coords がズレないことを保証できる
  （検証用途の本 Issue ではこの一致が重要）。date_anchor.py は同値リファクタ（動作不変）。
- **案B**: `date_anchor.py` は触らず、admin 側に同じ 8 行程度の計算を複製。date_anchor.py の
  回帰リスクはゼロだが、シフト計算が 2 箇所に分かれ将来の変更で乖離し得る。

> **推奨: Q1=案A, Q2=案A で進めて問題なければ。** 反対がなければ本計画はこの前提で記載する。

## 受入条件

1. Web UI から「date box 学習済み」の clinic の template を topmost → date に手動切替できる。
2. 切替後、`templates.coord_basis` が `'date'` になり、`template_history` に移行前の coords と
   `change_reason='manual_basis_toggle'` が記録される。
3. date box が未学習の clinic は切替を試みても失敗し、409 エラーが返る（誤書き換えしない）。
4. 切替は二重変換させない（既に date なら no-op / 冪等）。
5. `uv run pytest`（既知の既存失敗のみ）でチーム既知の失敗以外は通過。

## 未確定事項（実装時に決めない・現状維持）

- シフト計算の閾値等の新規パラメータは導入しない。`apply_date_anchor_normalization()` の
  オフセット導出（template date box の min-x/min-y）をそのまま踏襲する。
- `coord_basis` 列・`template_history` スキーマは #39 のまま変更しない（migration 不要）。
- 手動切替後の **receipt / ocr_json の再正規化は行わない**（template のみの変更。既存レシートの
  raw は topmost 基準のまま）。検証時は切替後に画像を再投入して date 基準の補正を確認する運用とする。

## 使用する技術・制約

- 判定は `template.get("coord_basis") or "topmost"`、date box 学習有無は `coords.get("date") is not None`
  （`date_anchor.py` の既存判定と同一）。
- 移行は `app/db.update_template_basis()`（history 保存 + coords 差し替え + basis 更新を 1 トランザクション。
  既存の二重変換ガード付き）を再利用。**新規に SQL を書かない**。
- シフトは `app/coord_normalizer.shift_template_coords()` を再利用（新規は LOI 補助関数 1 つのみ）。
- Web 層は `app/web/server.py` + `app/web/templates/`（FastAPI + Jinja2）。ルート登録は既存
  `/{file_stem}` 系より前に（パス形状は競合しないが安全側）。
- `db_path=None`（DB 未設定）の場合は管理機能を 503 で無効化（既存の JSON-only フォールバックに
  依存しない。管理機能は DB 必須）。
- テストは `tests/test_web.py`（tracked）へ追記。シフト補助関数は `tests/test_coord_normalizer.py`
  （tracked）へ追記。新規テストファイルは作らない（`.gitignore` の `test_*.py` による -f 回避）。
