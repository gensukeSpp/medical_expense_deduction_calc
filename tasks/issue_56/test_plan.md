# Issue #56: テスト計画

## テスト戦略

- **フレームワーク**: pytest（`uv run pytest`）
- **新規テストファイル**: `tests/test_watch_controller.py` — WatchController 単体テスト
- **既存テスト拡張**: `tests/test_web.py` — `/config` `/watch/*` `/images` `/api/status` と index.html 表示の結合テスト
- **PaddleOCR を起動しない**: `ocr_factory=lambda: object()` を注入。実 OCR は Task 6 の手動確認のみ
- **DB は migration 済み実ファイル**: `run_migrations` でスキーマ適用した一時 SQLite を使用
- **watch スレッドのタイミング**: `poll_interval` を 0.05〜1 秒に縮め、`threading.Event` で同期
- **fake scan_fn**: input_dir の画像ごとに `*-structured_data.json` を生成する偽スキャン

## 受け入れ要件との対応

| 受け入れ要件 | テスト |
|-------------|--------|
| 1. Web ページから watch が動作 | `test_start_stop_watch_toggles` / `test_watch_start_stop_toggles` / `test_watch_loop_calls_scan_repeatedly` |
| 2. DB なし + watch/画像指定でエラー | `test_start_watch_requires_db` / `test_watch_start_without_db_400` / `test_upload_image_without_db_400` |
| 3. 処理中は file input 操作不能 → 完了で解除 | `test_scan_sets_processing_flag` / `test_file_input_disabled_while_processing` / `test_file_input_enabled_after_processing` / `test_watch_picks_up_uploaded_image`（watch 継続 + 一覧リンク追加） |

## 機能要件との対応

| 機能要件 | テスト |
|----------|--------|
| watch/process 状態が見える | `test_get_status_shape` / `test_status_endpoint_json` / `test_index_has_status_poll_script` |
| 画像を指定フォルダへコピー | `test_copy_image_writes_to_input_dir` / `test_upload_image_copies_to_input_dir` |
| 前処理セレクト 4 択 | `test_index_shows_control_panel`（`clahe+adaptive` を含む） / `test_set_preprocess_mode_valid` / `test_config_rejects_unknown_mode` |
| 注意書き + 処理中は指定不可 | `test_index_shows_control_panel`（`1枚ずつ`）/ `test_upload_image_without_db_400` / 409 ガード |
| 生成完了で一覧にリンク追加 | `test_watch_picks_up_uploaded_image` |
| 出力 json / 前処理画像はデフォルトのまま | （設計判断。テスト対象外 — output_dir 等は CLI デフォルトと同一） |
| パネル配置（一覧上部・状態表示スペース） | `test_index_shows_control_panel` / `test_index_shows_db_unselected_hint` |

---

## テストケース一覧

### `tests/test_watch_controller.py`（新規・20 件）

#### 設定管理（Task 1）

| TC | テスト名 | 内容 |
|----|---------|------|
| 01 | `test_set_input_dir_creates_directory` | 存在しないパスが mkdir され状態が更新される |
| 02 | `test_default_input_dir_is_downloads_receipts` | デフォルトは `~/Downloads/receipts`（args.py と同一） |
| 03 | `test_set_db_path_runs_migrations` | db_path 設定時に migration が走りテーブルが作られる |
| 04 | `test_set_db_path_none_clears` | `None` で `has_db=False` |
| 05 | `test_set_preprocess_mode_valid` | 4 モード全て受理 |
| 06 | `test_set_preprocess_mode_invalid_raises` | 未知モードは `ValueError` |

#### watch スレッド（Task 2）

| TC | テスト名 | 内容 |
|----|---------|------|
| 07 | `test_start_watch_requires_db` | DB 未選択で `ValueError`（受け入れ要件 2） |
| 08 | `test_start_stop_watch_toggles` | start → `watch_on=True` / stop → `False` |
| 09 | `test_start_watch_idempotent` | 二重 start は no-op（スレッド 1 つ） |
| 10 | `test_scan_sets_processing_flag` | スキャン中 `is_processing=True`、終了後 `False`（受け入れ要件 3） |
| 11 | `test_scan_skips_when_already_processing` | 処理中は次のスキャンを開始しない（多重防止） |
| 12 | `test_watch_loop_calls_scan_repeatedly` | ポーリングで scan_fn が複数回呼ばれる |

