# PR #53 レビュー記録

- PR: feat #261109: Issue #50 coords を当てはめて強制 (--force オプション)
- 状態: DRAFT / author: gensukeSpp
- head: 5a9a986 (ローカル HEAD と一致)
- 比較基準: origin/main（main 相当。origin/main は存在）
- 差分: 15 files, +1469 / -15

## 参照資料の確認

- 確認できたもの:
  - PR 本文 (gh pr view 53)
  - tasks/issue_50/overview.md, tasks.md, architecture.md, test_plan.md
  - specs/2026-11-06-2-spec.md (本 PR で新規)
  - 実装: app/date_anchor.py, app/verify_date_anchor.py, app/services/image_processing_service.py, app/services/receipt_processor.py
- 確認できなかったもの:
  - GitHub Issue #50 の本文: PR 本文・タスク文書から Issue 番号は読めるが、`gh issue view 50` は今回未実行。
  - docs/architecture 配下の本 Issue 向けドキュメント: 本 PR では新規追加されていない（architecture は別 docs PR 運用のため欠落は指摘対象外）。
- knowledge graph (review context, changed_files 明示, max_depth=1, include_source=false) は約 605KB。変更 37 ノード、影響 65 ノード / 16 ファイル。結論はソースで検証した。

## 総評

Issue #50 の「date 基準テンプレで anchor 解決不能時に coords_corrections で name / amount を強制抽出する」機能を、
apply_date_basis_force() と両経路（画像 OCR / receipt_processor）への統合、および #47 検証ツールの --force で実装している。
ゲート条件（clinic 非空・coord_basis=date・date 学習済み）、DB 二重登録防止（insert なし）、元ファイル不変更のテストは揃っている。

一方、PR 本文の「採用案」（オフセット推定による再正規化）と、実装・spec の「案A」（再正規化なしの直接近傍抽出）が食い違っている。
実装されているのは、PR 本文自身が「フレームがずれていると不安定」と述べている案A である。この点が最大の論点。

## 良い点

- 両経路（ImageProcessingService / ReceiptProcessor）で「anchor 結果が None の時のみ force」を条件分岐しており、anchor 確定時の二重補正を防いでいる（テストあり）。
- apply_date_basis_force() は insert_receipt を呼ばず update_receipt_ocr_json_by_source のみで DB 同期しており、二重登録を避けている。
- verify_date_anchor の --force は元 raw / DB を変更せず、報告のみを返す設計になっている。
- 既存 fallback 方針（同点・曖昧時は採用しない）を date anchor 側で維持している。

## 指摘事項

### [P1] PR 本文・spec・実装のアルゴリズムが三者不一致
- 場所: PR 本文「採用案」(オフセット推定 / Hough 的投票 / match_template_by_layout 相当)
- 根拠: app/date_anchor.py の apply_date_basis_force()（約 364-442 行）は raw を再正規化せず、coords_corrections をそのまま search_fields_by_proximity に渡している。
  grep でも hough / 投票 / registration / match_template_by_layout の実装は見つからなかった。
  specs/2026-11-06-2-spec.md は「案A採用・再正規化は行わない」と明記。
  tasks/issue_50/overview.md:70 では案B は「検討のみ」。
- 影響: レビュアーと将来の作業者が、どの方式が正なのか判断できない。PR 本文を信じると未実装の機能があると誤解する。
- 修正方針: 採用方式を一つに決め、PR 本文・overview.md・spec を揃える。案B を採るなら実装が未完、案A を採るなら PR 本文の「採用案」節を削除か「不採用」へ書き換える。

### [P1] 案A は位置ずれ時に誤った name / amount を structured と DB に書き込む
- 場所: app/date_anchor.py apply_date_basis_force()（changed ループ〜update_receipt_ocr_json_by_source 呼び出し）
- 根拠: force は raw の絶対座標に対して template の coords_corrections をそのまま当てる。date box を探さないため、
  フレームが揃っていない画像ではずれた箇所の文字列を name / amount として拾う。
  PR 本文 (経緯) 自身が「ずれていると不安定」「#40 ガードと衝突」と認めている。
  ゲートは clinic・coord_basis・date 学習済みのみで、抽出結果の妥当性（被覆率・date との整合など）は見ていない。
