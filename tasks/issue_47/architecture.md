# Issue #47 アーキテクチャ: date anchor 実データ検証ツール

## 概要

本ツールは、本番で日付基準補正が走る経路（`apply_date_anchor_normalization` 内部の
「候補検索 → 候補解決 → 座標再正規化 → 座標抽出」）を、**同じ関数を同じ順序で**呼ぶことで
再現し、各段階の結果を出力する。独自の判定ロジックは持たない。

`app/verify_date_anchor.py` 1 モジュールのみ追加。本番コード・DB・既存出力は一切変更しない。

## データフロー（検証実行時）

```
入力
  --raw         raw_data.json（topmost 基準・date anchor 適用前）
  --structured  structured_data.json（date と clinic を提供）
  --db          対象テンプレートを含む SQLite（coord_basis='date'）
  [--workdir]   作業領域（未指定なら tempfile.mkdtemp）
  [--report]    任意で JSON 報告の出力先
  [--threshold] proximity 閾値（default: DEFAULT_PROXIMITY_THRESHOLD=50.0）

1. 前提チェック（作業コピー上で実施）
   db_copy ← cp(--db)
   structured から date / clinic を取得
   template = get_latest_template_by_clinic(db_copy, clinic_id)
   ❹ template 存在 ??  → template 欠落 で exit 2
   ❺ template["coord_basis"] == "date" ??  → basis 違反 で exit 2
   ❻ coords["date"] is not None ??  → date 未学習 で exit 2

2. コピー作成
   raw_copy ← cp(--raw)   # 以後これを normalize が上書きしても安全

3. 段階1: 候補検索
   entries   = read(raw_copy)
   candidates = find_date_candidates(entries, structured_date)
   → 出力: 候補一覧（text / box / center）

4. 段階2: 候補解決
   anchor = resolve_date_anchor(
       entries, structured_date,
       template_date_box=coords["date"],
       template_basis="date",
       template_coords=coords,
       proximity_threshold=threshold)
   → anchor が None: 「解決不能（同点/曖昧、安全側フォールバック）」を出力し exit 1
   → 採用時の出力: 採用 box と center

5. 段階3: 座標再正規化
   norm = normalize_coordinates_by_anchor(raw_copy, anchor)
   offset = (norm["offset_x"], norm["offset_y"])
   → 出力: 適用オフセット・再正規化後の raw_copy の代表 box（先頭数件）

6. 段階4: 座標ベース抽出
   entries_date = read(raw_copy)             # date 基準に並び替え済み
   proximity = search_fields_by_proximity(entries_date, coords, threshold=threshold)
   → 出力: date 以外のフィールド抽出結果（field → text）

7. 後始末
   --workdir 指定時は残す（各段階の中間物を確認できる）。未指定なら一時領域を削除または表示のみ。
   originals 非変更（raw / db / structured はコピーのみを利用）を明示。

終了コード: 0=検証完了（抽出あり） / 1=解決不能（同点・安全フォールバック） / 2=前提不成立 or ファイル欠落
```

## 再利用するアプリ実装（ロジック非複製の根拠）

| 段階 | 呼ぶアプリ関数 | ソース | 備考 |
|------|----------------|--------|------|
| 1 | `find_date_candidates(entries, structured_date)` | `app/date_anchor.py:41` | 既存 `parse_date()` で日付正規化比較 |
| 2 | `resolve_date_anchor(entries, date, template_date_box, template_basis=.., template_coords=.., proximity_threshold=..)` | `app/date_anchor.py:80` | date 基準で候補複数は `_resolve_date_basis_candidate`（layout scoring、同点 None） |
| 3 | `normalize_coordinates_by_anchor(raw_copy, anchor_box)` | `app/coord_normalizer.py:185` | 戻り dict に `normalized` / `offset_x` / `offset_y`、raw_copy を date 基準に上書き |
| 4 | `search_fields_by_proximity(entries_date, coords, threshold=..)` | `app/coord_search.py:207` | field→text マッピングを返す |

呼び出し順序は `app/date_anchor.apply_date_anchor_normalization()`（行 297-308, 308-313, 385）と揃える。
閾値は `app/structural_parser.DEFAULT_PROXIMITY_THRESHOLD`（50.0）を既定値にする。

