# PR #57 レビュー記録

対象: feat #261117: Issue #56 主要操作を Web UI に（DRAFT）
ブランチ: feature/command-to-webui/56 → main
レビュー対象 HEAD: e315128（gh pr view の headRefOid と一致。ローカル HEAD も同一で確認済み）
比較基準: origin/main (57df635)。ローカル main は存在しないため origin/main を使用。

## 0. 参照資料の確認

確認したもの:
- Issue #56: 存在・OPEN。PR タイトルに明示されているため読解。受け入れ要件は 3 件（watch 操作 / DB 未選択で画像指定はエラー / 生成中はファイル指定不可）。
- tasks/issue_56/: overview.md, tasks.md, architecture.md, test_plan.md すべて存在（PR 差分に含まれる）。
- specs/command-to-webui-spec.md: 存在（PR 差分に含まれる）。Q1〜Q3 の決定（案C・案A・案A）と実装は一致。

確認しなかったもの（理由）:
- docs/architecture/2026-11-16-webui-operations.md: AGENTS.md の方針により docs/architecture/* はレビュー対象外。
- AGENTS.md / .hermes/rules/cli.md: エージェント運用ファイルのためレビュー対象外。

## 1. 総評

Web UI から watch 開始・前処理切替・DB 選択・画像コピーを行えるようにする PR。
OCR を起動時でなく初回 watch 時に遅延初期化する変更、--serve 起動の高速化、フォーム送信を fetch に置き換えた UX 改善を含む。
設計は Issue #56 の要件と概ね一致している。
一方で、DB アップロード処理に既存 DB を失うデータ損失バグがあり、マージ前に修正が必要。

## 2. 良い点

- _scan_lock（非ブロッキング取得）で多重スキャンを防止（app/services/watch_controller.py _run_scan）。
- OCR を WatchController._get_ocr で遅延初期化し、--serve 起動時の PaddleOCR 読み込みを回避（main.py 16-31 行）。
- ファイル名は Path(...).name で basename 化し、パス注入を防止。画像は拡張子で検証。
- 303 リダイレクトを fetch で吸収し、エラーはパネル内 inline 表示（JSON エラーページに遷移しない）。
- 計画からの逸脱（tmp_path の mkdir、409 リトライ）を PR 本文で明示している。
- Issue の要件・Q1〜Q3 の決定と実装は整合（画像は案C、DB は data/ 直下の元ファイル名 案A、状態はメモリ保持）。

## 3. 指摘事項

凡例: 高 = マージ前に修正。中 = 次の修正で対応。低 = 改善推奨。情報 = 本 PR 外の既存問題。

### [高-1] DB アップロードで既存 DB を上書き・削除する（データ損失）
- 場所: app/services/watch_controller.py save_db_upload（dest.write_bytes → set_db_path → except 内 dest.unlink）
- 誘発条件: data/ に既存 DB（例: data/db.sqlite3）があり、同名ファイルを DB 選択でアップロードする。
  (a) 不正な内容の場合: 既存 DB を先に書き潰し、migration 失敗後に unlink で削除する。
  (b) 正しい SQLite の場合: 検証なしで既存 DB が差し替わる。
- 再現（scratch 環境、リポジトリ外の DB で実施）:
  - ケース 1: 不正バイト列を db.sqlite3 として送信 → 例外後、既存 DB ファイルが消失（exists=False）。
  - ケース 2: 別 DB を同名で送信 → 既存テーブル marker が消え、データが置換される。
- 影響: 利用者の実データ（領収書データを含む DB）が失われる。一覧・集計ができなくなる。
- 修正方針:
  - 一時ファイル（例: data/.upload-xxxx.tmp）に書き、migration と検証が成功した後に原子的に rename する。
  - 同名の既存ファイルがある場合は上書きせず拒否するか、別名（連番）で保存する。
  - unlink は今回作ったファイルに限定する（既存ファイルを消さない）。
  - 検証: SQLite ヘッダ確認＋必須テーブル（receipts 等）の存在確認。
- テスト: 既存 DB と同名の不正/正常アップロードで、既存 DB の内容が保持されることを確認するテストを追加。現在の test_save_db_upload_invalid_file_raises はこのケースを検証していない。

### [中-1] watch の停止→再開で監視スレッドが二重化する
- 場所: app/services/watch_controller.py stop_watch（join timeout 後に self._thread = None）と start_watch（self._stop_event.clear()）
- 誘発条件: スキャン中（poll_interval + 5 秒を超える）に停止し、直後に再開する。
- 再現（scratch で実施）: stop_watch は 6.0 秒で戻り watch_on=False と表示。start_watch 後に web-watcher スレッドが 2 本生存。
  停止要求（stop_event）が clear されるため、旧スレッドも停止せず監視を続ける。
- 影響: UI は OFF/ON を表示するが、実際のループ数が一致しない。停止が効かない状態が生じる。
  _scan_lock により同時スキャンは防止されているため、データ競合までは確認していない。
- 修正方針: スレッドごとに専用の stop Event を生成して渡す。stop_watch はスレッドが終了するまで待つか、生存中は _thread を保持する。start_watch は前スレッドの生存を確認してから起動する。

### [中-2] 処理中でも DB を差し替えられる
- 場所: app/web/server.py post_config（db_file 分岐）、watch_controller.py save_db_upload
- 内容: /images は is_processing で 409 を返すが、/config の db_file アップロードは処理中チェックがない。
  スキャン中に db_path が切り替わると、書き込み先 DB が途中で変わる。DB ファイルの削除（[高-1]）とも組み合わさる。
- 修正方針: is_processing 中は 409 を返す（UI の db-file-input も disabled 制御に含める）。

### [中-3] 「生成完了まで画像指定不可」が完全には満たされない（Issue #56 要件との差）
- 場所: app/web/server.py upload_image、app/web/templates/index.html applyStatus（imgInput.disabled = s.is_processing）
- 内容: Issue #56 は「structured_data が生成されるまでファイル指定を不可にする」と定めている。
  実装は is_processing（スキャン中）だけで制御している。コピー後、次のポーリングでスキャンが始まるまでの間は
  is_processing=False のため input が再度有効になる。その間に 2 枚目をコピーすると、1 回のスキャンで複数画像が処理され、
  Issue が避けるとした精度低下の状態になる。
- 修正方針: 「処理待ち」状態（コピー後〜structured 生成完了まで）をサーバ側で保持し、その間は /images を 409 にし、UI も disabled にする。

### [中-4] 認証のない変更系エンドポイント（任意パス操作）
- 場所: app/web/server.py post_config（input_dir）、/images、/watch/start、/watch/stop、/config（db_file）
- 内容: 認証がない。post_config は任意パスに対して expanduser と mkdir(parents=True) を実行する。
  既定 host は 127.0.0.1（app/args.py:42）だが、--host 0.0.0.0 を指定すると LAN から DB 差し替え、フォルダ作成、画像コピーが可能になる。
- 修正方針: 非 loopback の host で --serve を起動する場合は警告または起動拒否にする。入力パスは許可ルート配下に限定する。
  個人利用の前提を spec / cli.md に明記する。

### [中-5] copy_image が同名の既存画像を無確認で上書きする
- 場所: app/services/watch_controller.py copy_image（dest.write_bytes）
- 内容: input_dir に同名ファイルがあると上書きされる。mtime が更新されるため、処理済み画像が再処理対象になる。
  利用者の元画像（指定フォルダが Downloads など）も失われる。
- 修正方針: 既存ファイルがある場合は連番を付与するか、409 を返す。

### [低-1] テストが実カレントディレクトリの data/ に書き込む
- 場所: tests/test_watch_controller.py 186〜220 行（save_db_upload 系）、tests/test_web.py 約 790〜805 行（DATA_DIR を参照）
- 内容: DATA_DIR = Path("data") は相対パスのため、chdir も monkeypatch もなくリポジトリ直下で pytest を実行すると data/my.db 等が作られる。
  また、[高-1] のケース（既存同名 DB）を検証するテストがない。
- 修正方針: monkeypatch.chdir(tmp_path) または DATA_DIR の差し替えを使う。既存同名 DB のテストを追加する。
- 対応状況（2026-11-17）: 対応済み。
  - tests/conftest.py に autouse fixture `isolate_data_dir` を追加し、`app.services.watch_controller.DATA_DIR` を
    tmp 配下へ差し替えた。テスト中にリポジトリ直下の data/ が変更された場合はテストを失敗させるガードも実装。
  - `WatchController.__init__` の `data_dir` 既定値を実行時解決（モジュール変数 DATA_DIR 参照）に変更し、
    app/web/server.py 側も `watch_controller.DATA_DIR` の実行時参照へ変更（テストからの差し替えが可能）。
  - 既存同名 DB のテストは [高-1] 対応時に追加済み（test_save_db_upload_invalid_keeps_existing_db /
    test_save_db_upload_valid_keeps_existing_db / test_save_db_upload_does_not_delete_existing_db_on_failure /
    test_config_db_upload_keeps_existing_db）。
  - 隔離の回帰テスト（test_default_data_dir_is_isolated_from_repo_data /
    test_save_db_upload_with_default_data_dir_does_not_touch_repo）を追加。
  - 検証: pytest 全体 390 passed / 4 failed。4 件は本件と無関係な既存失敗（変更前でも同一 4 件が失敗することを確認済み）。

### [低-2] --serve と他の処理オプションの併用時の挙動が変わった
- 場所: main.py 16〜31 行
- 内容: 従来は --serve が処理（--input-json / --image-name など）の後に実行されていた。今は serve 分岐が早期 return するため、併用時は処理が実行されない。
  PR 本文にこの挙動変更の記載がない。
- 修正方針: 意図どおりであれば PR 本文と .hermes/rules/cli.md に「--serve 時は他の処理オプションを無視する」と明記する。

### [低-3] 409 リトライでテストのフレークを隠している
- 場所: tests/test_web.py test_watch_picks_up_uploaded_image（PR 本文「409 リトライを追加」）
- 内容: 409 は仕様上の挙動であり、[中-3] の状態管理が未実装のために生じている。リトライで通すと、その隙間が検出されなくなる。
- 修正方針: [中-3] の修正後にリトライを外し、状態遷移を直接検証する。

### [情報] 本 PR の範囲外にある既存問題（PR 本文でも記載）
- move_to_processed に呼び出し元がない: app/services/file_repository.py:52 の定義のみ（grep で確認）。成功画像が input_dir に残る。
- 読込不能ファイルの毎サイクル再処理（mtime ガードが効かない）: PR 本文記載。本レビューでは再現していない。

## 4. 改善提案

1. [高-1] を最優先で修正する。一時ファイル→検証→原子的 rename の順にし、既存ファイルを上書き・削除しない。
2. stop_watch / start_watch を、スレッドごとの stop Event と生存確認で作り直す（[中-1]）。
3. 処理中の状態（処理待ち含む）をサーバ側に持ち、/images と /config（db_file）を一貫して制御する（[中-2][中-3]）。
4. テストは tmp_path 配下で完結させ、既存同名 DB のケースを追加する（[低-1]）。
5. PR 本文に --serve の挙動変更と、非 loopback 起動時のリスクを追記する（[低-2][中-4]）。

## 5. 検証

実行したもの:
- gh pr view 57 / gh issue view 56 / git diff origin/main...HEAD（15 ファイル、+3082 / -20）。PR の changed files と一致。
- PR head（e315128）の worktree で pytest tests/test_watch_controller.py tests/test_web.py: 71 passed（PR 本文の値と整合）。
- 再現スクリプト（リポジトリ外 scratch で実行。repo の data/ は変更していない）:
  - [高-1] ケース 1 / ケース 2 を再現（既存 DB 消失・置換）。
  - [中-1] stop/start 競合を再現（生存スレッド 2 本）。
- グラフ（better-code-review-graph, repo_root 明示）:
  - review(context): 15 files / 128 changed nodes / 19 impacted nodes（11 files）。影響先は watcher.py, db_migrations.py, db.py, structural_parser.py, processor.py, args.py など依存先。
    「51 関数にテストがない」との指摘は、_watch_loop / copy_image / _run_scan に対応するテストが存在する（test_watch_loop_calls_scan_repeatedly, test_copy_image_writes_to_input_dir など）ため、そのまま採用しない。
  - query(impact): 0 件。changed_files を指定しなかったため作業ツリー基準（削除済みの .vscode/mcp.json など PR 外のファイル）になっており、PR の影響範囲としては使えない。影響判定はソース読解で代替した。
- 未実施: 全テストスイート（PR 本文の「381 passed / 4 failed」は未再現）、ブラウザでの実機操作確認（PR 本文では実施済みと記載、本レビューでは未確認）。

スコープ外として扱ったもの:
- AGENTS.md, .hermes/rules/cli.md, docs/architecture/*（レビュー規則によりスコープ外）。
- 作業ツリーの未コミット変更（.vscode/mcp.json の削除、requirements.txt, test_logic*.py などの untracked）は PR 差分に含まれないため指摘対象外。
