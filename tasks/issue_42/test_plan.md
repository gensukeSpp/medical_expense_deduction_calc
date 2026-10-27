# Issue #42: テスト計画 — coord_basis 手動切替の Web UI 追加

## テスト戦略

- **フレームワーク**: pytest / conftest.py（`PYTHONPATH=.` 有効化）/ FastAPI `TestClient`
- **主対象**:
  - `tests/test_web.py`（tracked）— admin ルートの統合テスト（サービス + DB + HTML を経由して検証）
  - `tests/test_coord_normalizer.py`（tracked）— `shift_template_to_date_basis()` の単体テスト
  - `tests/test_db.py`（tracked）— `get_clinic_by_id()` の単体テスト
  - `tests/test_date_anchor.py` — リファクタ（Task 2）の回帰確認に使用（**gitignore 対象・commit 不要**）
- **方針**: HTTP ステータス（404 / 409 / 400 / 503 / 200）と DB 副作用
  （`coord_basis`・`template_history` の内容）を両方検証する。
- **新規テストファイルは作らない**（`.gitignore` の `test_*.py` に対する `git add -f` を回避し、
  tracked ファイルへの追記のみで完結させる）。

## テストケース一覧

### Task 1: shift_template_to_date_basis（coord_normalizer 単体）

| テスト名 | 概要 | 確認点 |
|---------|------|-------|
| `test_shifts_by_date_box_min_point` | date box `[[43,234],...]` を含む coords | 全 box が (43,234) 起点でシフトされ、date box の min が (0,0) になる |
| `test_no_date_box_returns_none` | `coords` に date キー無し | None（未学習として扱う）|
| `test_malformed_date_box_returns_none` | `date: []` / `[["x","y"]]` など不正値 | None（409 ガードに繋がる）|

### Task 2: date_anchor リファクタ（回帰）

| テスト名 | 概要 | 確認点 |
|---------|------|-------|
| `tests/test_date_anchor.py` の既存**全テスト** | 自動移行・anchor 解決・シフトの既存挙動 | リファクタ後も全て PASS（動作不変の証明）|

### Task 3: get_clinic_by_id（db 単体）

| テスト名 | 概要 | 確認点 |
|---------|------|-------|
| `test_returns_clinic_dict` | 作成済み clinic を ID 検索 | `id` / `name` が一致する dict が返る |
| `test_returns_none_for_unknown_id` | 存在しない ID | None |

### Task 4/5: admin ルート（Web 統合）

#### TC-01: 一覧表示（受入条件 1 の表示側）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_index_lists_clinic_and_basis` |
| 概要 | `GET /admin/templates` が 200 で clinic 名・coord_basis を表示 |
| 準備 | `_seed_template_clinic()` で clinic + date box 学習済み template を作成 |
| 確認点 | レスポンス HTML に clinic 名と `topmost` が含まれる |

#### TC-02: 切替成功（受入条件 1・2）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_toggle_success` |
| 概要 | `POST /admin/templates/{clinic_id}/basis` body `{"basis":"date"}` |
| 確認点 | 200 / `status=updated`。DB: `coord_basis='date'`、`coords_corrections` がシフト後（date box min=0,0）、`template_history` 最新行の `change_reason='manual_basis_toggle'` かつ移行前 coords が保存済み |

#### TC-03: 冪等（受入条件 4）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_toggle_idempotent` |
| 概要 | 成功後に同じ POST を再送 |
| 確認点 | 200 / `status=noop`。`template_history` の件数が 1 件のまま（二重変換・重複記録なし）|

#### TC-04: date box 未学習ガード（受入条件 3）

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_toggle_requires_date_box` |
| 概要 | date box 無し template の clinic へ POST |
| 確認点 | 409 / `detail="date box 未学習のため切替できません"`。`coord_basis`・coords が不変 |

#### TC-05: 未知 clinic

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_toggle_unknown_clinic` |
| 確認点 | 404 |

#### TC-06: 不対応 basis 値

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_toggle_invalid_basis` |
| 概要 | `{"basis":"topmost"}`（date 以外）|
| 確認点 | 400 |

#### TC-07: DB 未設定

| 項目 | 内容 |
|------|------|
| テスト名 | `test_admin_db_not_configured` |
| 概要 | `create_app(db_path=None)` |
| 確認点 | GET / POST とも 503 |

#### TC-08: index からのリンク

| 項目 | 内容 |
|------|------|
| テスト名 | `test_index_links_to_admin` |
| 確認点 | `GET /` の HTML に `/admin/templates` へのリンクが含まれる |

## エッジケース一覧

| ケース | 対応方針 |
|--------|---------|
| `coord_basis` が NULL（旧データ）| `or "topmost"` → 切替可能（date box 学習済みなら）|
| date box がマルチ box 形式（list of lists）| `shift_template_coords` が multi-box 対応済み（同一オフセット適用）|
| date box の点が不正（2D 座標でない）| `shift_template_to_date_basis` が None → 409 |
| 切替直後の再 POST（競合）| `update_template_basis()` が False → `noop` として 200 |
| `template_history` の冪等性 | topmost → date の遷移時のみ 1 レコード追記（Task 5 の当事者テストで担保）|
| Form POST（HTML フォーム由来）| JSON と同様に `basis` を解釈（既存 PUT ハンドラと同方式）|
| date box 未学習の一覧表示 | 表には「×」と「切替不可」を表示（フォーム非表示）|

## テスト実行方法

```bash
# 対象単体（タスク単位）
uv run pytest tests/test_coord_normalizer.py -k ShiftTemplateToDateBasis -v
uv run pytest tests/test_db.py -k GetClinicById -v
uv run pytest tests/test_web.py -k "admin" -v
uv run pytest tests/test_date_anchor.py -q   # Task 2 の回帰のみ

# 変更ファイル一括
uv run pytest tests/test_coord_normalizer.py tests/test_db.py tests/test_web.py tests/test_date_anchor.py -q

# 全テスト（既存 + 追加）
uv run pytest tests/ -q

# 整形
uv run black --check app/coord_normalizer.py app/date_anchor.py app/db.py \
    app/services/receipt_database_repository.py app/services/template_admin_service.py \
    app/web/server.py tests/test_coord_normalizer.py tests/test_db.py tests/test_web.py
```

## 既知の事前テスト失敗（本修正と無関係・直さない）

- `test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs` —
  `caplog` が INFO ログを捕捉せず `"Saved 1 item to"` が空で常に失敗（base でも同様）。
- `test_image_processing_service.py::TestProcess::test_happy_path` —
  mock アサーションの引数形式（positional vs keyword）不整合で常に失敗（base でも同様）。

上記 2 件は `origin/main` でも同一失敗する既知の事前失敗であり、本 Issue の修正対象外。
全テスト実行時は「追加テストが全て PASS」かつ「上記以外に新規失敗なし」を確認する。
