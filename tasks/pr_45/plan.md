# PR #45 レビュー指摘 [中-1] 対応: 手動切替後の date 基準補正を実際に走らせる実装計画

> 対象: `.github/reports/pr-45-review.md` の 指摘事項 [中-1]
> 前提: レビュー指摘は**正しい**。PR #45 の「topmost では抽出 (*-structured_data.json に出力)
> できなかったデータの抽出の成功があった」という主張は**誤り**（後述の再現で確認）。

## Goal

Issue #42 で追加した Web 管理画面からの coord_basis 手動切替（topmost → date）を、
**実際に date 基準の補正が走る状態**へ直す。つまり:

- 手動切替後、対象 clinic（date 候補が 2 件：調剤日/発行日）のレシート処理で
  `resolve_date_anchor` が従来どおり None を返し、raw が topmost のまま落ちる問題を解消する。
- さらに、date 基準へ再正規化した後、**date 基準テンプレートによる座標ベースのフィールド抽出**を
  実際に適用し、`*-structured_data.json` へ反映する。これにより PR #45 の主張を「実際に成立する」形にする。

## 現状（再現した事実）

実データ `data/verify-01.db`（クリニック「なの花薬局札幌二十四軒店」）で確認した挙動:

1. 手動切替後、template は `coord_basis='date'`、date box は原点 `[[0,0],[357,4],[357,35],[0,31]]` に収まる。
2. 実 raw `output_json/IMG_20260929_104357-1_1790647522-raw_data.json` には date 候補が **2 件**
   （`調剤日：2026/09/02` box `[132,269]..` と `発行日：2026/09/02` box `[127,298]..`）。両方とも同じ
   `2026-09-02` に正規化されるため `find_date_candidates` が 2 件を返す。
3. `resolve_date_anchor(..., template_basis='date')` は `template_basis != "topmost"` のガード
   （`app/date_anchor.py:122`）で **None を返す**。→ `apply_date_anchor_normalization` が早期 return し、
   raw は date 基準へ再正規化されない（topmost のまま）。
4. `structural_parser.py:155` の `coord_basis != "date"` ガードにより、date 基準テンプレートは
   前方補正（座標ベースのフィールド上書き）を**スキップ**する。→ このクリニックでは座標抽出が効かず
   LLM/テキスト抽出フォールバックのみ。

結論: 手動切替は「テンプレートだけ date 化」に留まり、raw 側の date 基準化・座標抽出は一切走らない。
PR #45 の「topmost では抽出できなかったデータを抽出できた」は未成立。レビュー [中-1] は正確。

## 対応方針（確定）

レビュー [中-1] の修正方向 (a) を採用（「手動切替で date 基準の補正を検証可能にする」という
Issue #42 の目的に沿う。方向 (b) の文書辞退では目的を達せられないため不採用）。

座標正規化・date anchor の既存ルール（`.hermes/rules/coordinate-normalization.md`）に従い，
閾値は新規導入しない。既存 `DEFAULT_PROXIMITY_THRESHOLD`(50.0) を流用する。

### 変更 1: `resolve_date_anchor` が date 基準テンプレートでも 2 候補を解決できるようにする

`app/date_anchor.py` の `resolve_date_anchor` に `template_coords`（date 基準テンプレート全体の
`coords_corrections`）を渡せるようにし、`template_basis=='date'` かつ候補複数のときに
**レイアウト整合スコアリング**で一意な候補を選ぶ。

- 単一候補の fast path・topmost 基準の近接 disambiguation は既存のまま（回帰なし）。
- date 基準時: 各候補 box の top-left を anchor と仮定し、date 基準テンプレートの **date 以外の
  フィールド**（name / clinic / amount）を `フィールド中心 + 候補top-left` で実 raw の座標系へ投影。
  その中心と実 raw box 中心との距離を近接探索と同様に測る。
- スコア = （50px 外のフィールド数, 日付以外フィールドの距離合計）。辞書順最小の候補を採用。
- date 候補が今回の実データのように全フィールド 50px 内に落ちるケース（距離合計 76 vs 114）でも
  距離合計で 調剤日 が一意に選ばれる（実測）。同一スコアの候補が 2 つ以上ある場合は従来どおり None。

### 変更 2: date 基準へ再正規化した後、date 基準テンプレートで座標ベース抽出を適用する

`apply_date_anchor_normalization` が成功（offset を返す）するとき、以下を追加する:

- 再正規化済み raw（date 基準）を再読込し、`search_fields_by_proximity` を date 基準テンプレート
  coords と `proximity_threshold=DEFAULT_PROXIMITY_THRESHOLD` で実行。
- 空でない結果を `structured` へ上書き（`date` フィールドは除外）。
- `OutputWriter.write()` で `*-structured_data.json` を再出力（既存の命名ロジックを再利用）。
- 受け入れ条件どおり**レシートを二重登録しない**（`insert_receipt` は呼ばない。
  `apply_date_anchor_normalization` は既に `update_receipt_ocr_json_by_source` で ocr_json のみ更新する設計）。

