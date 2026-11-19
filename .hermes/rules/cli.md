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
- 操作パネルは**選択＝確定**（JS fetch で即時適用・画面遷移なし）:
  - 指定フォルダ: パスを直接入力して「適用」（ブラウザのダイアログからはサーバ側パスが取得できないため）
  - 前処理: セレクト変更で即適用（`none`/`clahe`/`adaptive`/`clahe+adaptive`）
  - データベース: ファイル選択で即アップロード（`data/` へ保存 + migration 適用。失敗時はパネル内にエラー表示）
    - 保存は「一時ファイル → 検証 → 原子的配置」の順。SQLite ヘッダと必須テーブル
      （`receipts`/`clinics`/`templates`/`corrections`）を検証し、不正なら保存せず 400
    - 同名の既存 DB は**上書き・削除しない**。連番（`my_1.db` 等）で別名保存する
  - watch ON/OFF ボタン + 状態バー（2秒ポーリングで更新）
- 画像アップロードは指定フォルダへ**コピーのみ**＋ watch 未 ON なら自動開始（処理は watch のポーリングが実施）
- watch 開始・画像指定は DB 選択必須（未選択なら 400）
- 処理中はファイル指定が disabled。新しい structured data 生成時に一覧が自動リロード
- 状態はプロセス内メモリのみ（サーバ再起動でリセット）
- `--serve` 時は他の処理オプションを無視する (PR #57)
- 起動は 127.0.0.1 (localhost) のみ。非 loopback host（`--host 0.0.0.0` 等）は認証がないため**起動拒否**される（Issue #58）
- 指定フォルダは**ホームディレクトリ配下**のみ指定可能（外部パスは 400）
- 個人利用・LAN 公開しない運用を前提とする
- 処理中は DB ファイル選択も disabled（サーバ側 409）。画像指定は処理中・処理待ち（コピー後〜生成完了）で disabled（409）
- 同名画像の再読み込みは 409（上書きしない）
