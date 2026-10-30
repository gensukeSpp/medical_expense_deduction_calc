# PR #46 レビュー記録

対象: fix #261029: PR #45 レビュー対応について（DRAFT）
PR head: fix/manual-coord-basis/45 @ ae64a9f / base: main（比較 target: origin/main）
レビュー実施: 2026-10-30
基準資料: .github/reports/pr-45-review.md（PR #45 のレビュー記録）

## 0. 参照資料の確認

確認した資料（PR head ae64a9f を detached worktree で展開して確認。終了後に削除済み）:
- gh pr view 46（本文・変更ファイル 22 件。additions 2136 / deletions 164 と一致）
- .github/reports/pr-45-review.md（指摘 中-1〜3、低-1〜4、情報 の対応状況を照合）
- specs/2026-10-29-spec.md（PR に含む）、tasks/pr_45/plan.md・tasks.md（PR に含む）
- ソース: app/date_anchor.py, app/coord_normalizer.py, app/services/image_processing_service.py, app/services/receipt_processor.py, app/web/server.py, .gitignore, tests/test_web.py ほか

存在しなかった／確認できなかった資料:
- Issue: PR 本文・ブランチ名に Issue 番号の明示なし（#261029 は社内採番とみられ GitHub Issue 照会は未実施）
- 「[中-3] 別 Issue」: PR 本文に Issue 番号の記載なし（未確認）
- PR 本文の tasks/pr-45/ 表記: 実際のディレクトリは tasks/pr_45/（表記ずれ）

差分基準の注意:
- ローカル HEAD は 51a7b50 で PR head（ae64a9f）と一致しない。ローカルブランチは origin/fix/manual-coord-basis/45 より 1 コミット先行（未 push）。レビューは ae64a9f の内容で実施。
- knowledge graph (better-CRG) は changed_files 明示・max_depth=1・include_source=false で実行。出力は約 1.2MB に膨らみ、以下の「テスト未カバー」「影響範囲」は範囲の目安としてのみ扱い、結論はソースと実行結果で確認した。

## 1. 総評

PR #45 レビュー記録の指摘のうち、中-1（date 基準での実補正）と低-1（303 リダイレクト）、中-2（テストの追跡化）は**コード上は対応済み**。中-3（管理 API 認証）と低-4 は意図的に未対応。低-2 は multi-box 対応として別経路で解消されている。

ただし次の 2 点で「対応として妥当」とは言い切れない:
- 低-1 の 303 リダイレクトが JSON クライアントにも一律適用され、既存の `tests/test_web.py` の 2 件（test_admin_toggle_success / test_admin_toggle_idempotent）が失敗する。PR #45 時点（5a28ca8）では test_web.py は 64 件 pass だったため、本 PR で導入された回帰。
- PR 本文の「251 passed」は今回の実行でも 251 passed 件数は一致するが、同時に 3 failed が含まれており、「既知の事前失敗 1 件」という説明より多い。

PR は DRAFT のため、マージ前の修正を前提に扱う。

## 2. 良い点

- 中-1 の本質（date 基準テンプレートでは resolve が常に None を返していた）を、`resolve_date_anchor` に date basis 用のレイアウト整合スコアリングを追加して解決（app/date_anchor.py:126-135, `_resolve_date_basis_candidate`）。同点は None にフォールバックする安全側の設計。
- `_apply_coordinate_extraction` で、date 基準 raw に対して座標抽出を structured_data.json に反映する経路を追加。PR #45 の「raw が topmost のまま」問題に対する直接の回答になっている。
- date 基準の 2 候補ケースを検証する単体テスト（test_duplicate_date_basis_picks_by_layout, test_duplicate_date_basis_tie_returns_none, test_writes_coordinate_extraction_to_output_dir）を追加。
- test_date_anchor.py が `git ls-files` で追跡済みになり、.gitignore の test_*.py 除外も削除。中-2 の「コミット済み差分で再現できない」問題は解消。
- multi-box の date box を `shift_template_to_date_basis` で扱えるようにし、低-2 の「学習済みなのに 409」問題の入力側を修正（app/coord_normalizer.py:323-338）。
- 管理画面の 303 リダイレクト自体は、フォーム送信で JSON が表示される問題（低-1）の方向性として妥当。

