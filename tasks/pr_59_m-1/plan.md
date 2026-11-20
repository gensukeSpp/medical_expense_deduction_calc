# PR #59 レビュー指摘 [中]#1 対応: 実装計画

> 対象: `.github/reports/pr-59-review.md` の指摘事項 [中]#1
> 「pending の解除が『その画像が新しく処理された』と確認できない」
> 対象モジュール: `app/services/watch_controller.py`（`_clear_pending_if_consumed()`）
> 前提: レビュー指摘は**正しい**。ソース・テスト確認済み（下記「現状」参照）。

## Goal

`copy_image()` でコピーした**その画像**が実際にスキャンで消費された（出力 JSON 生成 or
failed 退避）場合にのみ pending を解除する。ファイル名（stem）だけの glob 一致による
「過去出力が残っているだけで解除」を排除し、同じ stem の再アップロード時に受付可否が
実データ状態とずれる問題を解消する。

## 現状（ソース確認済みの事実）

### 問題箇所: `app/services/watch_controller.py:296-313`（`_clear_pending_if_consumed`）

```python
stem = Path(self._pending_image).stem
consumed = (
    list(self.output_dir.glob(f"{stem}_*-raw_data.json"))
    or list(self.output_dir.glob(f"{stem}_*-structured_data.json"))
    or (self.failed_dir / self._pending_image).exists()
)
```

- `{stem}_*-raw_data.json` / `{stem}_*-structured_data.json` の **glob は stem 一致のみ**。
  過去に同名画像（例: `IMG_001.jpg`）を処理した出力 `IMG_001_<旧mtime>-structured_data.json` が
  残っていると、**新しい画像が未処理でも** pending が即解除される。
- `(self.failed_dir / self._pending_image).exists()` も同名の過去 failed コピーで誤解除される。

### 利用できる事実（修正の鍵）

1. **出力ファイル名は元画像の mtime を埋め込む**:
   `app/services/receipt_processor.py:126` で
   `out_fname = f"{image_path.stem}_{mtime}-raw_data.json"`（`mtime = int(image_path.stat().st_mtime)`）。
   structured も同様に `{stem}_{mtime}-structured_data.json`（同ファイル L240）。
2. **`copy_image()` は `os.fdopen(fd, "wb")` で新規作成する**（`watch_controller.py:475-481`）ため、
   コピー先ファイルの mtime は「そのアップロードのコピー時刻」＝アップロードごとに一意。
3. **失敗退避は `shutil.copy2`**（`app/services/file_repository.py:60-67`）で mtime が保存される。
   同名 failed が既にある場合は `{int(time.time())}_{name}` のプレフィクス付きになるため、
   名前一致だけでは特定できない（mtime+size で特定する）。
4. **成功時は元画像が input_dir に残る**（`move_to_processed` の呼び出し元なし）。
   失敗時も `copy2` のため元画像は残る。→ 「input_dir から消えたか」では判定できない。

### 既存テストの状況（レビュー指摘のカバレッジ欠落）

- `tests/test_watch_controller.py:245-264`（`test_scan_clears_pending_when_structured_generated`）は
  **スキャン内で新規出力を生成した場合のみ**検証。過去出力が残る再アップロードケースが無い。
- 同ファイル L267-274（`test_pending_cleared_when_image_moved_to_failed`）は failed コピーを
  `write_bytes` で作成しており、mtime がコピー時刻と一致しない（新ロジックでは要修正）。
- 同ファイル L448-459（`test_copy_image_rejects_existing_same_name`）は `_pending_process` /
  `_pending_image` を直接リセットしており、新設するスナップショット項目のリセットが必要。

## 対応方針（確定）

### 方針: コピー時の mtime+size スナップショットを記録し、その画像固有の成果物で判定する

レビュー対応方針「コピー時にファイルの mtime または処理開始時のスナップショットを記録し、
その画像を実際にスキャン／移動／失敗退避したことを基準に pending を解除」に従う。
出力ファイル名に mtime が埋め込まれる既存仕様を利用し、**glob をやめて完全一致**で判定する。

#### 変更 1: `copy_image()` でスナップショットを記録

`app/services/watch_controller.py` の `__init__` に状態を追加:

```python
self._pending_image: str | None = None
self._pending_mtime: int | None = None   # [PR59-中1] コピー時 mtime（秒）
self._pending_size: int | None = None    # [PR59-中1] コピー時 size（同一秒アップロード対策）
```

`copy_image()` の書き込み成功後（`self._pending_image = name` の直後）に記録:

```python
st = dest.stat()
self._pending_mtime = int(st.st_mtime)
self._pending_size = st.st_size
```

#### 変更 2: `_clear_pending_if_consumed()` を mtime 完全一致ベースに書き換え

```python
def _clear_pending_if_consumed(self) -> None:
    if not self._pending_process or self._pending_image is None:
        return
    if self._pending_mtime is None or self._pending_size is None:
        return  # スナップショット未記録（旧状態）は解除しない
    stem = Path(self._pending_image).stem
    mtime = self._pending_mtime
    consumed = (
        (self.output_dir / f"{stem}_{mtime}-raw_data.json").exists()
        or (self.output_dir / f"{stem}_{mtime}-structured_data.json").exists()
        or self._failed_copy_exists()
    )
    if consumed:
        LOG.info("Pending image processed: %s", self._pending_image)
        self._reset_pending()
```

