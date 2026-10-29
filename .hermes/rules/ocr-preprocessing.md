# OCR 前処理 (Preprocessing) と出力ファイル命名

> AGENTS.md から切り出した doc。Issue #36 (抽出前の出力向上のための前処理) で追加。
> 前処理の挙動・パラメータ・生成ファイルが変わるたびに更新する。
> 設計判断の概観は [design-decisions.md](./design-decisions.md) を参照。

## 概要

リサイズ → グレースケール → OCR の過程で、読み取り精度を上げるための**事前処理**を追加した。
`--preprocess-mode` で CLAHE (局所コントラスト強調) と適応的二値化 (Adaptive Threshold) を
組み合わせて適用できる。

## 適用条件

前処理は以下の **両方** が成り立つときのみ再試行（OCR を 1 回やり直す）を実行する:

1. `--preprocess-mode` が `none` 以外
2. 以下のいずれか:
   - **低 Confidence**: topmost 文字の Confidence < 0.8（`coord_normalizer.get_topmost_confidence()`）
   - **強制**: `--preprocess-force` 指定時（Confidence に関係なく、目視検証用）

前処理は「低 Confidence 時のみ」が基本。`--preprocess-force` は高 Confidence でも
検証したい場合の手動スイッチ（例: 起点文字は高 Confidence だが読まれていないケース）。

## 前処理モード (`--preprocess-mode`)

| モード           | 内容                                   | 実装 (`app/image_preprocessing.py`)       |
| ---------------- | -------------------------------------- | ---------------------------------------- |
| `none`           | 前処理しない（デフォルト）             | `build_preprocess_fn()` → None            |
| `clahe`          | CLAHE のみ                              | `apply_clahe()`                           |
| `adaptive`       | 適応的二値化のみ                        | `apply_adaptive_threshold()`              |
| `clahe+adaptive` | CLAHE → 適応的二値化（推奨）           | `combined`（2つを連結）                  |

### パラメータ（現状デフォルト）

```python
# CLAHE
cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

# 適応的二値化
cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
```

- パラメータは `app/image_preprocessing.py` の関数引数で調整可能（現状 CLI では固定、コード変更で調整）。
- 検証結果によって最適値を調整する。

## 前処理フロー（ImageProcessingService.process）

```
1回目OCR (前処理なし) -> raw_data.json
  -> topmost Confidence 判定
  -> 条件成立時: CLAHE/二値化 -> 前処理済み画像を processed/ に保存
       -> 前処理済み画像で OCR 再実行 (1回) -> raw_data.preprocessed.json を別名保存
  -> 座標正規化・構造化パースは「前処理済み raw」を対象に実行
```

## 出力ファイル命名

| ファイル                                   | 内容                       |
| ------------------------------------------ | -------------------------- |
| `{stem}_{mtime}-raw_data.json`             | 前処理なしの OCR 結果（原本・温存） |
| `{stem}_{mtime}-raw_data.preprocessed.json`| 前処理後・再OCR の結果（別名保存） |
| `{stem}_{mtime}-structured_data.json`      | 構造化データ               |
| `processed/preprocessed_{画像名}`          | 前処理後画像（目視検証用） |

※ 前処理なし/ありの 2 ファイルを両方残すことで、目視・JSON 比較による効果検証ができる。
※ 前処理済み raw が存在する場合、後段（正規化・パース）はそちらを対象にする。

## 実装上の注意

- **前処理は単チャネル (グレー) が必須**。`ocr_pipeline.process_image` は
  resized 画像を BGR 3ch で読み込み、前処理関数へは `cvtColor(... BGR2GRAY)` で渡し、
  前処理後は `cvtColor(... GRAY2BGR)` で 3ch に戻して OCR に渡す（PaddleOCR は 3ch を要求）。
- 画像は 1 枚につき **再試行は 1 回のみ**。
- `--preprocess-force` は `--preprocess-mode=none` のときは無効（前処理自体が走らないため）。

## 検証結果の記録場所

- 手動検証の結果は `tasks/issue-36/tasks.md` の「実装メモ」表に記録。
- 例: `clahe+adaptive` は小さい/細字の文字を潰す傾向があるため、`clahe` 単体や
  二値化パラメータ調整を比較して判断する。
