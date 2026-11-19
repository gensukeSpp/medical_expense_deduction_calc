# Issue #56: 操作をコマンド主体から Web UI に

## 目的

現状の利用方法は `uv run ...` の CLI コマンド主体であり、第三者利用や長いコマンド入力の煩わしさがある。これを解消するため、**OCR 読み込み準備（watch 機能）・前処理指定・画像指定・DB 選択** を Web UI（`--serve` 起動のみ）から行えるようにする。CLI の `--input-dir` / `--db-path` / `--watch` / `--preprocess-mode` / `--image-name` に相当する操作をブラウザで完結させる。

## ユーザーストーリー（Issue 本文より）

1. Web 起動のみ `uv run`（`--serve`）
2. 画像を読み込む空のフォルダを指定（`--input-dir` にあたる）
3. 任意のフォルダを指定し、データベースを選択、もしくは作成（`--db-path`）
4. フォルダ監視ボタンをクリック（`--watch` 状態）
5. 前処理を選択する（`--preprocess-mode`）
6. 読み込む画像ファイルを指定（`--image-name`）
7. 一覧に読み込んだ内容のリンクが付け足される（`*-structured_data.json` が生成）

## 機能要件・受け入れ要件の対応先

| 要件 | 実装方針 |
|------|---------|
| watch 状態・process（structured data 生成中）が見える | 状態バー（横いっぱい）に `watch: ON/OFF / process: 実行中/待機中` を表示。JS ポーリングで更新 |
| `<input type='file'>` で指定したファイルを指定フォルダへ**コピー** | `POST /images` が input_dir へバイト列保存（**即時処理しない = 相談の回答 案C**） |
| 前処理をセレクトボックスで選択（none / clahe / adaptive / clahe+adaptive） | `POST /config` の `preprocess_mode`。選択肢は `app/args.py` の choices と同一 |
| 複数ファイル同時読み込みの注意書き + 生成完了までファイル指定不可 | 注意書き文をパネル内に表示。`is_processing` 中は JS が file input を `disabled` 化 |
| 生成完了後、非同期で一覧にリンクが表示される | 2 秒間隔の `/api/status` ポーリング。`is_processing` が true→false に遷移したらページリロード（一覧が再描画され新リンクが出る） |
| 出力 json / 前処理画像はアプリ内デフォルトのまま | `output_json/` `processed/` `failed/` は CLI と同じデフォルトを使用。UI 変更なし |
| 配置: 一覧の**上部**に操作パネル、一段下げて状態表示スペース | `index.html` の一覧テーブル上にパネル + 状態バーを追加 |

**受け入れ要件**

1. Web ページが開いている状態から watch（フォルダ監視）が動作できる → `POST /watch/start` でバックグラウンドのポーリングスレッド開始
2. **DB ファイル指定なし** で watch 状態・画像指定した場合はエラー → `POST /watch/start` と `POST /images` は `controller.has_db == False` なら HTTP 400
3. structured data 生成中、ファイル指定を表示・`<input type='file'>` とも操作不能にし、生成後は解除（watch は off まで継続） → JS ポーリングが `disabled` 属性を制御。watch スレッドは処理と独立に継続

## スコープ外（Issue 本文より）

- `--raw` `--structured` `--force` 他（date anchor 座標値強制の検証ツール用フラグ）
- 対象 structured data の Web UI からの削除
- `--preprocess-force` の UI 化（Issue のセレクトボックスは 4 モードのみ。常に False）

## 相談事項（ユーザー回答済み / 既定）

| # | 質問 | 回答 |
|---|------|------|
| Q1 | 画像アップロード後の処理タイミング（watch OFF 時） | **案C**: 指定フォルダへ**コピーするだけ**。処理は watch ON 後のポーリングで拾う |
| Q2 | アップロードした DB ファイルの保存先 | **案A**: アプリ内 `data/` ディレクトリに元ファイル名で保存（gitignore 済み・CLI の `data/db.sqlite3` と同じ場所） |
| Q3 | watch 設定のサーバ再起動後の保持 | **案A（ユーザー確認済み）**: プロセス内メモリのみ。再起動でリセット。DB スキーマ変更なし（YAGNI）。settings 永続化は必要になれば別 Issue 化 |

## 設計方針（対策の取捨選択）

