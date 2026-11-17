---
title: コマンド主体から Web UI へ (Command-to-WebUI)
created: 2026-11-16
updated: 2026-11-16
issue: 56
---

# コマンド主体から Web UI へ — 仕様

## 背景
CLI コマンド主体の操作を Web UI（`--serve` のみ）で完結させる。

## フロー
1. `uv run python main.py --serve [--db-path ...]` で起動（OCR は遅延初期化）
2. index.html の操作パネルで 指定フォルダ / 前処理 / DB（アップロード）を設定
3. watch ON でポーリング監視開始（DB 未選択なら 400）
4. 画像をアップロード → input_dir へ**コピーのみ**（案C）。watch のポーリングが処理
5. 処理完了でページが自動リロードされ一覧にリンクが追加

## エンドポイント
| ルート | メソッド | 動作 | エラー |
|--------|---------|------|--------|
| `/config` | POST | input_dir / preprocess_mode / db_file 設定 | 400 (mode, DB 不正) |
| `/watch/start` | POST | 監視開始 | 400 (DB 未選択) |
| `/watch/stop` | POST | 監視停止 | - |
| `/images` | POST | 画像を input_dir へコピー | 400 (DB 未選択・非画像), 409 (処理中) |
| `/api/status` | GET | 状態 JSON（ポーリング） | - |

## 状態（メモリ保持・再起動でリセット）
watch_on / is_processing / input_dir / db_path / preprocess_mode / poll_interval(5s) / last_error

## 決定事項
| 項目 | 決定 | 理由 |
|------|------|------|
| アップロード画像 | コピーのみ・即時処理しない（案C） | ユーザー回答 |
| DB 保存先 | アプリ内 data/ に元ファイル名（案A） | ユーザー回答・gitignore 済み |
| 状態永続化 | プロセス内メモリのみ（案A） | YAGNI・スキーマ変更なし |
| watch | ポーリング方式（scan_and_process 再利用） | 停止制御が容易 |
| 多重処理防止 | _scan_lock + 既存 mtime 冪等ガード | 二重スキャン・二重登録を防止 |

## 受け入れ要件との対応
1. Web ページから watch 動作 → POST /watch/start + /api/status で確認
2. DB なしで watch/画像指定 → 400
3. 処理中は file input disabled、完了で解除（watch 継続）→ JS ポーリング制御
