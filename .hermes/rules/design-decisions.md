# 重要な設計判断 (Design Decisions)

> AGENTS.md から切り出した doc。更新頻度が高い設計判断・理由を集約する。
> 判定条件やパラメータが変わるたびに更新する。

## PaddleOCR は CPU デフォルト

`app/args.py` の `setup_args()` で `CUDA_VISIBLE_DEVICES=-1` を強制する。
GPU なし環境でも動作する前提のため。

- 変更時: GPU 利用を可能にするなら `args.py` の環境変数設定を外す/条件化し、本書も更新する。

## Low Confidence Flag と OCR 前処理

座標正規化で最上部文字の confidence が 0.8 未満の場合:

- 正規化をスキップし `low_confidence: True` フラグを structured JSON に付与する。
- このフラグがある場合、テンプレート学習をスキップする（`receipt_updater.py` 行 98-108）。
- 低 Confidence 時（または `--preprocess-force` 時）は、前処理 (CLAHE/適応的二値化) で OCR を 1 回再試行する。

詳細（適用条件・モード・パラメータ・出力ファイル・実装上注意）は以下を参照:
- [.hermes/rules/ocr-preprocessing.md](./ocr-preprocessing.md)

## 座標正規化の基準と date anchor

座標正規化は既定で **topmost/leftmost 基準**（全 box の最小 x/y を原点）。
clinic のテンプレートに `date` 座標が学習済みで、同一 clinic/layout の処理時に日付候補が
**信頼できれば**、選択した **date box の左上を基準に全 box を再正規化**する（date 基準）。

主な設計判断:

- date 基準への切替は画像OCR / watcher 経路のみ。`process_input_json`（`--input-json` 単体）は無変更（後方互換）。
- `templates` に座標基準を識別する `coord_basis` 列（`'topmost'` / `'date'`）。基準切替は一度だけ（二重変換防止）。
- anchor 後は構造化 parse を再実行しない。DB `receipts.ocr_json` を `update_receipt_ocr_json_by_source()` で
  date 基準へ更新する（再解析は receipt の二重登録を招くため行わない）。
- 前方テンプレート補正の coord_basis ガード（Issue #40）: `coord_basis=='date'` の template は
  座標ベースのフィールド上書きをスキップ（topmost raw × date template の基底不一致防止）。
- coord_basis 手動切替（Issue #42, [中-1] 対応）: Web 管理画面から topmost → date へ手動切替できる。
  `apply_date_anchor_normalization` は `template_coords` で日付候補 2 件を解決し、`output_dir` 指定時に
  date 基準へ再正規化した raw へ座標ベース抽出を適用して `*-structured_data.json` へ反映する。

詳細（判定条件・閾値・DB 移行・手動切替・前方補正ガード）は以下を参照:
- [.hermes/rules/coordinate-normalization.md](./coordinate-normalization.md)

## ファイル安定性チェック

`watcher.py` / `file_repository.py` の `is_file_stable()`:

- ファイルサイズを 0.5 秒間隔で 3 回チェックする。
- サイズが変動中なら処理をスキップする。
- 書き込み完了を確実に判定する（書き込み中のファイルを読まないため）。
