# Issue #58: PR #57 レビュー指摘（競合制御・安全性）修正 — 実装計画

## 目的

PR #57（Issue #56「主要操作を Web UI に」）のレビュー指摘のうち、[中-1]〜[中-5] と [低-3] の計 6 件を修正し、watch スレッド・DB 差し替え・画像指定の競合制御と、非 loopback 起動時の安全性を確保する。高リスクの [高-1] は別対応済み（`.github/reports/pr-57-review.md` の 低-1 対応欄参照）、本 Issue スコープ外。

## 現状（コード読解の結果）

- `app/services/watch_controller.py`（Issue #56 で新設）が watch スレッド・DB 保存・画像コピー・状態を保持。`_stop_event` は `__init__` で 1 個生成し `start_watch` で `clear()` するため、停止→再開で旧スレッドの停止要求が消えて二重化する（[中-1] の直接原因）。
- `app/web/server.py` の `post_config` は `db_file` の処理中チェックなし（[中-2]）。`upload_image` は `is_processing` のみガードし、コピー後〜次スキャン開始までの「処理待ち」窓が空く（[中-3]）。`copy_image` は同名ファイルを無確認で `write_bytes` 上書きする（[中-5]）。
- `main.py` の serve 分岐は host 検証なし（[中-4]）。`set_input_dir` は任意パスに対して `mkdir(parents=True)` を実行する（[中-4]）。
- `tests/test_web.py::test_watch_picks_up_uploaded_image` は 409 をリトライでやり過ごす（[低-3]）。
- テスト現状: `tests/test_watch_controller.py`（25 件）+ `tests/test_web.py`（55 件）は 80 passed（2026-11-19 実測）。フルスイートは **394 passed / 0 failed**（PR #57 本文記載の「4 failed は pre-existing」は tasks/pr_53 で解消済み。本計画でさらに 15 件追加 → 409 になる見込み）。

## 対策の取捨選択（対応項目ごと）

| 項目 | 方針（採用） | 理由 |
|------|------------|------|
| [中-1] スレッド二重化 | **スレッドごとの専用 stop Event**。`start_watch` は毎回新しい `Event` を生成（旧 Event を clear しない）。`stop_watch` は join timeout 後も生存中なら `_thread` を保持（watch_on は生存中 True のまま）。`_watch_loop` は開始時に Event を捕捉 | レビュー修正方針に忠実。旧スレッドは自身の Event が SET 済みのため自己終了し、二重化が構造的に起きない。`_scan_lock` により重複スキャンも防止済み |
| [中-2] 処理中 DB 差し替え | `post_config` の `db_file` 分岐で `is_processing` なら **409**。UI は `db-file-input` を処理中 disabled（初期描画 + applyStatus） | 書き込み先 DB が途中で変わる競合をサーバ側で一貫拒否 |
| [中-3] 生成完了まで画像指定不可 | WatchController に **pending（処理待ち）状態**を追加。`copy_image` 成功で pending=True、スキャン 1 サイクル終了時に「処理待ち画像が消費された」（`{stem}_*-raw_data.json` / `-structured_data.json` 生成 or `failed/` へ退避）場合のみ解除。pending 中は `/images` 409 + UI disabled | 「コピー直後〜structured 生成完了」をサーバ状態として保持。消費判定に failed 退避を含めることで、読込不能画像でも永久ロックしない（既存の問題 1 との相互作用、後述） |
| [中-4] 非 loopback 起動 | **起動拒否**（`args.py` に `is_loopback_host()` を追加し、`main.py` の serve 分岐で非 loopback ならエラーメッセージ + return）。input_dir は**ホームディレクトリ配下に限定**（`set_input_dir` で検証、越えれば 400/ValueError）。個人利用・LAN 公開しない運用を `cli.md` と spec に明記 | 認証のない変更系エンドポイント群への曝露を構造的に防ぐ。拒否 vs 警告・ホーム限定 vs 絶対パスは 相談事項 Q1/Q2 で回答済み（いずれも案A を選択: 起動拒否・ホーム配下限定） |
| [中-5] 同名画像上書き | `copy_image` で同名既存ファイルがあれば **FileExistsError**（サーバは 409）。連番は採用しない | 画像は連番保存すると mtime が変わり**再処理されて受領書が重複登録**されるため、拒否が安全。DB は既存の連番保存（[高-1] 対応済み）を維持 |
| [低-3] 409 リトライ | `test_watch_picks_up_uploaded_image` からリトライループを除去。初回スキャン完了（アイドル）を待ってから 1 回 POST し、pending → structured 生成 → pending 解除を直接検証。409 は `test_images_409_while_pending` で仕様上の拒否として検証 | 中-3 の状態管理により「待って送り直す」必要が消える。状態遷移を直接見る |

## 「既存の問題 1」（読込不能ファイルの毎サイクル再処理）との関連性 — 判定と推奨

