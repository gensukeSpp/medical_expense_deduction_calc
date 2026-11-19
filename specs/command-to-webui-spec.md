---
title: コマンド主体から Web UI へ (Command-to-WebUI)
created: 2026-11-16
updated: 2026-11-19
issue: 56
---

# コマンド主体から Web UI へ — 仕様

## 背景
CLI コマンド主体の操作を Web UI（`--serve` のみ）で完結させる。

## フロー
1. `uv run python main.py --serve [--db-path ...]` で起動（OCR は遅延初期化）
2. パネルで 指定フォルダ（入力→適用）/ 前処理（選択で即適用）/ DB（ファイル選択で即アップロード）を設定
3. watch ON でポーリング監視開始（DB 未選択なら 400）
4. 画像を読み込む → input_dir へ**コピーのみ**＋ watch 未 ON なら自動開始
5. structured data 生成（structured_count 増加）で一覧が自動リロードされリンク表示

## エンドポイント
| ルート | メソッド | 動作 | エラー |
|--------|---------|------|--------|
| `/config` | POST | input_dir / preprocess_mode / db_file 設定 | 400 (mode, DB 不正, パス不正), 409 (処理中 db_file) |
| `/watch/start` | POST | 監視開始 | 400 (DB 未選択) |
| `/watch/stop` | POST | 監視停止 | - |
| `/images` | POST | 画像を input_dir へコピー（＋ watch 自動開始） | 400 (DB 未選択・非画像), 409 (処理中・処理待ち・同名) |
| `/api/status` | GET | 状態 JSON（ポーリング。structured_count 含む） | - |

## 状態（メモリ保持・再起動でリセット）
watch_on / is_processing / pending / input_dir / db_path / preprocess_mode / poll_interval(5s) / last_error / structured_count

- `pending`: 画像コピー後〜 structured data 生成完了までの「処理待ち」状態。解除条件は
  出力 JSON 生成 or failed/ への退避（読込不能画像でも永久ロックしない）

## 決定事項
| 項目 | 決定 | 理由 |
|------|------|------|
| アップロード画像 | コピーのみ＋ watch 自動開始（方式はポーリング） | ユーザー回答（案C）＋「読み込む＝処理開始」の期待 |
| DB 保存先 | アプリ内 data/ に元ファイル名（案A） | ユーザー回答・gitignore 済み |
| 状態永続化 | プロセス内メモリのみ（案A） | YAGNI・スキーマ変更なし |
| watch | ポーリング方式（scan_and_process 再利用） | 停止制御が容易 |
| 多重処理防止 | _scan_lock + 既存 mtime 冪等ガード | 二重スキャン・二重登録を防止 |
| UI 操作モデル | 選択＝確定（fetch 即時適用・画面遷移なし） | フォーム送信モデルが直感的でないとの運用フィードバック |
| 完了検知 | structured_count 増加時のみリロード（メッセージは sessionStorage で復元） | is_processing 遷移だとスキャン毎にリロードされ選択状態が消える |
| 指定フォルダ | テキスト入力（ダイアログ不可） | FileSystemDirectoryHandle はサーバ側パスを返せない |
| 非 loopback 起動 | 起動拒否（--host 0.0.0.0 はエラーで即終了） | 認証がないため LAN 曝露を構造的に防止 |
| input_dir 制限 | ホームディレクトリ配下のみ（他は 400） | 任意パス操作の防止 |
| watch スレッド | スレッドごとに専用 stop Event | 停止→再開で旧スレッドの停止要求が消えて二重化するのを防止 |
| DB 差し替え | 処理中は 409 | 書き込み先 DB が途中で変わる競合の防止 |
| 同名画像 | 409（上書きしない・連番保存もしない） | 上書きによる画像損失と、連番による再処理・重複登録の防止 |

## 受け入れ要件との対応
1. Web ページから watch 動作 → POST /watch/start + /api/status で確認
2. DB なしで watch/画像指定 → 400
3. 処理中は file input disabled、完了で解除（watch 継続）→ JS ポーリング制御
4. 処理中の DB 差し替えは 409（[中-2]）→ POST /config の db_file 分岐のガード
5. 画像コピー後〜 structured 生成完了まで画像指定は無効（[中-3]）→ pending 状態 + /images の 409
6. 同名画像は上書きせず 409（[中-5]）→ copy_image の FileExistsError
7. 非 loopback host での起動は拒否（[中-4]）→ app.args.is_loopback_host() + main.py の serve 分岐
8. ホーム配下以外の input_dir は 400（[中-4]）→ WatchController.set_input_dir() のガード
