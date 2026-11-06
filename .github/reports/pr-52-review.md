# PR #52 レビュー

## レビュー記録

- PR: [#52 fix #261106: Issue #51 coord_basis の不整合の修正](https://github.com/gensukeSpp/medical_expense_deduction_calc/pull/52)（Draft）
- 比較対象: `origin/main...HEAD`（`main` ref は存在せず、`origin/main` は `2302f69`）
- Issue: #51 は PR の目的と一致。受け入れ条件と説明を確認。#50 も PR 本文で関連する後続 Issue として明示されているため確認（Open、別スコープ）。
- タスク: `tasks/issue_51/overview.md`、`tasks.md`、`architecture.md`、`test_plan.md` を確認。PR 本文に明示された #50 の `tasks/issue_50/` 資料も後続機能の文脈として確認（作業ツリー上の未追跡ファイルで、PR 変更には含まれない）。
- 仕様・設計: `specs/2026-11-06-1-spec.md`、前提となる `specs/2026-11-02-spec.md`、関連する `docs/architecture/2026-10-27-architecture.md` と `2026-11-02-architecture.md` を確認。`docs/architecture/2026-11-06-architecture.md` も確認したが、作業ツリー上の未追跡ファイルで PR 差分には含まれない。PR 関連資料が存在しないため省略した項目はない。
- 知識グラフ: `.crg/graph.db` は 2026-11-02 更新。`graph` CLI が環境に存在せず、指定の stats/build を実行できなかったため、グラフの更新・参照はできず、ソースコードで影響範囲を確認。
- 確認範囲: 製品コード、テスト、関連資料を確認。`AGENTS.md` と `.hermes/` は指定されたレビュー対象外として指摘対象にしていない。
- テスト: `uv run pytest tests/test_feedback.py tests/test_web.py -q` — 36 passed。

## 総評

Issue #51 の目的に沿って `upsert_template()` に basis 指定を追加し、Web 修正時に `date` を `topmost` へ戻す実装です。ただし、座標データ全体を同じ基準へ変換せずに basis だけを切り替えるため、修正後にテンプレート内で座標基準が混在し、以後の抽出を誤らせます。

## 良い点

- `coord_basis=None` では既存 UPDATE の basis を維持し、既存呼び出しとの互換性を保っています。
- basis リセットと管理画面表示を確認するテストが追加されています。

## 指摘事項

### [P1] 座標全体を変換せず `coord_basis` だけ `topmost` に変更している

- 該当箇所: `app/template_feedback.py:73-81`
- `updated_coords` は既存座標をコピーし、修正されたフィールドだけを置き換えます（74-76行）。一方、既存 basis が `date` なら、座標全体を変換せず `coord_basis` を `topmost` に変更します（80-81行）。
- `date` への移行時にはテンプレート内の全座標が date box 起点へシフトされます（`app/coord_normalizer.py:314-343`）。また、date anchor による正規化後の OCR 座標は `receipts.ocr_json` に保存されます（`app/date_anchor.py:341-346`）。Web 修正の座標解決はその DB の OCR エントリを使うため、修正座標が常に topmost 基準とは限りません（`app/services/ocr_coordinate_service.py:29-47`）。
- その結果、修正対象外の date 基準座標が残ったままテンプレート全体が topmost と扱われます。後続の構造化処理は `coord_basis != 'date'` のとき座標補正を適用するため、これらの座標を topmost の OCR に使い、誤ったフィールド値を抽出する可能性があります（`app/structural_parser.py:149-167`）。逆に、保存済み OCR が date 基準の場合は、更新フィールド自体も topmost とは限りません。
- `tasks/issue_51` と仕様の「修正座標は topmost 基準」という前提も、保存済み OCR が date anchor 後に更新される実装と一致していません。
- **対応案:** 変更対象の OCR 座標基準を判別したうえで、テンプレートの全座標を同一基準に変換してから basis を更新してください。入力の基準を安全に判別できない場合は、basis だけを切り替えない設計にしてください。

## 改善提案

- テストでは、date 基準へ移行したテンプレートの一部フィールドだけを修正し、未修正フィールドを含む全座標の基準が維持・変換されることを確認してください。
- DB に保存済みの date 基準 OCR を使った Web 修正も検証し、座標値と `coord_basis` の組み合わせが整合することを確認してください。
