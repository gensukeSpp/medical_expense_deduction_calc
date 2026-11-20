# Issue #60: 読込不能画像が watch の各サイクルで再処理される — 実装計画

## 目的

watcher で読み込めない画像を処理すると、OCR が例外を出さず空の結果 `[]` を返し、
raw JSON が生成されない。この経路が失敗扱いにならず、次のスキャンでも同じ画像が
再処理され、`is_processing` の点滅と OCR の再実行が続く。本 Issue では読込不能
ファイルの再処理停止に限定して修正する。

Issue #58（UI・競合制御）とは別の watcher 側の問題であり、PR #59 のレビュー対応
（`tasks/pr_59_m-1/`）とも独立。本計画は `tasks/issue_58/tasks.md` 末尾の
「別 Issue 提案」3 項目を具体化したもの。

## 現状（コード読解の結果）

### 失敗経路 1: `process_image()` が空 `[]` を返すが例外を投げない

`app/ocr_pipeline.py::process_image()` は `resize_image_for_ocr()` の結果が
`None`（読込不能画像、`app/image_resize.py:18-20` で `cv2.imread` が None
を返す）のとき、**例外を投げず `return []` で早期 return** する（`ocr_pipeline.py:86-87`）。
ここで `output_json_path` への書き込みは**行われない**。

`app/services/receipt_processor.py::_sync_process()` はこの `[]` を受け取り、例外なしで
続行する:

- `normalize_coordinates()` は存在しない raw パスを読み込もうとして例外 → 捕捉され無視
- `process_input_json()` も存在しない raw パスで失敗 → 捕捉され `structured = None`
- `return True`（`receipt_processor.py:253`）→ **成功扱い**で、`move_to_failed` は呼ばれない

次サイクルでは mtime ガード `output_json_path.exists()`（`receipt_processor.py:130`）が
False のまま → 再処理 → 毎サイクル繰り返し。

### 失敗経路 2: 例外系失敗時の `move_to_failed()` が複製のみ

`app/services/file_repository.py::move_to_failed()` は現在 `shutil.copy2`（:66）で
failed/ へ**複製**するだけのため、元画像が `input_dir` に残る。例外系失敗
（OCR 例外等）でも同様に毎サイクル再試行される。

### watch レベルでの影響

`app/services/watch_controller.py::_run_scan()` はスキャン毎に `_is_processing` を
True → finally で False に戻す（:276, :293）。読込不能画像が `input_dir` から
外れないため、サイクルごとに `is_processing` が点滅し、OCR が何度も起動する。

## 対策の取捨選択

| 項目 | 方針（採用） | 理由 |
|------|------------|------|
| 再処理の停止方法 | **読込不能画像を failed/ へ「移動」し、`input_dir` から取り除く** | ファイル内容は不変のため再試行に成功見込みがなく、`input_dir` から外れれば mtime ガードに頼らない決定的な停止条件になる。失敗は一旦 failed/ に退避され、ユーザーが目視確認・別名で再投入できるためデータ損失なし |
| 空 `[]` の失敗判定 | **`process_image()` が空 `[]` を返し、かつ `output_json_path` が生成されていない場合のみ失敗扱い** | 読込不能画像（resize 失敗）では raw が書かれない。**有効な画像で OCR 結果ゼロ行**の場合は raw JSON は書かれている（`process_image` は resize 成功時必ず `write_json_atomic` する）ため、この区別で「読込不能」を正確に限定できる |
| 再試行 | この経路では**再試行しない**（即時 `move_to_failed` + `return False`） | resize 失敗はファイル内容に依存し再試行で回復しない。`while attempt <= retries` のループは例外系のみの救済として維持 |
| `move_to_failed` 内部 | `shutil.copy2` → **`shutil.move`** に変更 | copy2 だと `input_dir` に残るため再処理ループが続く。move で `input_dir` から消える（同一ファイルシステムでは rename 扱いで mtime が保持される） |

## 発生経路と対処の関係（watch レベル 完了条件）

修正後、`_sync_process()` が `move_to_failed()`（=move）で元画像を failed/ へ移すため、
`input_dir` から消え、次サイクルの `scan_images()` スキャン対象に含まれなくなる。
`_run_scan()` の `_is_processing` は finally で必ず False に戻るため、点滅は
1 サイクルで終了する。