### 判定: 関連しない（Issue #58 の 6 項目は UI/競合制御が対象で、再処理ループは watcher 側の既存問題）

`gh pr view 57` の「検証中に見つけた既存の問題 1」=「raw_data.json が書かれない場合 mtime ガードが効かず、watch が 5 秒毎に再試行 → is_processing が点滅」。

発生経路（`app/services/receipt_processor.py::_sync_process` 読解）:
1. `ocr_pipeline.process_image()` は `resize_image_for_ocr` が None（読込不能画像）のとき**例外なく `[]` を返し、raw JSON を書かない**（ocr_pipeline.py:86-87 で早期 return）。
2. `_sync_process` は例外なしで続行 → `process_input_json` が存在しない raw パスで失敗（内部で捕捉）→ structured=None → **return True**。失敗扱いにならない。
3. 次サイクルの mtime ガード `output_json_path.exists()` が False のまま → 再処理 → 毎サイクル繰り返し。
（例外系失敗は `move_to_failed` が呼ばれるが、`file_repository.move_to_failed` は `shutil.copy2` のため元画像が input_dir に残り、やはり毎サイクル再試行される。）

Issue #58 の 6 項目のどれもこの再処理ループには触れない（[中-3] の pending は UI 側の入力制御であり、ループ自体は止まらない）。

### 相互作用（本計画で対処する点）

[中-3] の pending は「処理待ち画像が消費された（structured 生成 or failed 退避）」で解除する設計のため、**読込不能画像（raw 生成なし・failed 退避なし）は pending が解除されず画像指定が永久ロック**になる。→ 本計画では消費判定に failed 退避を含めることで「failed/ にコピー済みなら解除」とし、既存の問題 1 が未修正でも永久ロックは回避する（再処理ループ自体は残る）。

### 推奨: 「再試行が続く → エラーで止める（失敗ファイルを input_dir から退避）」が better

理由:
1. **再試行に成功の見込みがない**（ファイル内容は不変。OCR の一時的エラー以外は同じ結果）。毎サイクルの OCR 起動コスト（数秒〜十数秒）と `is_processing` の点滅（UI の input が一瞬 disabled になる）が無駄に続く。
2. **既存の失敗処理が copy2 のため元画像が残る**。`move_to_failed` を `shutil.move` に変えれば 1 サイクルでスキャン対象から外れ、mtime ガードに頼らない決定的な停止条件になる。
3. **データ損失がない**。failed/ に退避されるだけなので、ユーザーが目視確認・別名で再投入できる。
4. 完了条件（受領書が重複登録されない・エラーが可視化される）に整合する。

最小実装（別 Issue として提案、本計画の tasks.md には含めない）:
- `file_repository.move_to_failed` を `shutil.copy2` → `shutil.move` に変更（例外系失敗が即退避で停止）
- `_sync_process` で「`process_image` が空リストを返し raw 未生成」を失敗扱いにして `move_to_failed` を呼ぶ（`if not structured and not output_json_path.exists(): move_to_failed → return False` 等）

※ 関連の「既存の問題 2」（`move_to_processed` に呼び出し元が無く成功画像が input_dir に残る）は「処理済み画像を移動するか残すか」というプロダクト判断を含むため、本件とは別に扱う（本計画のスコープ外）。

## ファイル変更表

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `app/services/watch_controller.py` | 修正 | [中-1] 専用 stop Event / `stop_watch` の生存保持 / `_watch_loop` の Event 捕捉。 [中-3] `_pending_process` / `_pending_image` / `pending_process` プロパティ / `copy_image` で設定 / `_run_scan` 終了時 `_clear_pending_if_consumed()` / `get_status` に `pending`。 [中-5] `copy_image` の同名 FileExistsError。 [中-4] `set_input_dir` のホーム配下限定 |
| `app/web/server.py` | 修正 | [中-2] `post_config` の `db_file` 処理中 409。 [中-4] `post_config` の `input_dir` ValueError → 400。 [中-3][中-5] `upload_image` の pending 409 / FileExistsError → 409 |
| `app/web/templates/index.html` | 修正 | [中-2] `db-file-input` を処理中 disabled（初期描画 + applyStatus）。 [中-3] `image-input` / `image-submit` を `is_processing || pending` で disabled |
| `app/args.py` | 修正 | [中-4] `is_loopback_host()` 追加（+ `import ipaddress`） |
| `main.py` | 修正 | [中-4] serve 分岐で非 loopback host ならエラーメッセージ + return |
| `tests/test_watch_controller.py` | 修正 | [中-1][中-3][中-5][中-4] の単体テスト追加、`test_get_status_shape` に `pending` 追加、`test_set_input_dir_creates_directory` の home 差し替え |
| `tests/test_web.py` | 修正 | [中-2][中-3][中-5][中-4] の結合テスト追加、[低-3] の `test_watch_picks_up_uploaded_image` 再構成、`test_config_updates_input_dir_and_mode` の home 差し替え |
| `tests/test_args.py` | **新規** | [中-4] `is_loopback_host` のテスト（新規ファイルのため `git status --short` で `??` か ignored を確認） |
| `.hermes/rules/cli.md` | 修正 | Web UI 運用節に非 loopback 拒否・パス制限・個人利用前提を追記 |
| `specs/command-to-webui-spec.md` | 修正 | エンドポイント表（409 追加）/ 状態一覧（pending）/ 決定事項（中-4 対策）/ 受け入れ対応 |

