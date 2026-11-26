# フィールド正規化まとめ: name (氏名)

> `template_history` 参照判断基準 (date anchor 後の設計方針・案B) の参考として、
> name フィールドの正規化・整形が現ソースのどこでどのように行われるかを項目でまとめたもの。
> 2026-11-24 時点の実装 (app/ 配下) に基づく。実測結果は `uv run python` で確認済み。

## 結論 (先に)

- **専用の name 正規化関数 (`normalize_name` 等) は存在しない**。`normalize_extracted()`
  は amount / date のみを正規化し、name は抽出・補正された値をそのまま通す。
- name の整形は「抽出 (llm_extractor)」「比較 (normalize_text)」「座標近傍抽出 (coord_search)」
  「Web 修正時の座標検索 (ocr_search_strategies)」「前方補正での上書き (structural_parser)」
  の各段階に分散しており、**経路によって「様」除去・空白の扱いが異なる**。

## 1. 抽出時 — `app/llm_extractor.py::extract_name_from_lines()`

優先順に 3 分岐。抽出値は**空白を維持したまま**返す。

| 分岐 | 条件 | 処理 | 例 (実測) |
|---|---|---|---|
| a | 行に「様」を含む | `line.split("様")[0].strip()` — **様の前を全て取る** | `山田 太郎様` → `山田 太郎` |
| b | `患者:` / `患者：` / 患者で始まる | `患者[:：]?\s*(.+)` で残部抽出 → `replace("様","")` → 長さ `1 < len <= 40` で採用 | `患者 山田 太郎` → `山田 太郎` |
| c | フォールバック | 漢字/かな/空白のみの 2〜40 文字行を strip | `佐藤 花子` → `佐藤 花子` |

注意点 (実測):
- **分岐 a が b より先**。`患者：鈴木 一郎様` は a に掛かり `患者：鈴木 一郎` —
  **「患者：」プレフィックスが残る** (b の正規化が効かない)。
- `患者 山田 太郎様` → `患者 山田 太郎` (様 は取れるが患者 が残る)。
- 行の正規化 `normalize_lines()` は NFKC 適用 (全角英数→半角)。姓と名の空白
  (半角・全角とも) はこの段階では**そのまま**。

## 2. 比較用 — `app/normalization.py::normalize_text()`

保存には使わず、**一致判定用**。全角英数→半角 → lowercase → `[^\w]` 除去:

| 入力 | 出力 |
|---|---|
| `山田 太郎` | `山田太郎` (空白除去) |
| `山田　太郎` (全角空白) | `山田太郎` |
| `ＡＢＣクリニック` | `abcクリニック` |
| `山田太郎様` | `山田太郎様` (**様は除去されない**) |

→ 姓と名の空白を無視して比較したい場合は、**正規化側で明示的に様 除去が必要**
  (normalize_text 単体では不十分)。

## 3. 座標近傍抽出時 — `app/coord_search.py::search_fields_by_proximity()`

テンプレート座標から値を引く際の整形:

| box 形式 | 処理 | 例 |
|---|---|---|
| single-box | `search_by_proximity` の text を**そのまま** | `山田 太郎` → `山田 太郎` (様 除去なし) |
| multi-box (姓名分割) | X 座標順に `"".join` で連結 → **末尾のみ `rstrip("様")`** | `山田` + `太郎様` → `山田太郎` |

→ 連結するため**姓と名の間の空白は入らない** (`山田 太郎` にはならない)。
  様 は**末尾のみ**除去 (真ん中の様 は残る)。

## 4. Web 修正時の座標検索 — `app/services/ocr_search_strategies.py::MultiBoxSubstringStrategy`

- 各行 `text.replace("様", "")` — **全箇所の様 を除去** (3 の rstrip と挙動が異なる)。
- `normalize_text()` 適用後、サブストリング一致判定。
- 連結は `"".join(...)` で空白なし。

## 5. 前方補正での上書き — `app/structural_parser.py::_apply_template_corrections()`

`coord_basis != 'date'` のとき 3 の結果で `extracted["name"]` を上書き。
テスト (test_structural_parser.py:343): LLM 抽出 `山田 太郎` → テンプレ補正後 `山田太郎`。

## まとめ — template_history 判断基準への示唆

- **同一人物の name が経路によって表記が揺れる**: LLM 抽出 = 空白あり (`山田 太郎`)、
  multi-box 座標抽出 = 空白なし (`山田太郎`)。テストも両方を許容
  (test_llm_wrapper.py:21,40)。
- 「前の値を採用する」(案B) の比較基準を作るなら、**`normalize_text()` + 様 除去の
  組み合わせ**が必要 (空白・全半角を吸収)。ただし現状 `normalize_text()` は様 を
  落とさないので、ラッパー (例: `normalize_name()` = `normalize_text(text.replace("様",""))`)
  を新設するのが候補。
- 抽出段階の欠陥 (患者プレフィックス残り: 分岐 a 優先) がそのまま DB に入り得るため、
  比較時に suffix/prefix 除去を入れるか、抽出側の分岐順修正が別途論点になり得る。