| 観点 | 決定 | 理由 |
|------|------|------|
| 状態管理 | 新規 `app/services/watch_controller.py`（WatchController）が**プロセス内メモリ**で保持 | SRP。server.py を薄く保つ。DB スキーマ変更なし |
| watch 実装 | **ポーリング方式**（`watcher.scan_and_process` をバックグラウンドスレッドで周期的実行） | watchdog は自前スレッドで停止制御が難しい。ポーリングは `threading.Event` で安全に停止可能。既存 `run_loop` は無限ループのため**再利用せず**、`scan_and_process` を直接叩く新ループを持つ |
| 二重処理防止 | 既存の mtime ベース出力名 + `output_json_path.exists()` ガード（`ReceiptProcessor._sync_process`）に依存 | 同じ画像の再アップロードはスキップされる。新規ロジック不要 |
| 多重スキャン防止 | `_scan_lock`（非ブロッキング acquire）で `is_processing` 中はスキャン開始をスキップ | watch ループと画像コピー後のポーリングが重ならない |
| OCR 初期化 | **遅延初期化**（初回スキャン時に `PaddleOCR` 生成）。`ocr_factory` でテスト注入可能 | `--serve` 起動を高速化。テストで PaddleOCR を起動しない |
| 非同期更新 | プレーン HTML フォーム（POST + 303）+ `fetch('/api/status')` 2 秒ポーリング | 本機能は htmx 不要（`list.html` の htmx は既存機能）。遷移検知で `location.reload()` が最も単純 |
| DB 反映 | `get_db_path()` / サービス DI を**コントローラの現在値**から解決 | 実行中に `/config` で DB を差し替えても、修正・管理 API が新 DB を参照する |
| 前処理モード | `app/args.py` の choices と同一の 4 値のみ。`preprocess_force` は非公開 | スコープ外。バリデーションは `WatchController.set_preprocess_mode` |
| アップロード検証 | 拡張子を `watcher.IMAGE_EXT` で検証（非画像は 400） | ポーリングで無視されるゴミファイルが input_dir に残るのを防ぐ |

## ファイル変更表

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `app/services/watch_controller.py` | **新規** | 設定・watch スレッド・DB 保存・画像コピー・状態取得のオーケストレーター |
| `app/web/server.py` | 修正 | `create_app` に `input_dir` / `processed_dir` / `failed_dir` / `controller` 引数追加 + `/config` `/watch/start` `/watch/stop` `/images` `/api/status` ルート追加 + `get_db_path` をコントローラ参照に変更 |
| `app/web/templates/index.html` | 修正 | 操作パネル + 状態バー + ポーリング JS を一覧の上部に追加 |
| `main.py` | 修正 | `--serve` 時は PaddleOCR を初期化せず `create_app` へディレクトリを渡す（OCR は遅延初期化） |
| `tests/test_watch_controller.py` | **新規** | WatchController 単体テスト（設定・スレッド・コピー・DB 保存・状態） |
| `tests/test_web.py` | 修正 | `/config` `/watch/*` `/images` `/api/status` と index.html 表示の結合テスト |
| `specs/command-to-webui-spec.md` | **新規** | 実装後ドキュメント（動作・決定事項・受け入れ対応） |
| `docs/architecture/2026-11-16-webui-operations.md` | **新規** | アーキテクチャスナップショット（README インデックス行追加） |
| `AGENTS.md` / `.hermes/rules/cli.md` | 修正 | ディレクトリ構成・Web UI 運用の追記 |

## 実装順序（推奨）

Task 1（コントローラ設定）→ Task 2（watch スレッド）→ Task 3（DB/画像/状態）→ Task 4（server.py）→ Task 5（index.html）→ Task 6（main.py）→ Task 7（ドキュメント）

詳細は `tasks.md`、テスト詳細は `test_plan.md` 参照。

## リスク・未確定事項

- **Q3 案A（メモリのみ）**: 再起動で watch 設定がリセットされる（ユーザー確認済み）。必要になれば settings 永続化を別 Issue 化
- **watch スキャン中の停止**: `stop_watch` はスキャン中の OCR 実行を待たず timeout で戻る（daemon スレッド）。is_processing はスキャン完了時に必ず False へ戻る
- **OCR 初回初期化の遅延**: 初回スキャンは PaddleOCR ロード（数秒〜十数秒）が `is_processing=True` に含まれる。状態バーに「実行中」と表示されるため許容
- **既存の pre-existing テスト失敗**: `tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs` 等は origin/main でも失敗（本 Issue と無関係）。全テスト実行時は差分を確認すること