## 3. 指摘事項

[高-1] 管理 API の JSON 応答が 303 リダイレクトに置き換わり、既存テストが 2 件失敗（回帰）
- 場所: app/web/server.py:79（`return RedirectResponse(url="/admin/templates", status_code=303)`）。JSON リクエストの分岐がない。
- 事実: 実行結果（PR head ae64a9f の worktree で `uv run pytest -q` 実行）: 3 failed, 251 passed。
  - tests/test_web.py::test_admin_toggle_success（tests/test_web.py:418-420 が `response.json()["status"]` を期待）
  - tests/test_web.py::test_admin_toggle_idempotent（tests/test_web.py:441-444）
  - 原因: TestClient は既定で follow_redirects=True のため、303 を追従して GET /admin/templates の HTML を JSON として解析し JSONDecodeError。
- 影響: 既存の JSON API 利用者（テスト、スクリプト、curl）が壊れる。CI で red。PR 本文に記載の検証結果と食い違う。
- 修正方向: 低-1 の対応を「フォーム送信（content-type が application/x-www-form-urlencoded / multipart）のときのみ 303」に限定する。JSON 経路は従来どおり `{"status": ...}` を返す。あわせてフォーム経路の結合テスト（303 の Location 確認）を追加。

[中-1] 低-2 の対応は「メッセージ改善」ではなく「multi-box 対応」にすり替わっている／未使用関数が残存
- 場所: app/coord_normalizer.py:260 `_has_any_numeric_point` は定義のみで、リポジトリ内のどこからも呼ばれていない（grep 確認）。PR 本文は「`_has_any_numeric_point()` で全座標点をチェック…」と記載。
- 事実: 実際の挙動変更は shift_template_to_date_basis の multi-box 分岐（app/coord_normalizer.py:323-338）によるもの。非数値・形式不一致の date box は引き続き「未学習」扱い（template_admin_service.py の 409 メッセージ「date box 未学習のため切替できません」）になる。
- 影響: 死んだコードが残り、PR 本文の説明と実装が一致しない。形式不一致を区別するメッセージは未実装のまま。
- 修正方向: `_has_any_numeric_point` を削除するか、multi-box 判定に実際に使う。PR 本文の説明を「multi-box を対応、形式不一致は従来どおり 409」に書き換える。形式不一致の専用メッセージは必要なら別途。

[中-2] 中-1 の解決ロジックは「date 基準で 2 候補」の判定が近似で、実 raw での確認がない
- 場所: app/date_anchor.py:154-165（`_resolve_date_basis_candidate`）。候補の top-left を仮想アンカーとみなし、フィールド中心を投影して最近傍距離を合計する。
- 事実: テストは合成データ（test_duplicate_date_basis_picks_by_layout）のみ。PR #45 レビュー記録が求めた「実 raw × 切替後テンプレート」の再現は PR 本文にもない（「実起動における検証過程での疑問」は未解決のまま記載）。
- 影響: 本番の 2 候補クリニックで誤った候補が採用されるリスク。ただし同点は None（従来方式）にフォールバックするため、データ破壊は起きにくい。誤採用時は座標抽出値が誤る。
- 修正方向: 実在クリニックの raw_data.json と切替後テンプレートで、採用候補・オフセット・抽出結果を 1 件再現し、PR 本文の「検証結果」に記録する。

[中-3] 管理 API の認証（PR #45 レビュー中-3）は「別 Issue」とあるが、Issue 番号・公開範囲の記録がない
- 場所: app/web/server.py:55-82（/admin/templates/{clinic_id}/basis は認証なし）。
- 事実: PR 本文は「別 Issue にて対応」とのみ記載。Issue 番号なし。bind 先（localhost 限定か）も PR 本文・コメントで確認できず。
- 影響: DB の coords とテンプレート履歴を第三者が書き換え得る状態が、別 Issue 化のまま残る。
- 修正方向: 別 Issue を作成し番号を PR 本文に記載。少なくとも「ローカル専用・非公開」の前提をコメントと PR 本文に明記。