- **成功判定**: `{stem}_{mtime}-raw_data.json` / `{stem}_{mtime}-structured_data.json` の
  **完全一致**（glob 不使用）。mtime が埋め込まれた出力が存在＝その画像が処理された。
- **失敗判定** `_failed_copy_exists()`: `failed_dir` を走査し、
  `int(f.stat().st_mtime) == self._pending_mtime and f.stat().st_size == self._pending_size`
  のファイルが存在すれば退避済みとみなす（`copy2` が mtime を保存するため一致する。
  プレフィクス付き failed 名にも対応）。
- **解除処理の共通化**: `_pending_image` / `_pending_process` / `_pending_mtime` /
  `_pending_size` をまとめてクリアする private メソッド `_reset_pending()` を新設する。

#### 変更 3: 既存テストの修正（新ロジックに整合させる）

1. `test_scan_clears_pending_when_structured_generated`（L245）: fake scan の出力名を
   ハードコード `_123-` から `int(p.stat().st_mtime)` を使う形に変更。
2. `test_pending_cleared_when_image_moved_to_failed`（L267）: failed コピーを
   `shutil.copy2(ctl.input_dir / "IMG_001.jpg", ctl.failed_dir / "IMG_001.jpg")` で作成し
   mtime を保存する（`write_bytes` は mtime が変わってしまうため）。
3. `test_copy_image_rejects_existing_same_name`（L448）: 直接代入の代わりに
   `ctl._reset_pending()` を使う（または新フィールドもリセット）。

#### 変更 4: 新規テストの追加（レビュー指摘のカバレッジ欠落を埋める）

`tests/test_watch_controller.py` の「[中-3] 処理待ち状態」セクションに追加:

1. **過去出力が残っていても pending は解除されない**（再アップロードケース）:
   旧 mtime の `IMG_001_9999999999-structured_data.json` を事前作成 → `copy_image` →
   何も処理しない scan → pending は True のまま。
2. **mtime 一致の新規出力で解除される**: `copy_image` → 実 mtime で
   `{stem}_{mtime}-structured_data.json` を生成する fake scan → pending は False。
3. **mtime+size 一致の failed コピーで解除される**: `copy_image` → `copy2` で failed 退避 →
   pending は False。
4. **過去の failed コピー（mtime 不一致）では解除されない**: 旧 mtime の failed コピーを
   事前作成 → `copy_image` → 何も処理しない scan → pending は True のまま。

## 設計判断（取捨選択）

| 論点 | 採用 | 理由 |
|------|------|------|
| 完了判定の基準 | **コピー時 mtime+size スナップショット** | 出力ファイル名に mtime が埋め込まれる既存仕様と整合。glob をやめ完全一致にできる。レビュー方針（mtime 記録）に忠実 |
| スナップショットの粒度 | mtime（秒）+ size | 同一秒内の同名再アップロードでも size で区別。pending ロックにより同一秒の再アップロードは実質不可能だが、防御的に併用 |
| failed 退避の判定 | failed_dir 走査で mtime+size 一致 | `move_to_failed` は `copy2`（mtime 保存）＋同名時プレフィクス付与のため、名前一致では特定できない |
| スキャン結果の戻り値利用 | 不採用 | `scan_and_process` は処理件数のみ返し、どのファイルを処理したか返さない。scan_fn の契約変更は影響範囲が大きく、mtime 方式で十分 |
| 入力ファイルの消滅判定 | 不採用 | 成功時も失敗時も元画像は input_dir に残るため判定不能 |
| `get_status()` の変更 | しない | スナップショットは内部状態。`test_get_status_shape` のキー集合を変えない |

## スコープ外（既知の制限）

- **読込不能画像（raw 未生成・failed 退避なし）**: `_sync_process` が例外なく `True` を返し
  成果物が一切残らないため、pending は解除されない（既存の問題 1 との相互作用）。
  Issue #58 の plan で「別 Issue として提案」とされた再処理ループ対策（`move_to_failed` の
  `shutil.move` 化・raw 未生成の失敗扱い）が解消すれば本件も自動的に解消する。
  本指摘 [中]#1 は「誤解除（false positive）」の修正が目的であり、この false negative は
  スコープ外とする。
- 指摘 [中]#2（`--serve --input-dir` のパス制限）と [中]#3（原子性）は別タスク。

## ファイル変更表

| ファイル | 変更種別 | 内容 |
|----------|---------|------|
| `app/services/watch_controller.py` | 修正 | `__init__` に `_pending_mtime` / `_pending_size` 追加。`copy_image()` でスナップショット記録。`_clear_pending_if_consumed()` を mtime 完全一致ベースに書き換え。`_reset_pending()` / `_failed_copy_exists()` を新設 |
| `tests/test_watch_controller.py` | 修正+追加 | 既存 3 テスト修正（mtime 整合・`_reset_pending()` 利用）。新規 4 テスト追加（過去出力/過去 failed での誤解除防止、mtime 一致での解除） |

## 検証

```bash
# 対象テスト
uv run pytest tests/test_watch_controller.py -q

# 回帰（Web サーバ経由の pending 遷移）
uv run pytest tests/test_web.py -q

# フルスイート
uv run pytest -q
```

期待: 全テスト green（既存 409 件 + 新規 4 件 − 修正対象は同数維持）。
