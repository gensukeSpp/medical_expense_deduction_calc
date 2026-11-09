# PR #53 (#50) レビュー対応 実装計画: [P1]#2 以降

- 根拠: `.github/reports/pr-53-review.md`
- ブランチ: `feature/constrain-date-anchor/50`（現 HEAD = 5a9a986）
- 対応範囲: レビュー指摘 **P1#2 / P2#1 / P2#2 / P3** すべて
- 対象外: **P1#1（PR 本文・spec・実装の三者不一致）** — PR 本文はユーザーにより書き換え済み。本計画書とタスクはコード・テスト・ドキュメントの実装のみ扱う。

---

## Goal

案A（`apply_date_basis_force`: 再正規化なし・`search_fields_by_proximity` 直接抽出）を維持したまま、
レビュー指摘の以下を是正する。

- **[P1]#2** 位置ずれ時に誤った name/amount を structured と DB へ**上書き**してしまうリスク → 上書き前に検証
- **[P2]#1** `apply_date_anchor_normalization` が `None` を返す理由は複数あり、force 呼び出し条件が spec の「date 候補 0 件」より広い → 失敗理由を区別して、date 候補 0 件による解決不能時のみ force を発火
- **[P2]#2** `TestRunOcr::test_calls_process_image_and_logs` の事前存在失敗の検証・記録
- **[P3]** `apply_date_basis_force` の threshold 分岐削除 / 戻り値型の統一 / 未テスト関数のカバレッジ追加

---

## 確定した方針（ユーザー選択 / 2026-11-09）

| 指摘 | 採用方針 |
|------|----------|
| P1#2（上書き検証） | **近傍一致の有無のみ** + **既存値との整合**（structured に既存の非空値がある場合、抽出値と異なれば**上書きしない**）。被覆率(match_ratio)検証・候補退避は不採用。 |
| P2#1（呼び出し条件） | **案(a) 返り値拡張**: `apply_date_anchor_normalization` の戻り値を「成功 ok / 失敗理由」を判別可能にし、両経路は `anchor_unresolved`（date 候補 0 件/曖昧）時のみ force を呼ぶ。 |

---

## 現状の実態（変更前の確認結果）

### apply_date_anchor_normalization の None 返却経路（app/date_anchor.py）

| line | 理由 | 意味 | force 対象? |
|------|------|------|------------|
| 271 | `db_path` なし | DB 未指定 | 否 |
| 275-276 | `date` または `clinic` なし | 前提欠落 | 否 |
| 282-283 | テンプレなし | クリニック未学習 | 否 |
| 287-288 | `coords_corrections['date']` なし | date 未学習 | 否 |
| 294-295 | OCR エントリ空 | 空 raw | 否 |
| **305-306** | `resolve_date_anchor` → None | **date 候補 0 件/曖昧** | **是（spec の想定）** |
| 309-310 | `normalized` でない | 再正規化失敗 | 否 |

→ force は「**305-306 の anchor_unresolved のみ**」で発火すべき。

### apply_date_basis_force の現状（app/date_anchor.py:364-442）

- ゲート: `db_path` / `clinic` 非空 / `coord_basis=='date'` / date 未学習でない
- 抽出: `search_fields_by_proximity(entries, coords, threshold)` → `date` 以外を `structured` へ**無条件上書き**（現状は `changed` 比較のみ）
- 戻り値: 変化時 `(0, 0)`、それ以外 `None`（型が紛らわしい）
- line 421-422: `if proximity_threshold == 50.0: proximity_threshold = DEFAULT_PROXIMITY_THRESHOLD` の無意味分岐

### 影響を受けるテスト・呼び出し元

- `apply_date_anchor_normalization` の戻り値を検証: `tests/test_date_anchor.py`（126, 286 `== (50,100)`, 329, 338, 393, 494）
- サービス統合テスト（False理由分岐が必要）: `tests/test_image_processing_service.py:319-375`、`tests/test_receipt_processor.py:162-230`
- force 単体: `tests/test_date_anchor.py:506-685`
- `verify_date_anchor.py` は `apply_date_basis_force` を**呼ばず** force 抽出を独自実装（147, 168）→ 返り値/検証変更の影響なし。ただし P1#2 と同方針の整合性はテストで回帰確認する。

---

## 設計（変更内容）

### (A) P2#1: 返り値拡張

`apply_date_anchor_normalization(...) -> tuple[bool, str | tuple[int, int]]`

- **成功**: `(True, (offset_x, offset_y))`（既存のオフセット返却を保持。テスト 286 の `== (50, 100)` が根拠）
- **失敗**: `(False, <REASON文字列>)`