[低-1] テスト追跡化の対応は済んだが、PR 本文の検証数値の内訳が不明確
- 場所: PR 本文「検証」節相当（本文に検証結果が個別に書かれていない）。
- 事実: 今回の実行: 全体 251 passed / 3 failed（うち 2 件は [高-1] の回帰）。PR #45 レビュー時の 215 passed / 1 failed とは件数が異なる（test_date_anchor.py 等の追加分）。
- 修正方向: PR 本文に「実行コマンド・件数・失敗テスト名」を記載。特に既知失敗 `test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs` は origin/main での再現結果を添える（今回は未確認）。

[低-2] PR 本文のファイル一覧と実際の差分の不一致・範囲外の変更の混在
- 事実: 変更ファイル 22 件のうち、PR 本文に列挙されていないもの: tests/test_migrations.py（+246）、.gemini/settings.json（+46）、.github/agents/pr-review.md（-47 削除）、.hermes/rules/design-decisions.md（+51）、docs/architecture/2026-10-27-architecture.md、AGENTS.md（+53/-52 の大幅書き換え）、GEMINI.md。
- 影響: Issue #42 / #45 レビュー対応と無関係な設定・ドキュメント変更が混在し、レビュー・ロールバックが困難。
- 修正方向: 無関係な変更（.gemini/, .github/agents/, GEMINI.md）は別 PR に分割。PR 本文に変更ファイルの説明を追加。

[低-3] タスク・仕様のディレクトリ名表記のずれ
- 事実: PR 本文は tasks/pr-45/、実ファイルは tasks/pr_45/。
- 修正方向: 本文の表記を実ディレクトリ名に合わせる。

[低-4] 低-3（自動移行の挙動変更）は PR 本文に一行記載のみ
- 事実: 「PR #42 に訂正を加える」とだけあり、旧 TypeError → None への変更内容・影響範囲が書かれていない。
- 修正方向: 旧挙動（非数値点で TypeError の可能性）と新挙動（数値点のみ、0 件で None）の差を PR 本文に明記。

[低-5] 未 push のローカルコミット（情報）
- 事実: ローカルブランチ fix/manual-coord-basis/46 は origin/fix/manual-coord-basis/45 より 1 コミット先行（51a7b50 docs #261030: pr-review update）。PR head には含まれない。
- 影響: レビュー対象は ae64a9f の内容で、51a7b50 は対象外。

## 4. 改善提案

1. [高-1] を最優先で修正。JSON 応答を維持し、フォーム送信のみ 303。`pytest tests/test_web.py` を通してから再 push。
2. [中-2] 実 raw × 切替後テンプレートの再現を 1 件行い、結果を PR 本文と spec の「検証結果」に記録。
3. [中-1] 未使用の `_has_any_numeric_point` を削除し、PR 本文の説明を実装に合わせる。
4. [中-3] 別 Issue を作成し番号を記載。公開範囲を明記。
5. [低-2] 無関係変更を別 PR へ分割。

## 5. 検証記録

実行済み（PR head ae64a9f を detached worktree で展開、終了後に削除）:
- `uv run pytest -q tests/test_date_anchor.py tests/test_coord_normalizer.py tests/test_image_processing_service.py tests/test_web.py tests/test_migrations.py` → 3 failed, 107 passed
  - FAILED tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs（既知の事前失敗とされるもの。origin/main での再現は未確認）
  - FAILED tests/test_web.py::test_admin_toggle_success（JSONDecodeError: 303 追従で HTML を解析）
  - FAILED tests/test_web.py::test_admin_toggle_idempotent（同上）
- `uv run pytest -q`（全体） → 3 failed, 251 passed（上記と同じ 3 件）
- `git ls-files tests | grep date_anchor` → tests/test_date_anchor.py は追跡済みであることを確認
- `git check-ignore` → tests/test_date_anchor.py / tests/test_migrations.py は ignore されていない

未実行・未検証:
- origin/main での既知失敗の再現
- 実 raw × 切替後テンプレートの再現（[中-2]）
- black 整形チェック
- GitHub Issue の照会（Issue 番号の明示がないため未実施）
- 管理画面のブラウザ動作確認（ブラウザ接続不可のため未実施）
- knowledge graph の結果は範囲の目安としてのみ使用（ソースで検証）

本記録はコードを変更していない。
