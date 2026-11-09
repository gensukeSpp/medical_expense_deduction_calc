# Issue #50: テスト計画

## テスト戦略

- **フレームワーク**: pytest（`uv run pytest ...`）
- **対象モジュール**: `app/date_anchor.py`（force 抽出）、`app/verify_date_anchor.py`（--force）、
  両オーケストレータ（統合）
- **既存テスト再利用**: `tests/conftest.py` / `tests/test_date_anchor.py` / `tests/test_verify_date_anchor.py` の
  フィクスチャやサンプル OCR エントリを流用
- **手法**: TDD（失敗テスト → 最小実装 → 成功確認）。DB は `temp_db` フィクスチャで実作成。

## テストケース一覧

### Task 2: `tests/test_verify_date_anchor.py` — #47 検証ツール --force

#### TC-01: --force で anchor 解決 None でも抽出を試みる

| 項目 | 内容 |
|------|------|
| テスト名 | `test_verify_force_extracts_when_anchor_none` |
| 概要 | date 候補 0 件でも `force=True` なら `search_fields_by_proximity` で強制抽出しレポートに含める |
| 準備 | date 一致 OCR テキストの無い raw + 対応 structured + date 基準 template 入り DB コピー |
| 確認点 | `report` に `force_extraction` が含まれ、name/amount が近傍で取得できること |
| 事前失敗 | `verify_date_anchor` に `force` 引数が無い（TypeError / 未対応） |

#### TC-02: force=False は従来どおり安全側フォールバック

| 項目 | 内容 |
|------|------|
| テスト名 | 既存 `test_*`（anchor None で UNRESOLVED を返す系） |
| 概要 | デフォルトは従来挙動（force なし = date 基準補正しない）。回帰確認 |

### Task 3: `tests/test_date_anchor.py` — `apply_date_basis_force()` 単体

#### TC-03: date 基準 + clinic 抽出で name/amount を近傍抽出

| 項目 | 内容 |
|------|------|
| テスト名 | `test_apply_date_basis_force_updates_structured` |
| 概要 | coord_basis='date' + date 学習済み template + clinic 非空で、`structured` の name/amount を近傍抽出で更新 |
| 準備 | date 基準 template（name/amount の box を含む）+ 対応 raw |
| 確認点 | 戻り値が非 None、`structured["amount"]` が抽出値に更新される |

#### TC-04: 受入要件3のゲート（coord_basis / clinic）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_apply_date_basis_force_skips_unless_date_basis_and_clinic` |
| 概要 | coord_basis!='date' なら force しない、clinic 空なら force しない |
| 確認点 | いずれも戻り値 `None` |

#### TC-05: date box 未学習は force しない（#40 ガード）

| 項目 | 内容 |
|------|------|
| テスト名 | 追加テスト（`coords.get("date") is None` で `None`） |
| 概要 | date 未学習の date 基準 template は強制しない |
| 確認点 | 戻り値 `None` |

#### TC-06: 既存 topmost template では force しない

| 項目 | 内容 |
|------|------|
| テスト名 | TC-04 で coord_basis='topmost' ケースを包含 |
| 概要 | 受入要件3（coord_basis=='date' のみ）を担保 |

#### TC-07: structured 更新が空でも安全に None を返す

| 項目 | 内容 |
|------|------|
| テスト名 | 追加（近傍一致なし時 `changed=False` → `None`） |
| 概要 | 取得できなければ値は LLM 抽出のまま保持。例外にしない |

### Task 4: オーケストレータ統合テスト

#### TC-08: apply_date_anchor_normalization が None のとき force が呼ばれる（画像OCR経路）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_process_calls_force_when_anchor_none`（test_image_processing_service.py） |
| 概要 | anchor None → `apply_date_basis_force` が 1 回呼ばれる |
| 手法 | mock.patch で anchor を None、force を MagicMock |

#### TC-09: 同 watcher 経路（receipt_processor.py）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_*_force_when_anchor_none`（test_receipt_processor.py） |
| 概要 | watcher 経路でも anchor None 時に force が呼ばれる |

#### TC-10: anchor 成功時は force を呼ばない（二重補正防止）

| 項目 | 内容 |
|------|------|
| テスト名 | `assert_not_called` |
| 概要 | `apply_date_anchor_normalization` が非 None なら force を呼ばない |

## エッジケース一覧

| ケース | 対応方針 |
|--------|---------|
| db_path 未設定 | `None`（force しない） |
| clinic が新規（template なし） | `get_or_create_clinic` → template None → `None` |
| date box 未学習 | #40 ガードで `None` |
| raw が空 | `None` |
| 近傍一致なし | `changed=False` → `None`（LLM 抽出のまま） |
| low_confidence | 呼び出し側 `not low_confidence` で gate |
| anchor 成功時 | force 呼ばず通常 date 基準パス（二重補正防止） |

## テスト実行方法

```bash
# #47 検証ツール --force（Task 2）
uv run pytest tests/test_verify_date_anchor.py -k force -v

# force 抽出単体（Task 3）
uv run pytest tests/test_date_anchor.py -k apply_date_basis_force -v
uv run pytest tests/test_date_anchor.py -k base_force -v

# 統合（Task 4）
uv run pytest tests/test_image_processing_service.py -k force -v
uv run pytest tests/test_receipt_processor.py -k force -v

# 全テスト（既知 2 件の事前存在失敗を除き PASS）
uv run pytest tests/ -v

# フォーマット
uv run black --check app/date_anchor.py app/verify_date_anchor.py \
    app/services/image_processing_service.py app/services/receipt_processor.py
```

## 受入条件との対応

| 受入条件 | 検証 |
|----------|------|
| 1. 現状機能の齟齬なし確認 | Task 1（ドキュメント・分析） |
| 2. #47 ツールで強制検証結果を確認 | TC-01（--force モード） |
| 3. 強制条件の厳守（coord_basis==date 且つ clinic 抽出成功） | TC-03/04/05/09/10 |
| 4. structured への座標抽出再適用 | TC-03 |
| 5. 既存テストがパス | 全テスト（Task 5） |

## 事前に確認すべきこと

- `tests/test_date_anchor.py`・`tests/test_verify_date_anchor.py` が git 追跡済みか（`git ls-files tests/`）。
  - 追跡済み: `git add tests/test_...py` で通常どおり。
  - 未追跡（`.gitignore` の `test_*.py` に該当）: `git add -f tests/test_...py` で追加する。

## 既知の事前存在テスト失敗（feature では修正しない）

- `tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs`
- `tests/test_image_processing_service.py::TestProcess::test_happy_path`

どちらも `origin/main` で同一に失敗する既存問題。本 Issue のスコープ外。
