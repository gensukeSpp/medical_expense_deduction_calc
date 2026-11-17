# CLI 使い方 & 引数一覧

> AGENTS.md から切り出した doc。引数が増えるたびにこのファイルを更新する。

## 使い方

```bash
# 単一画像処理
uv run python main.py --input-dir ~/Downloads/receipts --image-name IMG_001.jpg

# 単一画像処理 + 前処理(低Confidence時自動/強制)
uv run python main.py --input-dir ~/Downloads/receipts --image-name IMG_001.jpg \
    --preprocess-mode clahe+adaptive --preprocess-force

# 既存 OCR JSON から構造データ生成
uv run python main.py --input-json output_json/IMG-raw_data.json --db-path data/db.sqlite3

# フォルダ監視 (polling)
uv run python main.py --watch --input-dir ~/Downloads/receipts --poll-interval 10

# フォルダ監視 (watchdog inotify)
uv run python main.py --watch --use-watchdog --input-dir ~/Downloads/receipts

# Web UI 起動
uv run python main.py --serve --port 8000 --db-path data/db.sqlite3
```

## 引数一覧

| 引数                | 説明                                                             | デフォルト              |
| ------------------- | --------------------------------------------------------------- | ----------------------- |
| `--input-dir`       | 入力画像ディレクトリ                                             | `~/Downloads/receipts`  |
| `--image-name`      | 単一画像処理 (ファイル名)                                        | None                    |
| `--output-dir`      | JSON 出力先                                                      | `output_json`           |
| `--input-json`      | 既存 OCR JSON 処理                                               | None                    |
| `--model`           | LLM モデル名 or `mock`                                           | `mock`                  |
| `--watch`           | フォルダ監視モード                                               | False                   |
| `--use-watchdog`    | watchdog(inotify) 使用                                           | False                   |
| `--processed-dir`   | 処理済み画像先 / 前処理済み画像保存先                             | `processed`             |
| `--failed-dir`      | 失敗画像先                                                       | `failed`                |
| `--poll-interval`   | ポーリング間隔 (秒)                                              | 10                      |
| `--run-once`        | 1回スキャンして終了                                              | False                   |
| `--retries`         | 失敗時のリトライ回数                                             | 1                       |
| `--db-path`         | SQLite パス                                                      | None                    |
| `--serve`           | Web UI サーバー起動                                              | False                   |
| `--host`            | Web サーバーホスト                                               | `127.0.0.1`             |
| `--port`            | Web サーバーポート                                               | 8000                    |
| `--preprocess-mode`    | 前処理モード: `none`/`clahe`/`adaptive`/`clahe+adaptive`     | `none`                  |
| `--preprocess-force`   | Confidence に関係なく前処理を強制適用 (`--preprocess-mode`≠none時のみ) | False             |
| `--target-short-side`  | リサイズ時、短辺の目標サイズ (px)                                 | 960                     |

※ `--preprocess-*` / `--target-short-side` は Issue #36 (OCR 前処理) で追加。

## 実装場所

- 引数定義: `app/args.py::setup_args()`
- ディレクトリ初期化: `app/args.py::setup_directories()`

## Web UI 運用 (Issue #56)

- 起動: `uv run python main.py --serve [--port 8000] [--db-path data/db.sqlite3]`
- `--serve` では OCR を遅延初期化するため起動が高速。PaddleOCR は初回スキャン時に生成
- 操作パネル: 指定フォルダ(input_dir) / 前処理(preprocess_mode) / DB アップロード(data/ へ保存)
- 画像アップロードは指定フォルダへ**コピーのみ**（watch ON のポーリングが処理）
- watch 開始・画像指定は DB 選択必須（未選択なら 400）
- 処理中はファイル指定が disabled になり、完了で一覧が自動更新される
- 状態はプロセス内メモリのみ（サーバ再起動でリセット）
