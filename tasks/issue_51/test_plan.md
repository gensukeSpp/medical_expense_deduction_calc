# Issue #51: テスト計画

## テスト戦略

- **フレームワーク**: pytest（`uv run pytest ...`）
- **既存テスト拡張**: `tests/test_feedback.py`（単体）と `tests/test_web.py`（結合）を追加・拡張
- **テストデータ**: 既存のサンプル OCR エントリ（`SAMPLE_OCR_ENTRIES`）と `seed_clinic_and_receipt` フィクスチャを再利用
- **DB 使用**: `temp_db` フィクスチャ（`run_migrations` + `docs/schema.sql`）で実 DB を作成して検証

## テストケース一覧

### Task 3: `tests/test_feedback.py` — date 基準へ変換して維持（単体）

#### TC-01: date テンプレへの修正で座標を date 基準へ変換し date 維持

| 項目 | 内容 |
|------|------|
| テスト名 | `test_keeps_date_basis_and_converts_correction_on_date_template` |
| 概要 | `coord_basis='date'` のテンプレに対し topmost 基準座標で修正すると座標が date 基準へ変換され、`coord_basis` は `date` のまま |
| 準備 | date テンプレ作成 → レシート `ocr_json`（date box top-left=(50,50)）を持つ `seed_clinic_and_receipt` で amount を修正 |
| 確認点 | `get_latest_template_by_clinic(...)["coord_basis"] == "date"`、amount が (50,50) 分シフト（date 基準）される、未修正フィールドは不変 |

#### TC-01b: date 基準 ocr_json では変換は no-op

| 項目 | 内容 |
|------|------|
| テスト名 | `test_date_basis_correction_noop_on_date_basis_ocr` |
| 概要 | レシート `ocr_json` が既に date 基準（date box 原点）なら修正座標はそのまま（減算 no-op）で date 維持 |
| 確認点 | `coord_basis == "date"`、amount 座標が不変 |

#### TC-01c: anchor 未解決では書込まず基底不変

| 項目 | 内容 |
|------|------|
| テスト名 | `test_skips_feedback_when_date_anchor_unresolvable` |
| 概要 | date テンプレで anchor 判定不能（receipt_id=None 等）なら座標を書き込まず基底も `date` のまま |
| 確認点 | `updated_fields == []`、`history_id is None`、`coord_basis` 不変、amount 未登録 |

#### TC-02: 既存 topmost テンプレでは topmost が維持

| 項目 | 内容 |
|------|------|
| テスト名 | 既存 `test_updates_template_with_coords`（回帰対象）
| 概要 | `coord_basis` 既定 topmost のテンプレへの修正で topmost のまま（既存テスト回帰）。Task1・2 後も挙動不変を確認 |
| 確認点 | 従来どおり `coords_corrections` 更新 + 履歴記録。`coord_basis` は topmost 維持 |

#### TC-03: テンプレ未作成（新規）では既定 topmost

| 項目 | 内容 |
|------|------|
| テスト名 | 既存 `test_no_existing_template_creates_new` |
| 概要 | 新規テンプレ作成時 `coord_basis` は schema 既定 topmost |
| 確認点 | `upsert_template` の `coord_basis=None` 経路が INSERT で既定 topmost を入れる（既存挙動維持） |

### Task 4: `tests/test_web.py` — 管理画面表示（結合）

#### TC-04: date テンプレを Web 修正しても管理画面は date 表示のまま

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_keeps_date_basis_after_correction_on_date_template` |
| 概要 | `PUT /{file_stem}` で修正 → `GET /admin/templates` で `date` 表示のまま、DB の `coord_basis` も `date` |
| 準備 | date テンプレ + raw/structured を作成 → `create_app(output_dir, db_path)` → TestClient |
| 確認点 | PUT 200、`get_latest_template_by_clinic(...)["coord_basis"]=="date"`、anchor 未解決のため座標未書込 |

#### TC-05: 管理画面の通常表示（回帰）

| 項目 | 内容 |
|------|------|
| テスト名 | 既存 `test_admin_index_lists_clinic_and_basis` |
| 概要 | 既定 topmost のテンプレが管理画面で topmost 表示される（既存テストで回帰確認） |

## エッジケース一覧

| ケース | 対応方針 |
|--------|---------|
| 既存 template なし（新規） | `existing_basis=None` → `upsert_template` は既定 topmost。破壊なし |
| `coord_basis` 不明（旧データ） | `(existing or {}).get(...)` → None → 既定 topmost のまま |
| `coord_basis=='topmost'` 既存 | `coord_basis=None` を渡し `upsert_template` は Update で coord_basis に触れない |
| `coord_basis=='date'` 既存（anchor 可） | 修正座標を date 基準へ変換し `'date'` を明示 → Update で date 維持 |
| `coord_basis=='date'` 既存（anchor 不可） | 座標を書かず `coord_basis` も変えない（安全側） |
| `upsert_template` 旧呼び出し（coord_basis 省略） | SQL/params が従来と同一（後方互換）。全既存テストで確認 |
| low_confidence レシート | `ReceiptUpdater` 側で feedback スキップ（既存）。変更なし |

## テスト実行方法

```bash
# フィードバック単体（Task 3）
uv run pytest tests/test_feedback.py -v

# 管理画面結合（Task 4）
uv run pytest tests/test_web.py -k keeps_date_basis -v

# 影響対象モジュールの回帰
uv run pytest tests/test_db.py tests/test_web.py -v

# 全テスト（既知 2 件の事前存在失敗を除き PASS）
uv run pytest tests/ -v

# フォーマット確認
uv run black --check app/db.py app/template_feedback.py
```

## 受入条件との対応

| 受入条件 | 検証テスト |
|----------|-----------|
| 1. 修正後 coord_basis が date、coords と整合 | TC-01 + TC-01b（単体）+ TC-04（結合） |
| 2. 管理画面が date 表示のまま | TC-04 |
| 3. 既存テストがパス（回帰なし） | 全テスト実行（Task 5） |
| 4. anchor 未解決では書込まず基底不変 | TC-01c（単体） |

## 既知の事前存在テスト失敗（feature では修正しない）

- `tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs` — INFO ログを捕捉しない既存バグ
- `tests/test_image_processing_service.py::TestProcess::test_happy_path` — mock 引数の位置/キーワード不一致（既存）

どちらも `origin/main` で同一に失敗するため、本 Issue のスコープ外。