- 影響: 誤った金額が structured_data.json と DB の ocr_json に上書きされる。医療費控除の申告データに直接影響する。
  テストは「値が変わる」ことを確認しているが、「正しい値になる」ことは検証していない。
- 修正方針: (a) 案B のように raw フレームを補正してから抽出する、または (b) 抽出値の検証（テンプレ由来の期待ラベル有無、被覆率の閾値、既存値との比較）を入れ、
  不確かな場合は上書きせず低信頼扱いにする。少なくとも「上書き」ではなく「候補」として残す案を検討する。

### [P2] force 呼び出し条件が spec の「anchor 解決不能」より広い
- 場所: app/services/image_processing_service.py（約 90-101 行）、app/services/receipt_processor.py（約 219-230 行）
- 根拠: apply_date_anchor_normalization() は date 未抽出・clinic 未抽出・テンプレなし・OCR 空でも None を返す。
  そのため anchor_result is None の条件では、date 候補 0 件以外の理由でも force が呼ばれる。
  force 側のゲートで多くは弾かれるが、呼び出し意図（spec: 「date 候補 0 件等」）と一致しない。
- 修正方針: anchor 失敗理由を戻り値で区別するか、force 側で date 候補有無を確認する。少なくとも spec の前提を明記する。

### [P2] テスト失敗 1 件（既存の失敗として扱われているが未検証）
- 場所: tests/test_image_processing_service.py:92 TestRunOcr::test_calls_process_image_and_logs
- 結果: 対象 4 ファイル実行で 72 passed, 1 failed。失敗は caplog に "Saved 1 item to" が出ないことによる。
- 根拠: _run_ocr（app/services/image_processing_service.py:174-191）は本 PR の差分に含まれない。
  テストは origin/main にも同じ内容で存在する。PR 本文は「stash しても落ちる既知の事前存在失敗」と述べている。
- 注意: 私は stash による再現は行っていない。PR の主張は妥当性が高いが、要確認。

### [P3] apply_date_basis_force() の細部
- proximity_threshold == 50.0 の分岐で DEFAULT_PROXIMITY_THRESHOLD に置き換えている。
  値が同じなら引数を素直に既定値へ置けばよく、分岐は不要。
- 戻り値が (0, 0) で、apply_date_anchor_normalization() の「オフセット」と同じ型になっている。
  呼び出し側は戻り値を使わないが、型の意味が紛らわしい。bool か None 相当に揃えると良い。
- 注: PR 本文の「旧:305-306」等の行番号参照は既に古い可能性がある。

### [P3] テストカバレッジ（graph 報告）
- review context の untested_functions に _apply_coordinate_extraction, _is_valid_calendar_date,
  _resolve_date_basis_candidate, _to_ocr_entries, _top_left_box などが含まれる。
  graph の結果は build 時点のスナップショットであり、テストの有無はソースと pytest で別途確認が必要。
  本 PR 新規の apply_date_basis_force 自体は tests/test_date_anchor.py で検証されている。

## 改善提案

1. 最優先: 採用方式（案A か案B か）を決め、PR 本文・tasks/issue_50/overview.md・specs/2026-11-06-2-spec.md を一致させる。
2. 案A を採るなら、上書き前の抽出値検証（被覆率 / テンプレ期待値との整合 / 既存値との比較）を追加し、
   誤値を DB に書かないテストを足す。案B を採るなら apply_date_basis_force の実装を差し替える。
3. force の呼び出し条件を spec の「anchor 解決不能（date 候補 0 件等）」に限定する。
4. 既存の TestRunOcr 失敗は、origin/main で同じ失敗が出ることを確認して PR 本文に根拠を残す。
5. 小さな整理: threshold の分岐削除、戻り値の型の統一。

## 検証の範囲と限界

- 実行したテスト: tests/test_date_anchor.py, tests/test_verify_date_anchor.py, tests/test_image_processing_service.py, tests/test_receipt_processor.py
  -> 72 passed, 1 failed（上記 TestRunOcr）。
- 未実行: 全テスト、black チェック、origin/main での失敗再現、gh issue view 50。
- 本記録は PR 本文・差分・spec・タスク文書をソースと突き合わせた結果に基づく。
- 作業ツリーには PR 外の未追跡ファイル（requirements.txt, test_logic*.py, .vscode/mcp.json.bak 等）がある。PR のレビュー対象外として扱った。
