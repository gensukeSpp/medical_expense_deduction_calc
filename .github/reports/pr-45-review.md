# PR #45 レビュー記録

対象: feat #261027: Issue 42 手動で date anchor（DRAFT）
PR head: feature/converts-coord-basis/42 @ 5a28ca8 / base: main（比較 target: origin/main）
レビュー実施: 2026-10-27

## 0. 参照資料の確認

確認した資料（PR head 5a28ca8 上）:
- Issue #42 本文（gh issue view 42）: 受け入れ条件 1〜5、再利用する既存実装の指定あり
- PR #45 本文と変更ファイル一覧（gh pr view 45）
- tasks/issue_42/overview.md, tasks.md, architecture.md, test_plan.md
- specs/2026-10-27-spec.md
- AGENTS.md, .hermes/rules/coordinate-normalization.md（PR で追記）

存在しなかった資料:
- tests/test_date_anchor.py: PR head に存在しない。.gitignore の test_*.py に該当するため追跡対象外。test_plan.md は「gitignore 対象・commit 不要」と記載。PR 本文の「25 passed」はこのローカル未追跡ファイルに依存しており、コミット済み差分では再現できない。
- docs/architecture の当該 Issue 向けスナップショット: PR に含まれず。本件は Python/FastAPI のため不在を欠陥とは扱わない。

差分基準の注意:
- ローカル HEAD は 885d83e で、PR head（5a28ca8）と一致しない。本レビューは origin/main...5a28ca8 の差分を基準とした。
- knowledge graph は作業ツリー/ローカル HEAD ベースで、PR head の鮮度は未確認。graph の出力は範囲の目安としてのみ使用した（ソースで検証）。

## 1. 総評

Issue #42 の主目的（Web 管理画面から topmost → date の coord_basis を手動切替、date box 未学習は拒否、二重変換防止、template_history 記録）は、コードとテストの上で概ね満たされている。切替ロジックは既存の `shift_template_to_date_basis()` に集約され、自動移行（date_anchor.py）と同じ変換を共有しており、設計は Issue の指定どおり。

一方、Issue #42 の「date 基準による補正を試せるようにする」という検証目的は、Issue が挙げた実例（同日付候補 2 件の clinic）では達成されない見込みが高い。これは本 PR の一番大きな論点。

PR 本文の検証数値（251 passed）は、コミット済みテストのみでは再現できなかった（後述）。

## 2. 良い点

- 切替ロジックの一元化: `coord_normalizer.shift_template_to_date_basis()` を新設し、date_anchor.py の自動移行と admin 手動切替で共有。date_anchor.py のリファクタは出力・分岐が同値（date 未学習 → None）。
- 受け入れ条件の多くがテストで担保: 成功・冪等（noop）・date 未学習で 409・存在しない clinic で 404・不正 basis で 400・DB 未設定で 503。history の change_reason=manual_basis_toggle も検証。
- layout matching で date 基準テンプレートを候補から除外する既存の #40 ガードが維持されている（structural_parser.py:131）。
- update_template_basis() の二重変換ガード（coord_basis=='date' なら False）と、service 側の noop 判定の二段構え。
- フォームの application/x-www-form-urlencoded にも対応（python-multipart は pyproject.toml 依存に存在）。

## 3. 指摘事項

[中-1] 手動切替後も、Issue の対象 clinic では date 基準の補正が実際には走らない（検証目的との不一致）
- 場所: app/date_anchor.py:111-130（resolve_date_anchor）、app/date_anchor.py:219（自動移行は topmost のときのみ）、app/services/template_admin_service.py:toggle_basis
- 根拠（コード読解。未再現）: Issue #42 の clinic は date 候補が 2 件（調剤日/発行日）。切替後の template_basis は 'date' になるが、resolve_date_anchor は候補 2 件かつ basis != 'topmost' のとき None を返す。結果として apply_date_anchor_normalization は早期 return し、raw は再正規化されない（topmost のまま）。
- 影響: テンプレートだけが date 座標系になり、raw は topmost のまま。structural_parser.py:155 は date テンプレートの座標上書きをスキップするため、このクリニックでは座標ベースの抽出が無効化され LLM フォールバックに落ちるだけで、「date 基準の補正」は検証できない。Issue の目的（date 基準正規化の挙動検証）と乖離。
- 修正方向: (a) 手動切替時に raw 側の date 基準化を同時に行う経路を用意する（既に date basis のテンプレートでも 2 候補を解決できるよう resolve の条件を見直す）、または (b) PR 本文・Issue に「本 PR は date テンプレート化まで。2 候補 clinic の補正検証は別 Issue」と明記し、Issue #42 の受け入れ条件 1〜4 との対応を整理する。いずれにせよ一度再現（実 raw × 切替後テンプレート）してから判断すること。