#### DB 保存 / 画像コピー / 状態（Task 3）

| TC | テスト名 | 内容 |
|----|---------|------|
| 13 | `test_save_db_upload_migrates_and_sets_db_path` | `data/` 保存 + migration 適用 + `has_db=True`（Q2 案A） |
| 14 | `test_save_db_upload_sanitizes_filename` | パス注入（`../../evil.db`）を basename に丸める |
| 15 | `test_save_db_upload_invalid_file_raises` | DB でないバイト列は例外 + 保存先を掃除 |
| 16 | `test_copy_image_writes_to_input_dir` | 画像バイトが input_dir に保存される |
| 17 | `test_copy_image_rejects_non_image` | 非画像拡張子は `ValueError` |
| 18 | `test_get_status_shape` | status dict のキー集合が正しい |

（残り 2 件は Task 2 の `test_scan_sets_processing_flag` 系カウントに含まれる実装差分による。実装後 `uv run pytest tests/test_watch_controller.py -q` の pass 数が最終確定値。）

### `tests/test_web.py`（既存 + 新規）

#### Issue #56 結合テスト（Task 4・5）

| TC | テスト名 | 内容 |
|----|---------|------|
| 19 | `test_index_shows_control_panel` | パネル要素・前処理 4 択・注意書き・状態バー |
| 20 | `test_config_updates_input_dir_and_mode` | `/config` で 303 → 表示に反映 |
| 21 | `test_config_rejects_unknown_mode` | 未知モード 400 |
| 22 | `test_config_db_upload_saves_and_migrates` | DB アップロード → `data/` 保存 + `has_db=True` |
| 23 | `test_watch_start_without_db_400` | 受け入れ要件 2（watch start） |
| 24 | `test_watch_start_stop_toggles` | `/api/status` の `watch_on` が反転 |
| 25 | `test_upload_image_without_db_400` | 受け入れ要件 2（画像指定） |
| 26 | `test_upload_image_copies_to_input_dir` | 案C: コピーのみ・即時処理しない |
| 27 | `test_upload_rejects_non_image` | 非画像 400 |
| 28 | `test_status_endpoint_json` | `/api/status` の JSON 形状 |
| 29 | `test_watch_picks_up_uploaded_image` | watch ON 中アップロード → ポーリング処理 → 一覧にリンク（受け入れ要件 1・3 統合） |
| 30 | `test_file_input_disabled_while_processing` | 受け入れ要件 3（disabled 属性） |
| 31 | `test_file_input_enabled_after_processing` | 受け入れ要件 3（解除） |
| 32 | `test_index_shows_db_unselected_hint` | DB 未選択時に「未選択」表示 |
| 33 | `test_index_has_status_poll_script` | ポーリング JS（`/api/status`・`pollStatus`）が含まれる |

## テスト実行コマンド

```bash
# Task 1〜3: コントローラ単体
uv run pytest tests/test_watch_controller.py -q            # 期待: 20 passed

# Task 4: エンドポイント
uv run pytest tests/test_web.py -k "control_panel or config or watch or upload or status" -q

# Task 5: index.html 表示
uv run pytest tests/test_web.py -k "file_input or unselected or poll_script" -q   # 期待: 4 passed

# 全体
uv run pytest -q
```

## 既知の pre-existing 失敗（本 Issue と無関係）

- `tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs` — INFO ログを caplog で捕捉できないため常時失敗（origin/main でも同様）
- `tests/test_image_processing_service.py::TestProcess::test_happy_path` — キーワード引数と位置引数の不一致による mock 検証失敗（origin/main でも同様）

全テスト実行時は上記 2 件の失敗のみであることを確認する。疑わしい場合は
`git stash push -- <該当ファイル>` → `uv run pytest <該当テスト>` → `git stash pop` で再確認する。

## カバレッジ補完（任意）

```bash
uvx --python 3.11 --with pytest --with pytest-asyncio coverage run --source=app.services.watch_controller -m pytest tests/test_watch_controller.py -q
uvx --python 3.11 coverage report --show-missing
```

防御的コード（`last_error` の例外分岐等）で未到達行が出る場合は、無理にテストを歪めず
「到達不能な防御コード」として報告してよい。