## 前提判定・exit コード

| exit | 意味 | 出力例 |
|------|------|--------|
| 0 | 検証完了（anchor 解決 → 再正規化 → 抽出あり） | 成功トレース全文 |
| 1 | 候補が解決不能（同点/曖昧で None） | 「解決不能: date 候補が同点のため選択不可（安全側フォールバック）」 |
| 2 | 前提不成立または入力欠落 | 「対象テンプレートが見つかりません」「coord_basis が 'date' ではありません（現在: topmost）」等 |

## 非破壊の保証

- `--db`・`--raw` は必ず作業コピーに複製し、**コピーだけ**を読み書きする。オリジナルは `shutil.copy2` 時点の
  ハッシュを記録し、終了時に不変であることを確認（検証の健全性のため）。
- `insert_receipt` / `process_input_json` は呼ばない（レシート重複登録を防ぐ — 参照
  `references/date-anchor-normalization.md` の pitfall）。
- `--structured` は読み取りのみ（ツールは structured を書き換えず、write はしない。抽出結果は標準出力 /
  任意の `--report` 先にのみ書き出す）。
- DB は読み取り専用で開く（`get_latest_template_by_clinic` のみ使用。`get_or_create_clinic` は INSERT する
  ため使わない）。
- 本番コード（`date_anchor.py` / `coord_normalizer.py` / `coord_search.py` / `structural_parser.py` /
  `db.py` / `main.py` / 両オーケストレータ）は変更しない。

## ファイル構成（新規のみ）

```
app/verify_date_anchor.py      # 検証 CLI（argparse + verify_date_anchor() + main()）
tests/test_verify_date_anchor.py  # ツールの単体テスト（git add -f で追跡）
```

## 入力ファイル名は不問（rename してもよい）

- ツールは ``--raw`` / ``--structured`` を **JSON のパス**として読むだけ。ファイル名の形式
  （IMG_{日付} 等）の判定・整形・弾く処理はない。
- ``--raw`` は作業領域へ ``raw_data.json``（固定名）としてコピーし、``--structured`` は指定パスを
  そのまま読む。判定は structured の中身の ``clinic`` / ``date`` と DB の date 基準 template に依存し、
  ファイル名には依存しない。
- よって、コピーして ``1-raw_data.json`` / ``1-structured_data.json`` のような短名へ rename して渡しても
  問題ない。
- **注意（本ツール外・将来の参考）**: 本番の ``apply_date_anchor_normalization`` 経路は
  ``update_receipt_ocr_json_by_source`` で ``receipts`` 行を source_path の部分一致で照合するため、本番で
  raw を rename すると DB の receipt 行との照合が切れ得る。ただし本検証ツール（案A）は DB を書き換えない
  ため、rename は影響しない。

## サンプル入力（リポジトリ内で実データとして使えるもの）

- DB: `data/verify-01.db` — なの花薬局札幌二十四軒店 template が `coord_basis='date'`、date box は原点
  （例 `[1,0],[273,11],...`）。
- raw / structured: `output_json/IMG_20261028_155506_1793242101-{raw,structured}_data.json`
  （clinic=なの花薬局札幌二十四軒店, date=2026-10-28）。
- raw: `output_json/IMG_20260929_104357_1790614680-raw_data.json` は「調剤日/発行日」の**同日付候補 2 件**
  （2026-09-02）を持つため、候補一覧表示の確認に有用（対応 template が date でない場合は exit 2 になる）。

### 実測例（2026-11-02、上記なの花薬局サンプル）

サンプルコマンド実行で:
- `[段階1]` candidates 2 件（`調剤日：2026/10/28` box `[7,295]..` / `発行日：2026/10/28` box `[6,325]..`）。
- `[段階2]` レイアウト整合スコアリングで `調剤日` box `[[7,295],[279,306],[278,337],[6,326]]` を採用。
- `[段階3]` 適用オフセット `(6, 295)`。
- `[段階4]` 抽出 `name='相原'` / `clinic='なの花薬局札幌二十四軒店'` / `amount='￥1,450'`。exit 0。
- 実行前後で raw / db の sha256 一致（オリジナル不変）。
- 前提不成立（clinic が DB に無い）例 → `[前提不成立] ...` で exit 2。