## 実装順序（推奨）

Task 1（[中-1] スレッド）→ Task 2（[中-3] pending 導入: コントローラ）→ Task 3（[中-5] copy_image）→ Task 4（[中-2] サーバ/UI）→ Task 5（[中-3][中-5] サーバ/UI）→ Task 6（[低-3] テスト再構成）→ Task 7（[中-4] パス制限）→ Task 8（[中-4] host チェック）→ Task 9（ドキュメント）

各 Task は TDD（失敗テスト → 実装 → 成功確認 → コミット `fix #58: <説明>`、Task 9 のみ `docs #58: <説明>`）。詳細は `tasks.md`。

## 完了条件との対応

| 完了条件 | 対応 |
|---------|------|
| 停止→再開で watch スレッドが一つだけ起動する | Task 1（`test_stop_restart_no_duplicate_thread` が生存スレッド 1 本を検証） |
| 処理中の DB 差し替えが 409 になる | Task 4（`test_config_db_upload_409_while_processing`） |
| 画像コピー後〜 structured_data 完成まで画像指定が無効になる | Task 2/5（pending 状態 + `/images` 409 + UI disabled） |
| `/images` と `/config` の競合状態がサーバ側で一貫して制御される | Task 4/5 |
| 非 loopback 起動時のリスク表示または起動拒否が実装される | Task 8（起動拒否。Q1 で警告案が選ばれた場合は警告表示に変更） |
| 同名画像が上書きされず、重複時の挙動が明確になる | Task 3/5（FileExistsError → 409、元画像保持をテスト） |
| 409 リトライをテストから除去し、状態遷移を直接検証できる | Task 6 |
| 関連テストが通る | 各 Task の pytest + 最終フルスイート |

## 検証方法

- 各 Task: `uv run pytest tests/test_watch_controller.py tests/test_web.py tests/test_args.py -q`（期待値は tasks.md の各 Task 参照）
- 最終: `uv run pytest -q` → 期待 **409 passed / 0 failed**（2026-11-19 実測の 394 件 + 追加 15 件）
- 手動: `uv run python main.py --serve --host 0.0.0.0` → エラーで即終了。`--host 127.0.0.1` → 起動し `curl -s localhost:8000/api/status` に `"pending": false` が含まれる

## 相談事項（2026-11-19 ユーザー回答済み: 全件 推奨の案A を選択）

| # | 質問 | 選択（回答） | 内容 |
|---|------|------------|------|
| Q1 | [中-4] 非 loopback host 起動時の対応 | **案A: 起動拒否** | エラーメッセージ + return。最も安全（Task 8 が実装） |
| Q2 | [中-4] input_dir の許可ルート | **案A: ホームディレクトリ配下に限定** | 越えれば 400。レビュー指摘「許可ルート配下に限定」に忠実。既存テスト 2 件の修正あり（Task 7 が実装） |
| Q3 | 既存の問題 1（読込不能ファイルの毎サイクル再処理）の扱い | **案A: 別 Issue 化** | 本 Issue では pending の failed 退避判定のみ。最小修正（`move_to_failed` の move 化 + 空 raw の失敗扱い）は別 Issue 提案（tasks.md 末尾参照） |

## リスク・トレードオフ・未確定事項

- **[中-1] watch_on の表示**: スキャン中に停止した場合、スレッド終了まで `watch_on` が True のまま（UI は「watch 停止」ボタン表示のまま）。実態に即した表示であり許容。
- **[中-3] pending と watch 停止の相互作用**: pending 中に watch を停止すると消費スキャンが走らず pending が残る（画像指定はロックされたまま）。watch 再開で解消。仕様上は「1 枚ずつ」の維持であり許容、リスクとして記録。
- **[中-3] 読込不能画像との相互作用**: failed 退避判定を含めるため永久ロックはしないが、再処理ループ自体（既存の問題 1）は残る。別 Issue で「エラーで止める（退避）」方式を推奨（上記）。
- **[中-4] ホーム配下限定は既存テスト 2 件を壊す**: `test_set_input_dir_creates_directory` と `test_config_updates_input_dir_and_mode` が tmp_path（ホーム外）を使うため、monkeypatch で `Path.home` を tmp 配下に差し替えて更新する（Task 7 に完全な置換コード）。
- **コミット方針**: ユーザーの既存方針により自動コミットしない。tasks.md のコミット手順は「実行時にグループを確認してから」実行する。