これをパラメータ `output_dir`（省略時 None = 書出しなし）で `apply_date_anchor_normalization` に
追加し、両オーケストレータ（`image_processing_service.py` / `receipt_processor.py`）から渡す。
これにより date 基準テンプレートの前方補正を「再正規化後は正しく適用」できる（#40 ガードの意図
＝ topmost raw × date テンプレートの基底不一致は変わらず防ぐ）。前方補正の時点（topmost raw）は
#40 ガードのままスキップするので変更なし。

## 変更ファイル一覧

| ファイル | 変更種別 | 内容 |
| --- | --- | --- |
| `app/date_anchor.py` | Modify | `resolve_date_anchor` に date 基準の 2 候補解決（レイアウト整合スコアリング）を追加。`apply_date_anchor_normalization` に `output_dir` を追加し、date 基準再正規化後の座標抽出と structured_data 再出力 |
| `app/services/image_processing_service.py` | Modify | `apply_date_anchor_normalization(..., output_dir)` を渡す |
| `app/services/receipt_processor.py` | Modify | 同上 |
| `tests/test_date_anchor.py` | Modify(未追跡→要 `git add -f`) | 新規テスト（変更1・2）を追記 |
| `tests/test_image_processing_service.py` | Modify(tracked) | mock_anchor 呼び出しが `output_dir` を受ける想定のアサーション調整（必要なら） |
| `tests/test_receipt_processor.py` | Modify(tracked) | 同上 |
| `.hermes/rules/coordinate-normalization.md` | Modify | 手動切替後に「raw 側の date 基準化＋座標抽出」が走る仕様を追記 |
| `AGENTS.md`, `docs/schema.sql` | 変更なし | スキーマ・ガード方針は不変 |

## 実装ステップ（概要）

1. `resolve_date_anchor` の date 基準 2 候補解決をテスト First で実装（失敗テスト → 実装 → PASS）。
2. `apply_date_anchor_normalization` の `output_dir` 追加と座標抽出・再出力を実装（失敗テスト → 実装 → PASS）。
3. 両オーケストレータが `output_dir` を渡すよう修正。
4. 実データ（`data/verify-01.db` のコピー + 上記 raw）で end-to-end に「raw が date 基準へ再正規化され、
   structured_data.json に座標抽出結果が反映される」ことを検証。
5. `uv run pytest` 全体 + `black .`。既知の事前失敗（test_image_processing_service の 2 件）以外は通過を確認。
6. ドキュメント整備 + コミット（コミットはユーザー確認後に）。

## テスト / 検証

- 単体: `tests/test_date_anchor.py` に
  - `TestResolveDateAnchor::test_duplicate_date_basis_picks_by_layout`（調剤日と発行日、date 基準で 調剤日 が選ばれる）
  - 同一候補スコアだと None に落ちるテスト
  - `apply_date_anchor_normalization` に `output_dir` を渡すと structured_data.json に座標抽出結果が反映されるテスト
- 統合: 既存 `test_image_processing_service.py` / `test_receipt_processor.py` の
  `apply_date_anchor_normalization` mock 呼び出しが回帰しないこと。
- 全体: `uv run pytest -q`（既知の事前失敗 2 件のみ許容。追跡対象外の `tests/test_date_anchor.py` があるため
  実行結果はローカルと一致すること。本当に合計 251 にするなら `git add -f tests/test_date_anchor.py` で追跡化も可）。
- `black .`（line-length=119）。

## リスク・トレードオフ・未確定事項

- **レイアウト整合スコアリングの信頼性**: 今回の実データでは 距離合計 で一意に選べたが、他の clinic で
  date 候補 2 件がほぼ同位置にある場合に誤選択する可能性。対策として **同一点数の候補が複数なら None**（従来どおり
  安全側フォールバック）を維持する。新規閾値を導入せず既存 50px を使う（リスク受容: 実データで破綻するサンプルが
  出たら、その時に対策を別 Issue で検討）。
- **structured 上書きの影響**: 座標抽出結果で `normalizer.normalize` 済みの値を上書きするため、
  text 抽出と coords 抽出のどちらが正かは座標抽出優先となる。これは topmost テンプレートの既存前方補正と同挙動。
- **スコープ**: 逆方向（date → topmost）切替は対象外。既存レシートの一括再正規化も対象外。
- **未確認**: knowledge graph の PR head 向け再構築は今回実施しない（graph は作業ツリー基準で鮮度不明のため
  ソース検証を優先）。

## 相談事項（実装前確認・推奨）

- **Q1**: 変更 2 を「`apply_date_anchor_normalization` 内に組み込む」案A（推奨）vs
  「オーケストレータ側に別関数で呼び足す」案B。A はロジック集約・回帰リスク小さめ。B は date_anchor の責務（正規化）を
  保つが 2 箇所に同じ結合が並ぶ。→ **推奨 A で進めて問題なければ**。
- **Q2**: `tests/test_date_anchor.py`（.gitignore の `test_*.py` に一致し未追跡）を `git add -f` で
  追跡化して PR head で検証数値を再現できるようにするか。→ **推奨: 追跡化する**（[中-2] の再現性問題にも寄与）。
  ただしユーザー指示を優先。