## ファイル変更表

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `app/services/file_repository.py` | 修正 | `move_to_failed` の `shutil.copy2` → `shutil.move`（:66）。ログ文言を「Copied」→「Moved」に更新 |
| `app/services/receipt_processor.py` | 修正 | `_sync_process` の `process_image()` 直後に「空 `[]` かつ raw 未生成 → `move_to_failed` + `return False`」のガードを追加 |
| `tests/test_file_repository.py` | 修正 | `test_copies_file_to_failed_dir` を `test_moves_file_to_failed_dir` に変更（元画像が残る→消えるのアサーションへ） |
| `tests/test_receipt_processor.py` | 修正 | 読込不能（空 `[]` + raw 未生成）→ `False` かつ failed/ へ移動されるテストを追加 |
| `tests/test_watcher_integration.py` | 修正 | `scan_and_process` で読込不能画像が 1 サイクルで failed/ へ移動し、次のサイクルで再処理されないテストを追加 |
| `tests/test_watch_controller.py` | 修正 | `_run_scan()` 後 `is_processing` が False に解除され、読込不能画像が `input_dir` から消え failed/ に存在する(watch レベルのテスト)を追加 |

## 実装順序（推奨）

Task 1（`move_to_failed` の move 化）→ Task 2（`_sync_process` の失敗ガード）→
Task 3（watcher / watch レベル統合テスト）→ Task 4（最終フルスイート検証 + ドキュメント反映）

各 Task は TDD（失敗テスト → 実装 → 成功確認 → コミット `fix #60: <説明>`、Task 4 の
ドキュメントは `docs #60: <説明>`）。詳細は `tasks.md`。

## 完了条件との対応

| 完了条件 | 対応 |
|---------|------|
| `move_to_failed()` 実行後、元画像が `input_dir` から消え、`failed/` に存在する | Task 1（`test_moves_file_to_failed_dir` で `not p.exists()` かつ failed に存在） |
| 読込不能画像は最初の処理サイクルで失敗扱いとなり、`failed/` に退避される | Task 2（`_sync_process` のガード + `test_receipt_processor` の新テスト） |
| 次の watch サイクルで同じ画像が再処理されない | Task 3（`test_watcher_integration` の 2 サイクル検証） |
| watch レベルで `is_processing` が失敗処理後に解除され、点滅が続かない | Task 3（`test_watch_controller` の `_run_scan` 後 `is_processing is False` + `input_dir` から消えている） |
| 既存の成功処理および失敗処理のテストが通る | Task 4（`test_file_repository` 以外の既存テストは無変更で pass、フルスイート検証） |

## 検証方法

- 各 Task: 該当ファイル限定の `uv run pytest <file> -q`（期待値は tasks.md の各 Task 参照）
- 最終: `uv run pytest -q` → 期待 **425 passed / 0 failed**（2026-11-20 実測の 424 件 + 追加 1 件。
  `test_file_repository` は既存 14 件の文言変更のみで件数不変）。`uv run black --check .`

## リスク・トレードオフ・未確定事項

- **有効画像で OCR 結果ゼロ行との区別**: 失敗判定は「`[]` かつ raw 未生成」に限定しているため、
  有効画像でテキストが検出されない正当なケース（raw は書かれる）は誤って失敗扱いされない。
  ただし `process_image()` が resize 成功後に書き込みで例外を出した場合は raw が未生成となり
  失敗扱いに落ちるが、その場合は書き込み例外が try/except の外で呼び出し側（`_sync_process`）
  に伝播し retry ループで再試行される（既存の例外系経路が保持される）ため、本ガードの対象外。
- **`shutil.move` と mtime**: 同一ファイルシステムでは rename 相当で mtime が保持される。
  `watch_controller._failed_copy_exists()` は failed コピーの mtime+size で pending 画像の
  退避を判定しているが（watch_controller.py:306-326）、move でも mtime は保持されるため
  判定は従来通り機能する。ロジック変更不要。
- **失敗扱いになった読込不能画像は自動復旧しない**: failed/ へ退避されるだけで、ユーザーが
  目視確認し必要に応じて別名で再投入する運用。データ損失はない。
- **コミット方針**: ユーザーの既存方針により自動コミットしない。tasks.md のコミット手順は
  実装・検証後にグループを確認してから実行する。

## 対象外（明示）

- `move_to_processed` の呼び出し元が無く成功画像が `input_dir` に残る件（Issue #58 plan.md の
  「既存の問題 2」）はプロダクト判断を含むため本スコープ外。
- `is_processing` の UI 側表示や pending 状態（Issue #56/#58）は変更しない。