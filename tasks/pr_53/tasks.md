# PR #53 (#50) レビュー対応 実装タスク

> 実施順に並べた bite-sized タスク。各タスクは TDD（失敗→実装→成功）で進め、完了ごとにコミット。
> 前提: `tasks/pr_53/overview.md` の設計に従う。Python 3.11 / `uv run` / black(line-length=119)。
> 注意: ユーザーは自動コミットを望んでいない。**コミットは実行せず**、各タスクの実装・テスト確認のみ行い、
> コミット群の確認はユーザーに提示する。

---

## タスク 1: 失敗理由定数と返り値拡張（P2#1）

**Objective:** `apply_date_anchor_normalization` の戻り値を「成功 `(True, (ox,oy))` / 失敗 `(False, reason)`」に変更し、経路からの force 呼び出しを `ANCHOR_UNRESOLVED` 時のみに限定する。

**Files:**
- Modify: `app/date_anchor.py:235-361`（`apply_date_anchor_normalization`）
- Modify: `app/services/image_processing_service.py:90-101`
- Modify: `app/services/receipt_processor.py:218-231`
- Test: `tests/test_date_anchor.py` / `tests/test_image_processing_service.py` / `tests/test_receipt_processor.py`

**Step 1: 失敗理由定数と戻り値変更（実装）**

`app/date_anchor.py` の `apply_date_anchor_normalization` docstring 直上に定数を追加:
```python
ANCHOR_OK = "ok"
NO_DB = "no_db"
NO_DATE_OR_CLINIC = "no_date_or_clinic"
NO_TEMPLATE = "no_template"
NO_DATE_COORDS = "no_date_coords"
NO_OCR_ENTRIES = "no_ocr_entries"
ANCHOR_UNRESOLVED = "anchor_unresolved"
NOT_NORMALIZED = "not_normalized"
```

各 early-return を理由ペアへ置換（戻り値型 `tuple[bool, str | tuple[int, int]]` に更新）:
```python
if not db_path:
    return (False, NO_DB)
if not structured_date or not clinic_name:
    return (False, NO_DATE_OR_CLINIC)
if not template:
    return (False, NO_TEMPLATE)
if template_date_box is None:
    return (False, NO_DATE_COORDS)
if not ocr_entries:
    return (False, NO_OCR_ENTRIES)
if anchor_box is None:
    return (False, ANCHOR_UNRESOLVED)     # ← force 発火対象
if not result.get("normalized"):
    return (False, NOT_NORMALIZED)
...
# 成功パス（既存通り offset を返す。migration 成功後）
return (True, (offset_x, offset_y))
```
※ `shift_template_to_date_basis` が None（line 324-325）も `(False, NO_DATE_COORDS)` を返す。

**Step 2: docstring の Returns 節を更新**（`(offset_x, offset_y)` → 上記ペアの仕様）。

**Step 3: サービス経路の force 呼び出しを限定**

`image_processing_service.py:96-100` を:
```python
anchor_ok, anchor_detail = apply_date_anchor_normalization(
    active_raw_path, structured, db_path, output_dir=output_dir
)
if not anchor_ok and anchor_detail == ANCHOR_UNRESOLVED:
    from app.date_anchor import apply_date_basis_force
    apply_date_basis_force(active_raw_path, structured, db_path, output_dir=output_dir)
```
`receipt_processor.py:225-229` も同様に変更。

**Step 4: 既存テストのアサーション更新**

- `tests/test_date_anchor.py`:
  - 284-286 `result == (50, 100)` → `result == (True, (50, 100))`
  - 329・338 `is None` → `== (False, NO_DATE_COORDS)` / `== (False, NO_DB)`
  - 393・491-494 の成功系 → `result[0] is True`（または `== (True, (...))`）
- `tests/test_image_processing_service.py:331` mock `return_value=None` → `(False, ANCHOR_UNRESOLVED)`（force 発火）／ `:352` 成功 `(0,0)` → `(True, (0, 0))`（不発）
- `tests/test_receipt_processor.py:166` → `(False, ANCHOR_UNRESOLVED)` ／ `:205` → `(True, (0, 0))`

**Step 5: force 発火理由を絞るテストを追加**

`tests/test_image_processing_service.py` に、`apply_date_anchor_normalization` が
`(False, NO_TEMPLATE)` / `(False, NO_OCR_ENTRIES)` 等の**非 anchor 理由**を返す際、`apply_date_basis_force` が**呼ばれない**ことを検証するテストを追加（2 経路とも）。

**Step 6: 検証**

```bash
uv run pytest tests/test_date_anchor.py tests/test_image_processing_service.py tests/test_receipt_processor.py -q
```
Expected: 全 PASS。

---

## タスク 2: force の上書き検証と戻り値 bool 化（P1#2 + P3）

**Objective:** `apply_date_basis_force` が「既存の非空値は保守（上書きしない）」するようにし、戻り値を bool に統一。近傍一致の有無を明示化。

**Files:**
- Modify: `app/date_anchor.py:413-442`（force 本体）
- Test: `tests/test_date_anchor.py:506-685`

**Step 1: 上書きループ変更**

`app/date_anchor.py`（現在 424-432 相当）を:
```python
changed = False
for field_name, text in proximity.items():
    if field_name == "date":
        continue
    existing = structured.get(field_name)   # 近傍一致の有無は proximity dict に載った時点で保証
    if not text:
        continue
    if existing and existing != text:
        # 既存の非空値は保守（位置ずれ時の誤上書き防止 / Issue #50 レビュー P1#2）
        continue
    if structured.get(field_name) != text:
        structured[field_name] = text
        changed = True
if not changed:
    return False
```

