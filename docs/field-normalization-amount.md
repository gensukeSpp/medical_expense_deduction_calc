# フィールド正規化まとめ: amount (支払い金額)

> `template_history` 参照判断基準 (date anchor 後の設計方針・案B) の参考として、
> amount フィールドの正規化が現ソースのどこでどのように行われるかを項目でまとめたもの。
> 2026-11-24 時点の実装 (app/ 配下) に基づく。実測結果は `uv run python` で確認済み。

## 結論 (先に)

- name と異なり、**専用の正規化関数 `parse_amount()` が存在する** (app/normalization.py)。
  抽出・更新・一覧表示の各経路がこの関数を再利用する。
- 最終的な structured データの amount は **int (JPY)** になる。不正・空・負値は
  `None` (parse_amount) または `0` (一覧表示 `_to_amount`) に丸められる。

## 1. 正規化関数 — `app/normalization.py::parse_amount(text) -> Optional[int]`

文字列 → int (JPY)。失敗時は `None`。

### 処理順

1. **負値ガード**: 先頭 (または数字以外の直後) に `-` / `−` + 数字があれば `None`。
   ※ 郵便番号 (123-4567) や電話番号 (03-1234) の**桁間ハイフンは負値とみなさない**
   (PR #55 で全ハイフン拒否の regression を修正。ただし下記 4 の最終手段により
   `123-4567` 自体は `None` になる)。
2. **全角→半角**: `０-９`→`0-9`、`，`/`、`→`,`、`．`→`.`。
3. 形式別パース (上から順に最初にマッチしたものを採用):
   - `([0-9,]+)\s*円` — 例: `3,800円` → 3800、`税込 3800円` → 3800
   - `([0-9]+)万\s*(?:([0-9]+)(千)?)?` — 例: `1万2千` → 12000、`1万` → 10000
   - 漢数字 + 円: `([一二三四五六七八九十百千万]+)\s*円` — 例: `一万二千円` → 12000
   - プレーン数字: `3,800` → 3800、`12345` → 12345 (カンマ除去後 `isdigit()`)
4. いずれにもマッチしなければ `None`。

### 実測結果一覧 (エッジケース)

| 入力 | 結果 | 備考 |
|---|---|---|
| `3,800円` | 3800 | 基本形 |
| `４，５００円` | 4500 | 全角対応 |
| `一万二千円` / `二千円` | 12000 / 2000 | 漢数字+円 |
| `3800` / `3,800` / `12345` / `0` | 3800 / 3800 / 12345 / 0 | プレーン数字 |
| `1万2千` / `1万` | 12000 / 10000 | 万円形式 |
| `10,000円` / `1,000,000円` | 10000 / 1000000 | 桁区切り |
| `3800円abc` | 3800 | 数字マッチを許容 |
| `3800円と500円` | 3800 | 複数金額は**最初の数字のみ** |
| `合計 1,200円 うち消費税等 100円` | 1200 | 同上 |
| `（税込）3,800円` | 3800 | 前後に他の文字があっても可 |
| `3800 円` | 3800 | 数字と円の間に空白可 |
| `''` / `'   '` | None | 空文字列 |
| `abc` | None | 非数字 |
| `-3800` / `−3800` | None | 負値は無効 |
| `03-1234-5678` / `123-4567` | None | ハイフン入りはプレーン数字判定で None |
| `一万二千` (円なし) / `三千` | None | 漢数字は**円 必須** |
| `一万` | None | 同上 (漢数字 万 のみは非対応) |
| `1千万` | None | `([0-9]+)万` の形式に合わない |
| `1.5万` | 50000 | **注意**: `([0-9]+)万` が `5万` にマッチ。小数万円は誤パース |
| `¥3800` | None | 通貨記号 `¥` 非対応 (円 必須) |
| `3800.5` / `3.5` | None | 小数非対応 |
| `+3800` | None | `+` 符号非対応 |
| `000123` | 123 | 先頭ゼロは int 化で落ちる |

## 2. 比較用 — `app/normalization.py::normalize_text()`

name と共通。全角英数→半角 → lowercase → `[^\w]` 除去。
amount では `3,800` → `3800` (カンマ・空白が落ちる) になり、**金額の一致判定に使える**。
なお `円` は `\w` に含まれないため `3800円` → `3800円` のまま (カンマだけ除去)。

## 3. 抽出時 — `app/llm_extractor.py`

- `extract_amount_from_text(text)` = `parse_amount(text)` そのまま (llm_extractor.py:55-58)。
- `MockLLMClient.extract_fields()` は全文連結テキストに対して `parse_amount` を呼ぶ。
- `app/normalization.py::normalize_extracted()`:
  - extracted["amount"] が str → `parse_amount` で int 化。
  - `None` なら OCR の text_lines / words から `円` または `万` を含む行を探し、
    最初に `parse_amount` が成功した値を採用。
- **name と異なり 様 除去・空白整形は不要** (数字パースなので)。

## 4. 前方補正・座標近傍抽出 — `app/coord_search.py` / `app/structural_parser.py`

- `search_fields_by_proximity()`: テンプレート座標の近傍から text を取り、
  `extracted[field_name]` を**上書き**する。amount は single-box 扱いで
  **生テキスト (例: `3,800円`) のまま**上書きされる。
- その後 `normalize_extracted()` が **`parse_amount` を再適用**して int 化する
  (structural_parser.py の process 順: extract → normalize)。
- 実測: `extracted={'amount': '3,800円'}` → `normalize_extracted()` → `3800`。
- ※ date anchor 経路 (`apply_date_basis_force` / `_apply_coordinate_extraction`) は
  **`parse_amount` を挟まず** structured へ生テキストで入る (date_anchor.py)。
  この経路では amount が str のまま残り得るため、一覧表示側は `_to_amount` で吸収する。

## 5. 更新時 — `app/services/receipt_normalizer.py::ReceiptNormalizer`

- `normalize_amount(text)` = `parse_amount(text)` (None/空は None のまま返す)。
- `normalize()` は `normalize_extracted(updated_data, old_data)` を呼ぶ (amount/date のみ正規化)。

## 6. 一覧表示・補填額 — `app/services/receipt_list_service.py::_to_amount(value) -> int`

月別一覧 (Issue #54) で amount / compensation を int に丸める:

| 値 | 結果 |
|---|---|
| `None` / `''` / 空白のみ | 0 |
| bool | 0 |
| int / float | `max(0, int(value))` (負は 0) |
| str が `-` で始まる | 0 |
| `parse_amount` が None | 0 |
| それ以外 | `max(0, parsed)` |

→ **一覧計算では不正値は 0 として扱う** (行単位の控除計算
`compute_contribution = max(0, amount - compensation)` は app/calc.py)。

## まとめ — template_history 判断基準への示唆

- amount は `parse_amount()` で**一元的に int 化**されるため、name のような表記揺れ
  (空白・全角) の問題は**ほぼ無い**。前値比較は int 同士の等値比較で足りる。
- ただし注意点:
  1. **date anchor 経路は生テキストのまま structured に入り得る** (str vs int 混在)。
     比較時は `_to_amount` 相当か `parse_amount` を通してから比較するのが安全。
  2. `1.5万` の誤パース (50000) や `一万` (円なし) → None など、**parse_amount の
     解釈境界**を知った上で比較基準を決める必要がある。
  3. 負値・`¥` 記号は None/0 になる。補填額 (compensation) も同じ `_to_amount` 経路。
