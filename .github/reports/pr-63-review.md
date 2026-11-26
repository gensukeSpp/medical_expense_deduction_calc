# PR #63 レビュー

## 確認範囲・前提

- PR: [#63 fix #261126: Issue #62 正規化改良と DB ファイル選択と新規作成](https://github.com/gensukeSpp/medical_expense_deduction_calc/pull/63)
- 比較対象: `origin/main...HEAD`
- 関連 Issue: #62（OPEN）。「患者」から「氏名」への変更、先頭の `¥`、Web UI の DB 選択動作改善が目的。実装は既存互換性のため「患者」もフォールバックとして維持しており、この設計差は `tasks/issue_62/plan.md` と `tasks/issue_62/tasks.md` に説明されている。
- 確認した計画資料: `tasks/issue_62/plan.md`, `tasks/issue_62/tasks.md`
- `tasks/issue_62/overview.md`, `architecture.md`, `test-plan.md` は存在しない。
- 確認した仕様・関連資料: `specs/db-select-path-spec.md`, 既存の `specs/command-to-webui-spec.md`, `docs/field-normalization-amount.md`, `docs/field-normalization-name.md`, `docs/adr/001_date-anchor-abandoned-topmost-maintained.md`, `date anchor 後の設計方針.md`
- `docs/architecture/*` は本レビューの対象外として参照していない。
- better-code-review-graph: ローカル CLI の `status` と depth 1 の `impact` を利用。スナップショットは `2026-11-26T14:21:02`、PR HEAD `1c8a90a` と一致。4つの変更 Python ファイルから13ファイルが影響候補として挙がり、テスト・呼び出し側をソースで確認した。指定された MCP の `review(action="context")` は利用可能なツールとして見つからなかったため、CLI の impact 結果で代替した。
- 検証: `tests/test_llm_wrapper.py`, `tests/test_normalization.py`, `tests/test_watch_controller.py`, `tests/test_web.py` — **123 passed**。

## 総評

氏名抽出と金額正規化の補強、DB のアップロード／連番コピーを廃止してサーバ側パスを直接参照する変更は、Issue #62 の目的に沿っています。DB 選択と新規作成の lock ガードや既存動作のテストも維持されています。一方、「新規作成」が既存ファイルを拒否せず migration する点と、DB パスの入力エラーが HTTP 500 になる経路は修正が必要です。また、既存 Web UI 仕様に旧アップロード方式が残っています。

## 良い点

- `select_db_path` と `create_db_path` を分け、既存 DB の検証と初回 DB 作成の責務を明確にしている。
- DB 切替時の `_scan_lock` と busy 応答を維持し、スキャン中の切替を拒否するテストがある。
- `¥-500` の負値拒否を記号除去より先に保つ回帰テストが追加されている。
- 関連する4つのテストモジュールを実行し、すべて通過した。

## 指摘事項

### [P2] 「新規作成」が既存 DB を変更してしまう

- 場所: `app/services/watch_controller.py:413-422`
- `create_db_path()` は絶対パスだけを検証し、既存ファイルの有無を確認せず `_run_migrations()` を呼びます。画面の「新規作成」は入力欄に現在の DB パスが表示された状態でも押せるため、既存 DB を指定して押すと、その DB にアプリのテーブル／migration が適用され、既存 DB がそのまま使用対象になります。空の DB を作る操作としては予想外の副作用です。未管理の SQLite ファイルを指定した場合にも、既存ファイルへアプリのスキーマが追加されることを一時 SQLite DB で確認しました。
- 望ましい対応: 新規作成時は既存ファイルを拒否し、作成時の競合も含めて既存ファイルを上書き・変更しない方式にしてください。仕様にも「既存パスを指定した場合はエラー」と明記するのが望ましいです。
- 補足: 現行タスク／spec は意図的に存在チェックを省く設計を記載していますが、その前提が「新規作成」操作のデータ保護を損なっています。

### [P2] ディレクトリを DB パスに指定すると HTTP 500 になる

- 場所: `app/services/watch_controller.py:376`、`app/web/server.py:298-306`
- `/config` の `db_path` に既存ディレクトリを指定すると、`_validate_db_file()` の `open(path, "rb")` が `IsADirectoryError` を送出します。`/config` は `ValueError` と `WatchBusyError` のみを HTTP エラーへ変換するため、この入力は 400 ではなく未処理例外（HTTP 500）になります。ディレクトリ指定をコントローラへ渡す再現でも `IsADirectoryError` を確認しました。
- 望ましい対応: ファイルであることを事前確認し、`OSError` を利用者向けの `ValueError`（HTTP 400）へ変換してください。アクセス不能などの I/O エラーも同じ境界で扱うと、誤入力時にサーバーエラーを返さずに済みます。

### [P3] 既存の Web UI 仕様が旧 DB アップロード方式のまま

- 場所: `specs/command-to-webui-spec.md:15,23` および「決定事項」の「DB 保存先」
- この仕様は DB を「ファイル選択で即アップロード」とし、`/config` の `db_file` を設定する記述を残しています。PR の実装および新しい `specs/db-select-path-spec.md` はサーバ側パス入力の `db_path`／`db_create_path` に移行しており、両資料が矛盾します。
- 望ましい対応: 生きている仕様として、旧仕様を新しい DB パス選択仕様へ更新するか、旧記述が superseded であることを明記してください。文書の PR 分離や同梱自体は本指摘の対象ではありません。

## 改善提案

- DB 選択テストに「既存パスで `create_db_path` が拒否され、ファイル内容と現在の DB 設定が変わらない」ケースを追加してください。
- ディレクトリ／権限不足などの不正 DB パスに対して、コントローラ例外と `/config` の HTTP 応答を検証するテストを追加してください。