**Step 2: 戻り値を bool 化**

- ゲート不通過（各 `return None`）→ `return False`
- 変化なし → `return False`
- 最後の `return (0, 0)` → `return True`
- docstring の Returns 節を「`True` = structured を 1 件以上更新、`False` = それ以外」に更新。

**Step 3: threshold 分岐削除（P3）**

現在の `if proximity_threshold == 50.0: proximity_threshold = DEFAULT_PROXIMITY_THRESHOLD`（421-422）を削除し、
実引数に `DEFAULT_PROXIMITY_THRESHOLD` を既定値として渡す（既定値 50.0 と同値のため）。

**Step 4: テスト更新**

- `tests/test_date_anchor.py`:
  - `result is not None`（559, 671）→ `is True`
  - `is None`（588, 590, 592, 619）→ `is False`
  - `test_updates_structured_on_date_basis_template`（519-561）: amount を最初 `None` にして「空欄を埋める」形に変更（既存 `3800` のままでは不変になり期待が変わるため）。name は既存 `"山田 太郎"` == 抽出値 で no-op。
  - 追加テスト: **既存非空値が抽出値と異なる場合は上書きされない**（structured に `"amount": 9999` を入れ、抽出が `"3,800"` でも `9999` のまま・戻り値 `False`）ケース。

**Step 5: 検証**

```bash
uv run pytest tests/test_date_anchor.py -v
```
Expected: 全 PASS。

---

## タスク 3: テストカバレッジ追加（P3）

**Objective:** レビュー指摘の未テスト関数にテストを足す。

**Files:**
- Test: `tests/test_date_anchor.py`

**Step 1: カバレッジ確認**
```bash
uv run pytest tests/test_date_anchor.py --cov=app.date_anchor -q
```

**Step 2: テスト追加（P3 で挙げられたもの）**
- `_is_valid_calendar_date`: 有効日付 / 無効（2026-99-99, うるう日境界, 月日境界）
- `_resolve_date_basis_candidate`: 単一候補 / 同点 → None / topmost vs date の比較
- `_to_ocr_entries`: 空 / 正常 / confidence 欠落
- `_top_left_box`: box の左上導出
- `_apply_coordinate_extraction`: `output_dir` なし / ありで structured + json 反映

**Step 3: 検証**
```bash
uv run pytest tests/test_date_anchor.py -v
```
Expected: 全 PASS（既存関数の実装変更は不要のはず。仕様と一致しない挙動が出た場合は報告）。

---

## タスク 4: 既存テスト失敗の検証記録（P2#2）

**Objective:** `TestRunOcr::test_calls_process_image_and_logs` が本ブランチ固有でない（事前存在）ことを実機で確認し記録する。

**Files:**
- 記録: `tasks/pr_53/overview.md`（または本タスクのメモ）に追記

**Step 1: 現ブランチで失敗再現**
```bash
uv run pytest "tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs" -v
```

**Step 2: origin/main で同一失敗を再現**
```bash
git stash && git checkout origin/main
uv run pytest "tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs" -v
git checkout feature/constrain-date-anchor/50 && git stash pop
```
Expected: いずれも同一理由で FAIL（caplog に "Saved 1 item to" が出ない）。→ 事前存在失敗と確定。
※ stash にある未追跡ファイル（requirements.txt 等）は pop 前に必ず確認。

**Step 3: 記録**
失敗理由・再現手順・「本 PR の差分に起因しない」根拠を `tasks/pr_53/overview.md` の検証欄に記録。

---

## タスク 5: 全体検証・ドキュメント更新

**Objective:** 全テスト・black・対象ドキュメントの整合を確認する。

**Files:**
- Modify: `specs/2026-11-06-2-spec.md`（force 発火条件・既存値保守を反映）
- Modify: `tasks/issue_50/overview.md`（必要に応じ同じく反映）
- Modify: `.hermes/rules/design-decisions.md`（「force は ANCHOR_UNRESOLVED 時のみ / 既存非空値は不変」を追記）

**Step 1: 全テスト**
```bash
uv run pytest -q
```
Expected: 全 PASS（既知の TestRunOcr 事前存在失敗 1 件のみを除く）。

**Step 2: black**
```bash
uv run black . --check
```
Expected: no diff を目指す。違反時は `uv run black .` で整形。

**Step 3: ドキュメント**（上記 3 ファイル）に今回の設計判断を追記し、spec のフロー図（35-46 行）の
「None (anchor 未解決) -> force」を「`(False, anchor_unresolved)` -> force（他の理由では不発）」に更新。

**Step 4: 最終確認**
```bash
uv run pytest -q
uv run black . --check
```

---

## コミット（ユーザー確認後に実施）

コミットは自動で行わない。タスク完了後に以下をユーザーに提示し、承認後にまとめてコミットする。
提案コミット分割（例）:
1. `refactor #50: apply_date_anchor_normalization の戻り値を理由付きに拡張（P2#1）` — app/date_anchor.py(anchor 部), services 2件, 該当テスト
2. `fix #50: force の上書き前検証と戻り値 bool 化（P1#2/P3）` — app/date_anchor.py(force 部), test_date_anchor.py
3. `test #50: 未テスト関数のカバレッジ追加（P3）` — test_date_anchor.py
4. `docs #50: レビュー対応の仕様・設計を記録` — specs/, tasks/issue_50/, .hermes/rules/

コミットのファイル分割・メッセージはユーザーに確認する。