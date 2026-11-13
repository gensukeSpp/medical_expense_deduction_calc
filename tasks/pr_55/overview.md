# PR #55 レビュー指摘事項対応計画

- 背景: `.github/reports/pr-55-review.md`
- 対応対象: 指摘事項 B, C, D, E, F を順番に対応
- 確認済み: A は対応済み。実装とテストの確認を行い、再発しないことを確認する。
- 追記: G は B-F 対応後に、境界ケースのテスト補完として対応する
- 参照: `tasks/pr_53/overview.md` / `tasks/pr_53/tasks.md`

## Goal

Issue #54 の一覧表示・補填額・氏名フィルタの実装を、レビュー指摘事項に沿って安定化する。

- B: 補填額に負の値が入力されたときに、符号が消えて正数として扱われる問題を解消する
- C: 氏名にカンマが含まれると filter が崩れる問題を解消する
- D: 氏名フィルタの配置が要件と整合しているかを確認し、必要なら UI を統一する
- E: コメントの文字化けを修正する
- F: `tasks/issue_54/` の記述 drift を解消して、実装の最終形と一致させる
- G: テスト不足を補完し、最小回帰確認を行う

## 現状確認（A を確認）

### A: 日付不明行の扱い

実装側では、`app/services/receipt_list_service.py` の `load_rows()` が `date_missing_rows` を分離し、`get_page_context()` で `date_missing_count` と `unprocessed` を返す形に更新済み。

- `normal_rows`: 日付あり
- `date_missing_rows`: 日付なし
- `date_missing_count`: 日付不明件数

`tests/test_calc.py` には `test_get_page_context_date_missing_count` があり、境界ケースの回帰確認が見えている。

このため、今回の担当範囲では A の再対応は不要で、A の確認を行い、B-F の修正に集中する。

## 実装方針

1. B を最優先で修正し、補填額入力の符号を制約する。
2. C を確認し、`_parse_names()` / `hx-vals` / `#list-state` の送信方式を安全化する。
3. D と E は UI と文言の整合を確認し、必要なら合わせる。
4. F は計画書の drift を解消し、最終実装と同期させる。
5. G は増えた境界条件に対する最小テストを追加し、よく使う回帰確認を残す。

## 変更対象ファイル

- `app/services/receipt_list_service.py`
- `app/web/server.py`
- `app/web/templates/list_table.html`
- `tests/test_calc.py`
- `tests/test_web.py`
- `tasks/issue_54/` 配下

## 検証コマンド

```bash
uv run pytest tests/test_calc.py tests/test_web.py -q
uv run pytest -k "date_missing or compensation or names or list" -q
```

## リスク

- B を安全側に強く制限すると、既存の補填額入力が変わる場合がある
- C は UI でカンマ連結をやめると、既存の query form と不一致が起きる可能性がある
- D は Issue 要件の「配置」解釈が曖昧なため、実装上の妥協と文書更新が必要になる可能性がある
- F は文書更新が多いため、コードとの差分とズレないように慎重に扱う

## 判定

A は確認済み。B-F を順番に対応し、最後に G を補完して回帰確認を行う。実装と文書の整合を優先する。