理由はモジュール定数で公開:
```python
ANCHOR_OK          = "ok"
NO_DB              = "no_db"
NO_DATE_OR_CLINIC  = "no_date_or_clinic"
NO_TEMPLATE        = "no_template"
NO_DATE_COORDS     = "no_date_coords"
NO_OCR_ENTRIES     = "no_ocr_entries"
ANCHOR_UNRESOLVED  = "anchor_unresolved"   # force 発火対象
NOT_NORMALIZED     = "not_normalized"
```

両方のサービス経路（image_processing_service.py:96-100, receipt_processor.py:225-229）:
```python
anchor_ok, anchor_detail = apply_date_anchor_normalization(...)
if not anchor_ok and anchor_detail == ANCHOR_UNRESOLVED:
    apply_date_basis_force(...)
```

### (B) P1#2: 上書き前検証 + (C) P3: 戻り値統一

`apply_date_basis_force(...) -> bool`

- **近傍一致の有無**: `search_fields_by_proximity` が返したフィールドのみ対象（既存挙動を明示化。閾値内に box が無ければ候補にすら入らない）。
- **既存値との整合**: `structured` に既存の**非空**値があり、抽出値と**異なる**場合は**上書きしない**（既存値を保守）。
  上書きするのは「既存値が空/None」or「抽出値 == 既存値（no-op）」のときのみ。
- 戻り値: `structued` を 1 つ以上更新したら `True`、それ以外（ゲート不通過・抽出・更新なし）は `False`。`(0,0)`/`None` の紛らわしい型を廃止。
- threshold 分岐（line 421-422）を削除し、既定値を `DEFAULT_PROXIMITY_THRESHOLD` に統一。

### (D) P3: テストカバレッジ

未テスト関数にテストを追加: `_is_valid_calendar_date` / `_resolve_date_basis_candidate` / `_to_ocr_entries` / `_top_left_box` / `_apply_coordinate_extraction`

### (E) P2#2: 既存失敗の検証記録

`TestRunOcr::test_calls_process_image_and_logs` が origin/main でも失敗することを実機で再現し、原因（`_run_ocr` の caplog 期待）と再現手順を記録。

---

## 変更・検証対象ファイル

| ファイル | 種別 | 内容 |
|----------|------|------|
| `app/date_anchor.py` | 修正 | 返り値拡張・定数 / force の検証&戻り値 bool / threshold 分岐削除 |
| `app/services/image_processing_service.py` | 修正 | force 呼び出し条件を `ANCHOR_UNRESOLVED` 時のみに限定 |
| `app/services/receipt_processor.py` | 修正 | 同上 |
| `tests/test_date_anchor.py` | 修正 | 返り値アサーション更新 + force 検証テスト + カバレッジ追加 |
| `tests/test_image_processing_service.py` | 修正 | force 発火条件のモック更新（False/(True,offset) 分岐） |
| `tests/test_receipt_processor.py` | 修正 | 同上 |
| `specs/2026-11-06-2-spec.md` | 修正 | 「force は anchor_unresolved 時のみ発火」「既存非空値は不変」を反映 |
| `tasks/issue_50/overview.md` | 修正 | 上記決定の反映（可能なら） |
| `.hermes/rules/design-decisions.md` | 修正 | 必要に応じ design decision 追記 |

※ PR 本文はユーザーが書き換え済みのためコミット対象外。実装のみを対象にする。

---

## 検証コマンド

```bash
uv run pytest tests/test_date_anchor.py -v
uv run pytest tests/test_receipt_processor.py -v
uv run pytest tests/test_image_processing_service.py -v
uv run pytest tests/test_verify_date_anchor.py -v
uv run pytest -q            # 全件
uv run black .              # 整形（line-length=119）
```

---

## リスク・トレードオフ

- **P1#2 の「既存非空値は不変」により、force は「空欄を埋める」補助に限定**される。anchor 未解決時に既存値が誤っている場合の自動修正は機能しない（安全側・意図どおり）。既存値の誤りはユーザー修正（Web UI）に委ねる。
- **P2#1 の返り値拡張**は公開シグネチャの破壊的変更（呼び出し元 2 箇所 + テスト 複数）。範囲限定で実施し、全テストで回帰確認する。
- `verify_date_anchor` の force 抽出は本実装と分岐しており、P1#2 の検証が効かない。「報告のみ・上書きなし」設計なので許容。必要なら別 Issue で統一検討。

## 未解決・要相談

- なし（方針はすべて確定。不明点があれば本タスク実行前に確認）。