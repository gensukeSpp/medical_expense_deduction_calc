# Issue #50: date anchor による補正の強制

## 目的

`templates.coord_basis` が `date` のクリニックで、クリニック名の抽出に成功したにもかかわらず date anchor
補正が実行できない（`resolve_date_anchor` が date 候補 0 件で `None` を返す）ケースでも、**テンプレートの
`coords_corrections` を使って強制的に座標ベースの抽出（name / amount）を試みる**機能を追加する。

## ゴール

- 受入要件1: 現状機能が Issue 記載の結論（date anchor 補正には topmost からの date 座標値の特定が必須）と
  齟齬がないことを確認・記録する。
- 受入要件2: #47 検証ツールで「強制補正」の結果を可視化できる。
- 受入要件3: 強制条件（`coord_basis==date` 且つ クリニック名抽出成功）を厳守する。

## 背景（Issue の結論との整合）

現行 `apply_date_anchor_normalization()`（app/date_anchor.py）は、`find_date_candidates` → `resolve_date_anchor`
で raw 内の「構造化 date と一致する日付 box」を特定できなければ `None` を返し、date 基準への再正規化も
座標抽出も一切起きない。実データでは「date 候補一覧 (0 件)」が頻発し、date anchor 補正が成果に至らない。

**⇒ これは Issue #50「結論」と一致している**（date 座標値の特定が必須）。したがって差し戻し不要で、
「日付 box を特定せずに coords を当てはめて強制抽出する」拡張が必要。

## 参照

- [Issue #50](https://github.com/gensukeSpp/medical_expense_deducation_calc/issues/50)
- [Issue #47 実装](../../tasks/issue_47/overview.md) — date anchor 実データ検証ツール
- [Issue #39 実装](../../tasks/issue_39/overview.md) — date 基準正規化・coord_basis
- [Issue #42 実装](../../tasks/issue_42/overview.md) — 手動トグル・date 基準判別
- [coordinate-normalization ルール](../../.hermes/rules/coordinate-normalization.md)
- `app/date_anchor.py` — anchor 解決・適用 / `_apply_coordinate_extraction`
- `app/coord_search.py` — `search_fields_by_proximity` / `_calculate_box_center`
- `app/coord_normalizer.py` — `shift_template_to_date_basis` / `normalize_coordinates_by_anchor`
- `app/services/image_processing_service.py` — 画像OCR経路（統合点）
- `app/services/receipt_processor.py` — watcher 経路（統合点）
- `app/verify_date_anchor.py` — #47 検証ツール

## スコープ

### 含むもの
- 受入要件1の確認結果の記録（本 overview の「背景」節 + 実装タスク内で仕様反映）
- #47 検証ツールへの **force モード**追加（受入要件2）
- **強制 date 基準抽出**関数の新設（`apply_date_basis_force()`）
- 両処理経路（画像OCR / watcher）への統合
- 単体・結合テスト

### 含まないもの（次回以降）
- date → topmost 逆方向の仕様（#51 で別途）
- `resolve_date_anchor` の曖昧性緩和（既存の layout スコアリングの踏襲のみ）
- RealLLMClient 統合

## 受入条件

1. 現状機能の「結論（date 座標値の特定が必須）」との齟齬がないことを確認する（受入要件1）。
2. #47 検証ツール `--force` で、強制された date anchor による検証結果を確認できる（受入要件2）。
3. 強制補正は `coord_basis==date` 且つ クリニック名抽出成功した場合のみ実行される（受入要件3）。
4. 強制補正後、structured に対し `coords_corrections` で座標抽出を再適用できる。
5. 既存テストが全てパス（回帰なし。既知の事前存在失敗2件を除く）。

## 設計判断（対策の取捨選択）

### 対策1: 強制抽出の実現方法
- **案A（採用・本実装）**: `search_fields_by_proximity` を date 基準 template の `coords_corrections` に対して、
  既存 `_apply_coordinate_extraction` の延長で実行（transform 前提）。**再正規化**は行わず、「template 座標を
  強制適用 → raw の box を近傍抽出」で name/amount を取得。既存の `search_fields_by_proximity` / `_calculate_box_center`
  を再利用し、新規レジストレーションを書かない（YAGNI・手戻り防止）。
  - **#47 の「date 候補一覧 (0 件)」問題の本質**は「日付 box を OCR テキストで特定できない」ためであり、座標抽出自体は
    template の coords さえ揃えば可能。
- 案B（検討のみ）: `match_template_by_layout` 相当のオフセット推定で raw を再正規化。実装量・検証コスト大。
  今回はスコープ外とし、必要なら後続 Issue で検討。

### 対策2: 強制のゲート
- `coord_basis=='date'` **かつ** `structured.clinic` が非空（クリニック名抽出成功）**かつ** template の
  `coords_corrections` に date が学習済み。
- low_confidence 時（`not low_confidence`）と `apply_date_anchor_normalization` が成功した場合（anchor 確定）は
  通常の date 基準パスに任せ、force は**補完**としてのみ発動（二重補正防止）。

### 対策3: structured への反映・二重登録防止
- `_apply_coordinate_extraction` と同様、`structured` を in-place 更新 + `OutputWriter.write` で
  `*-structured_data.json` を書き直す。`insert_receipt` は呼ばない（二重登録なし）。DB 座標同期は `update_receipt_ocr_json_by_source`。

### 対策4: 検証順序
- **force モードを #47 ツールに先行実装**し、実データで強制抽出の効果を確認してから本番経路へ統合する（案Cの考え）。

## 悩みどころ（相談事項）

### Q1: 強制適用時、`coords_corrections` の基準が date で raw が topmost の基底不一致をどこまで許容するか
- **案A（推奨）**: 強制は date 基準 template に対して `search_fields_by_proximity` を適用し、近傍に一致すれば
  採用。適合しなければ値が取得されない = 安全側に落ちる。基底が一致しなくとも「試みる」のが本 Issue の意図。
- 案B: 事前に raw を date 基準へ変換してから抽出（精確だが実装大）。
- → **案A で進める**。

### Q2: 強制を date 未学習 template にまで適用するか
- 適用しない（**受入要件3 の趣旨**）。「date box 未学習のまま強制 date 化すると前方補正が崩れる」#40 ガードと整合。
- date 未学習 template は force 対象外。

## ファイル変更サマリ（予定）

| ファイル | 変更種別 | 変更規模 |
|----------|---------|---------|
| `app/date_anchor.py` | 修正（`apply_date_basis_force()` 追加） | ~45行 |
| `app/services/image_processing_service.py` | 修正（統合） | ~6行 |
| `app/services/receipt_processor.py` | 修正（統合） | ~6行 |
| `app/verify_date_anchor.py` | 修正（`--force` モード） | ~40行 |
| `tests/test_date_anchor.py` | 修正（force 単体テスト） | ~50行 |
| `tests/test_verify_date_anchor.py` | 修正（force 検証テスト） | ~30行 |
| `tests/test_image_processing_service.py` | 修正（統合テスト） | ~25行 |
| `tests/test_receipt_processor.py` | 修正（統合テスト） | ~25行 |

## 実装順序

Task1 受入要件1確認 → Task2 #47 force モード（先行検証）→ Task3 force 抽出の単体実装 → Task4 両経路統合 →
Task5 結合テスト・全テスト・コミット。詳細は `tasks.md`。
