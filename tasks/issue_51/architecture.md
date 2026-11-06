# Issue #51: アーキテクチャ設計 — coord_basis の値の調整

## 全体データフロー

```mermaid
graph TD
    subgraph "Web フォーム修正（受入条件の核）"
        PUT["PUT /{file_stem}<br/>ReceiptUpdater.update_receipt()"] --> OCR["OCRCoordinateService<br/>process_feedback()"]
        OCR --> RESOLVE["_resolve_coord_results()<br/>レシート ocr_json（topmost 基準）から<br/>フィールド座標を決定"]
        RESOLVE --> FB["process_correction_feedback() ★変更"]
        FB --> EXIST{"既存 template<br/>coord_basis == 'date' ?"}
        EXIST -->|yes| RESET["coord_basis を 'topmost' に戻す"]
        RESET --> UPSERT["upsert_template() ★変更<br/>coord_basis 引数対応"]
        EXIST -->|no / topmost のまま| UPSERT
        UPSERT --> HIST["insert_template_history()<br/>change_reason='user_correction'"]
    end

    subgraph "テンプレート管理画面"
        ADMIN["GET /admin/templates<br/>TemplateAdminService.list_templates()"] --> READ["templates.coord_basis を読む"]
        READ --> DISP["topmost / date 表示"]
    end
```

## モジュール構成

### 変更モジュール

| モジュール | 変更内容 | 責務 |
|-----------|---------|------|
| `app/db.py` | `upsert_template()` に任意引数 `coord_basis: str | None = None` を追加 | INSERT/UPDATE の SQL を `coord_basis` を含む形に拡張 |
| `app/template_feedback.py` | `process_correction_feedback()` で既存 basis が `date` なら `topmost` へ戻す | 修正で書き込む topmost 座標と `coord_basis` の整合を保証 |

### 変更しないモジュール

| モジュール | 理由 |
|-----------|------|
| `app/services/ocr_coordinate_service.py` | 座標解決ロジックは不変。`process_feedback` が既存 `field_coords_map` のまま `process_correction_feedback` を呼ぶ |
| `app/services/receipt_updater.py` | 修正フローは不変 |
| `app/web/server.py` / `admin_templates.html` | 管理画面は DB の `coord_basis` を読んで表示するだけ（受入条件2は DB 値修正で自動反映） |
| `app/db.py::update_template_basis` | topmost→date のトグル用。本 Issue では触らない |
| `app/coord_normalizer.py` / `date_anchor.py` | 本 Issue のスコープ外 |

## 設計詳細

### 1. `upsert_template()` の `coord_basis` 対応（`app/db.py`）

`coord_basis` は `moment` でなく任意引数とし、**未指定なら既存挙動を維持**する（呼び出し元の後方互換）。

```python
def upsert_template(
    db_path: str | Path,
    template_id: str,
    clinic_id: str,
    version: int,
    coords_corrections: Dict[str, Any],
    coord_basis: Optional[str] = None,  # 追加
) -> None:
    coords_str = json.dumps(coords_corrections, ensure_ascii=False)
    conn = get_db_connection(db_path)
    try:
        with conn:
            # 新規 INSERT: coord_basis 指定があればそれ、無ければ COALESCE(?, 'topmost') でスキーマ既定に従う
            conn.execute(
                """
                INSERT INTO templates (id, clinic_id, version, coords_corrections, coord_basis)
                VALUES (?, ?, ?, ?, COALESCE(?, 'topmost'))
                ON CONFLICT(id) DO UPDATE SET
                    clinic_id = excluded.clinic_id,
                    version = excluded.version,
                    coords_corrections = excluded.coords_corrections
                    {coord_basis_set}
                """,
                (template_id, clinic_id, version, coords_str, coord_basis),
            )
    finally:
        conn.close()
```

**ポイント**: JOIN/CTE を使わず、`coord_basis` が `None` なら UPDATE 対象から除外する必要がある。
単純に `coord_basis = excluded.coord_basis` を常に入れると、`None` 指定時に既存値を壊すため、条件分岐で SQL 文字列を組み立てる。
具体的な Safe な実装（`tasks.md` に complete code 記載）:
- `coord_basis` が非 None なら UPDATE 句に `coord_basis = excluded.coord_basis` を含める
- `coord_basis` が None（従来呼び出し）なら従来どおり `clinic_id/version/coords_corrections` のみ UPDATE（coord_basis は不変）

### 2. `process_correction_feedback()` の coord_basis リセット（`app/template_feedback.py`）

既存テンプレの `coord_basis` を取得し、`date` なら `topmost` に戻して `upsert_template` へ渡す。

```python
existing = get_latest_template_by_clinic(db_path, clinic_id)
existing_basis = (existing or {}).get("coord_basis")  # ない（新規）なら None
new_basis = "topmost" if existing_basis == "date" else existing_basis
# 新規作成時/existing が topmost のときは None（従来どおり既定 topmost）

upsert_template(
    db_path=db_path,
    template_id=template_id,
    clinic_id=clinic_id,
    version=new_version,
    coords_corrections=updated_coords,
    coord_basis=new_basis,
)
```

**根拠**: 修正座標はレシート ocr_json（topmost 基準）由来。既存 `coord_basis=='date'` なのに topmost 座標で上書きするのは
矛盾なので、`coord_basis` を `topmost` に戻して両者を揃える。既に `topmost` なら変更不要。

### 3. 履歴と管理画面

- `process_correction_feedback` は既存どおり `insert_template_history`（`change_reason='user_correction'`）を呼ぶ。
  coord_basis の変更もこの履歴で代表される（多段の履歴追加は YAGNI で実施しない）。
- `TemplateAdminService.list_templates()` は `t["basis"] = t.get("coord_basis") or "topmost"` で DB 値表示。DB 値が
  `topmost` に戻れば自動で管理画面も `topmost` 表示になる（受入条件2、コード変更不要）。

## エラーハンドリング

| シナリオ | 対応 |
|---------|------|
| 既存 template なし（新規作成） | `existing_basis=None` → `upsert` は既定 `topmost`（従来どおり） |
| `coord_basis` が不明（旧データ等） | `(existing or {}).get(...)` で None → 既定 topmost のまま。破壊しない |
| `upsert_template` の UPDATE セットが空にならないよう | `coord_basis` 非 None の場合のみ SQL に付与（既存必須カラム update は維持） |
| low_confidence 等で feedback 自体が走らない | 既存フローが `ReceiptUpdater` 側でゲートする。変更なし |