[中-2] 検証数値の再現性と回帰テストの欠落（Task 2 のリファクタが未コミットテストに依存）
- 場所: tests/test_date_anchor.py（PR head に無し。.gitignore:19 の test_*.py に該当）、PR 本文「検証」節
- 事実: PR head 上で tests/test_coord_normalizer.py / test_db.py / test_web.py は 64 passed。全スイートは 215 passed / 1 failed（PR 本文の 251 passed とは不一致。ローカル未追跡の test_date_anchor.py を含めた数の可能性が高い）。
- 影響: date_anchor.py の大きめのリファクタ（自動移行の共通化）の回帰テストが、コミット済みの PR からは検証できない。レビュー・CI で再現できない。
- 修正方向: test_date_anchor.py の該当テストを `git add -f` で追跡させるか、回帰テストを既存の追跡対象ファイル（test_coord_normalizer.py 等）へ移し、PR 本文の数値を PR head で再現できる形に直す。

[中-3] 管理 API に認証がない（状態を書き換える POST）
- 場所: app/web/server.py:34-60（/admin/templates, /admin/templates/{clinic_id}/basis）
- 事実: Depends による認証・権限チェックは無い。既存 UI も同様に無認証の可能性が高いが、本 PR は DB の coords とテンプレート履歴を書き換える点で既存より影響が大きい。
- 影響: 同一ネットワークから到達可能な環境で、第三者が座標基準を変更できる。
- 修正方向: 本 PR で解決するか、少なくとも「ローカル検証用・非公開」の前提を PR 本文とコード（コメント）に明記し、別 Issue に切り出す。サーバ bind 先（localhost 限定か）も確認すること。

[低-1] フォーム送信時に JSON がそのまま表示される（UX）
- 場所: app/web/templates/admin_templates.html（form method=post）→ server.py:60-72
- 事実: 成功時は JSON（{"status":"updated"}）を返すのみ。ブラウザのフォーム送信では JSON 画面に遷移し、一覧に戻れない。
- 修正方向: フォーム経路は 303 で /admin/templates へリダイレクトする（Accept やヘッダで分岐、もしくは別ルート）。

[低-2] 不正な date box のエラーメッセージが誤解を招く
- 場所: app/coord_normalizer.py（shift_template_to_date_basis）、template_admin_service.py:toggle_basis
- 事実: date が multi-box（[[[..]]] 形式）の場合、_is_numeric_point が False となり None → 「date box 未学習」の 409 になる。実際は学習済みだが形式不一致。
- 修正方向: 形式不一致と未学習を区別したメッセージにする、または multi-box を明示的に扱う。

[低-3] 自動移行の挙動変更（非数値座標）
- 場所: app/date_anchor.py:219-225 の旧実装 vs 新実装
- 事実: 旧コードは非数値点を含む date box で min() が TypeError を出し得たが、新コードは数値点のみを対象とし、0 件なら None（従来方式）。より安全だが、既存の挙動変更。
- 修正方向: 変更として PR 本文に記載する（同値リファクタという説明とは厳密には一致しない）。

[低-4] 既存の文字列リテラル残骸（本 PR 起因ではない）
- 場所: app/db.py（PR head で約 170 行付近、main でも 147 行付近に存在）
- 事実: 関数間に、コミットメッセージのような文字列リテラルが残っている。動作には影響しない。
- 修正方向: 別 PR で削除（本 PR の範囲外として扱う）。

[情報] 既知失敗 1 件
- tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs（caplog が INFO を取得しない）。本 PR の変更ファイル外。PR 本文は「既知の事前失敗」としているが、origin/main 上での再現は今回未確認（未検証扱い）。

## 4. 改善提案

1. [中-1] の判断: 実 raw × 切替後テンプレートで、apply_date_anchor_normalization と structural_parser の出力を一度再現してから、Issue #42 の受け入れ条件 1〜4 と「検証目的」をどう切り分けるか決める。
2. [中-2] 回帰テストを追跡対象に移す。PR 本文の検証数値は PR head で再実行した値に差し替える。
3. [中-3] 管理 API の公開範囲（bind 先・認証の要否）を決め、本 PR か別 Issue に記録する。
4. フォーム経路の 303 リダイレクトと、フォーム送信の結合テスト（現状は JSON のみテスト）を追加する。
5. multi-box の date box を扱うなら、shift_template_to_date_basis の入力形式を明示したテストを追加する。

## 5. 検証記録

実行済み（PR head 5a28ca8 を detached worktree で展開、終了後に削除）:
- uv run pytest -q tests/test_coord_normalizer.py tests/test_db.py tests/test_web.py → 64 passed
- uv run pytest -q（全体） → 215 passed, 1 failed（test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs）
- tests/test_date_anchor.py は PR head に存在せず、実行不可（検証できず）

未実行:
- black 整形チェック（PR 本文は実施と記載。今回未再実行）
- origin/main での既知失敗の再現（未検証）
- 実 raw × 切替後テンプレートの再現（[中-1] の確認は未実施。コード読解による推定）
- knowledge graph の PR head 向け再構築・鮮度確認（graph は作業ツリーのスナップショットで、PR head との一致は未確認）。review context は changed_files 明示・max_depth=1・include_source=false で実行したが、出力は 1.1MB に膨らんだ。graph が示した「テスト未カバー 63 関数」は範囲の目安として扱い、テスト有無はソース検索で確認した。

本記録はコードを変更していない。
