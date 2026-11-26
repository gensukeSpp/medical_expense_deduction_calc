# DB パス選択（案A）仕様 — Issue #62

## 背景

Issue #56 の Web UI DB 選択はファイルアップロード → `data/` へ保存（同名は連番
`name_1.db` 生成）という設計だった。その結果、検証のたびに `data/` 配下へ DB の
コピーが増殖する問題があった。

また初回起動時は参照する DB ファイルが存在せず「選択」できない、という指摘があった
（2026-11-26 ユーザー確認）。

## 決定（案A・ユーザー回答済み）

DB 選択は**サーバ側パスのテキスト入力 + 適用**に変更し、既存ファイルをそのまま参照する。
アップロード機能は削除。さらに**新規作成**経路を追加する。

- `WatchController.select_db_path(path)` — 既存参照系
  - 絶対パス必須（相対パスは ValueError「絶対パスを指定してください」）
  - 存在チェック必須（無ければ ValueError「ファイルが存在しません」）
  - SQLite ヘッダ + 必須テーブル検証（`_validate_db_file`、旧 `_validate_uploaded_db` を流用）
  - 検証後に migration を適用し `_db_path` に設定
- `WatchController.create_db_path(path)` — 新規作成系（初回起動対応）
  - 絶対パス必須。**既存ファイル／ディレクトリを指定した場合は ValueError**（既存 DB を
    上書き・変更しないため。PR #63 レビュー [P2]#1 対応）
  - 存在確認と作成の競合を防ぐため `os.open(O_CREAT|O_EXCL)` で空ファイルを原子的に作成し、
    その後 `_run_migrations` を適用する。migration 失敗時は作成したファイルを削除する
- 両者とも `[PR59-中3]` の `_scan_lock` ガード（スキャン中は `WatchBusyError` → 409）を維持

## 変更点

| 対象 | 変更 |
|------|------|
| `app/services/watch_controller.py` | `select_db_path` / `create_db_path` / `_validate_db_file` 追加。`save_db_upload` / `_install_db` / `_atomic_install` / `uuid4` import 削除。`data_dir` 引数は `create_app` シグネチャ互換のため残置（未使用化） |
| `app/web/server.py` `/config` | `db_file`（UploadFile）分岐を削除し、`db_path` / `db_create_path`（テキスト）分岐に置換。`WatchBusyError` → 409、`ValueError` → 400 |
| `app/web/templates/index.html` | DB ファイル input をパステキスト input +「適用」/「新規作成」ボタンに変更。JS は `postDbPath(field)` で `db_path` / `db_create_path` を送信 |
| 既存 `set_db_path` | 変更しない（`__init__` / `create_app(db_path=...)` の CLI 互換用。migration でファイルを生成する振る舞いを維持） |

## 動作

1. 「適用」: 入力パスの既存 DB を検証 + migration して参照対象にする。`data/` への
   コピー・連番は**一切作られない**（増殖問題の解消）。
2. 「新規作成」: 入力パスが既存ファイル／ディレクトリの場合はエラー（HTTP 400）。
   存在しないパスへ空 DB を新規作成し参照対象にする（初回起動の解消）。
3. 処理中（`is_processing`）の変更は 409 で拒否される（`[PR59-中3]` の原子性維持）。

## 検証結果（2026-11-26 実施）

- `tests/test_llm_wrapper.py` → 6 passed（ベースライン 3 + 追加 3）
- `tests/test_normalization.py` → 12 passed（ベースライン 11 + 追加 1）
- `tests/test_watch_controller.py` → 40 passed（ベースライン 42 − upload 10 + 新規 8）
- `tests/test_web.py` → 65 passed（ベースライン 63 − upload 4 + 置換 4 + 追加 2）
  - 注: ベースライン計測後の upload 系結合テスト 4 件は本変更でパス選択系に置換
- `tests/test_watch_controller.py::test_stop_restart_no_duplicate_thread` は
  `test_web.py` と同時実行時に落ちる既存のフレーキー（ベースラインでも再現。本変更と無関係）
- `black app/ tests/` → 整形エラーなし

## レビュー対応（PR #63 [P2]#1）

初版は「存在チェックはしない（migration は冪等なので既存パスを渡しても安全）」としていたが、
画面の「新規作成」は入力欄に現在の DB パスが表示された状態でも押せるため、既存 DB を指定して
押すとその DB にアプリのテーブル／migration が適用され、既存 DB がそのまま使用対象になる
副作用があった（未管理の SQLite ファイルを指定した場合も同様）。2026-11-26:
`create_db_path` は既存ファイル／ディレクトリを ValueError で拒否し、`os.open(O_CREAT|O_EXCL)`
による原子的な空ファイル作成に変更した。これにより存在確認と作成の間にファイルが割り込む
競合でも既存ファイルを上書き・変更しない。migration 失敗時は作成したばかりのファイルを
削除する。

## ADR 001 との関係

`docs/adr/001_date-anchor-abandoned-topmost-maintained.md` の案 ii（抽出結果の空値に
過去の補正値を採用する）は、本 Issue で整備した正規化（氏名プレフィックス `氏名` 対応、
`¥` 付き金額の正規化）の上に、`template_history` 参照による採用基準を定めて実装する。
