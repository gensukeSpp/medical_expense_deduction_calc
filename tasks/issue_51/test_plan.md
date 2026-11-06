# Issue #51: テスト計画

## テスト戦略

- **フレームワーク**: pytest（`uv run pytest ...`）
- **既存テスト拡張**: `tests/test_feedback.py`（単体）と `tests/test_web.py`（結合）を追加・拡張
- **テストデータ**: 既存のサンプル OCR エントリ（`SAMPLE_OCR_ENTRIES`）と `seed_clinic_and_receipt` フィクスチャを再利用
- **DB 使用**: `temp_db` フィクスチャ（`run_migrations` + `docs/schema.sql`）で実 DB を作成して検証

## テストケース一覧

### Task 3: `tests/test_feedback.py` — coord_basis リセット（単体）

#### TC-01: date テンプレへの修正で topmost に戻る

| 項目 | 内容 |
|------|------|
| テスト名 | `test_resets_coord_basis_to_topmost_after_correction_on_date_template` |
| 概要 | `coord_basis='date'` のテンプレに対し topmost 基準座標で修正すると `coord_basis` が `topmost` になる |
| 準備 | `upsert_template(..., coord_basis="date")` で date テンプレを作成 → `process_correction_feedback` で amount を修正 |
| 確認点 | `get_latest_template_by_clinic(...)["coord_basis"] == "topmost"`、`coords_corrections` に amount が入る |
| 事前失敗確認 | Task 1・2 実装前は `coord_basis` が `date` のまま（FAIL） |

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

#### TC-04: date テンプレを Web 修正すると管理画面が topmost 表示に

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_shows_topmost_after_correction_on_date_template` |
| 概要 | `PUT /{file_stem}` で修正 → `GET /admin/templates` が `topmost` 表示、DB の `coord_basis` も `topmost` |
| 準備 | date テンプレ + raw/structured を作成 → `create_app(output_dir, db_path)` → TestClient |
| 確認点 | PUT 200、管理画面 HTML に `topmost` が含まれる、`get_latest_template_by_clinic(...)["coord_basis"]=="topmost"` |

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
| `coord_basis=='date'` 既存 | `'topmost'` を明示 → Update で上書き |
| `upsert_template` 旧呼び出し（coord_basis 省略） | SQL/params が従来と同一（後方互換）。全既存テストで確認 |
| low_confidence レシート | `ReceiptUpdater` 側で feedback スキップ（既存）。変更なし |

## テスト実行方法

```bash
# フィードバック単体（Task 3）
uv run pytest tests/test_feedback.py -v

# 管理画面結合（Task 4）
uv run pytest tests/test_web.py -k admin_shows_topmost -v

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
| 1. 修正後 coord_basis が topmost、coords と整合 | TC-01（単体）+ TC-04（結合） |
| 2. 管理画面が topmost 表示 | TC-04 |
| 3. 既存テストがパス（回帰なし） | 全テスト実行（Task 5） |

## 既知の事前存在テスト失敗（feature では修正しない）

- `tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs` — INFO ログを捕捉しない既存バグ
- `tests/test_image_processing_service.py::TestProcess::test_happy_path` — mock 引数の位置/キーワード不一致（既存）

どちらも `origin/main` で同一に失敗するため、本 Issue のスコープ外。
