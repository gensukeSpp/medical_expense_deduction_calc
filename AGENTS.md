# medical-exp-deduction-calc

医療費控除用領収書 OCR アプリ。確定申告のためのデータ入力簡略化が目的。
Phase 1（読み込み、抽出、修正、キャッシュ）のみを実装。計算（Phase 2）は未着手。

---

## ディレクトリ構成

```
.
├── main.py                    # CLI エントリポイント (argparse)
├── pyproject.toml             # uv + 依存定義 (black)
├── uv.lock
│
├── app/                       # 本体コード
│   ├── args.py                # CLI 引数定義・ディレクトリ初期化
│   ├── db.py                  # SQLite ヘルパー (全テーブル CRUD)
│   ├── db_migrations.py       # スキーママイグレーション
│   ├── schema.sql             # 最終的な SQL スキーマ (source of truth)
│   ├── coord_normalizer.py    # 座標正規化 (offset 除去) + topmost Confidence 判定
│   ├── date_anchor.py         # date anchor 照合・解決・適用 [Issue #39]
│   ├── coord_search.py        # 座標ベースのフィールド検索
│   ├── image_preprocessing.py # OCR 前処理 (CLAHE / 適応的二値化) [Issue #36]
│   ├── image_resize.py        # OCR 向け画像リサイズ
│   ├── input.py               # JSON 読み込みヘルパー
│   ├── llm_extractor.py       # LLM クライアント抽象 + Mock 実装
│   ├── normalization.py       # 金額/日付の正規化
│   ├── ocr_pipeline.py        # PaddleOCR エンジン (resize -> preprocess -> predict -> normalize)
│   ├── output.py              # Atomic JSON 書き出し
│   ├── processor.py           # 単一画像処理 (CLI layer)
│   ├── prompts.py             # LLM プロンプト定義
│   ├── structural_parser.py   # 構造化パーサー (ExtractionService, ReceiptProcessingService)
│   ├── template_feedback.py   # クリニックテンプレート学習 (フィードバックループ)
│   ├── watcher.py             # フォルダ監視 (polling / watchdog)
│   ├── error_logging.py       # エラーログ出力
│   │
│   ├── services/              # 分離されたサービス層 (SRP)
│   │   ├── image_processing_service.py  # 画像処理オーケストレーター
│   │   ├── receipt_processor.py          # ビジネスロジック (OCR -> 解析)
│   │   ├── file_repository.py            # ファイルシステム操作
│   │   │
│   │   ├── receipt_service.py            # Web UI 用receipt操作用オーケストレーター
│   │   ├── receipt_file_repository.py    # receipt ファイル I/O
│   │   ├── receipt_database_repository.py # receipt DB操作
│   │   ├── receipt_normalizer.py         # receipt データ正規化
│   │   ├── ocr_coordinate_service.py     # 座標検索・フィードバック
│   │   ├── receipt_updater.py            # receipt 更新オーケストレーター
│   │   ├── template_admin_service.py     # テンプレート管理・coord_basis 手動切替 [Issue #42]
│   │
│   └── web/                   # Web UI (FastAPI + Jinja2)
│       ├── server.py          # FastAPI アプリ作成
│       └── templates/         # HTML テンプレート
│
├── docs/
│   ├── DEVELOPMENT.md         # 開発環境セットアップ
│   ├── schema.sql             # DB スキーマ
│   └── architecture/          #  dated architecture snapshots
│
├── tests/                     # pytest テスト
│   ├── conftest.py
│   ├── test_*.py
│
├── output_json/               # 処理済み JSON 出力先 (gitignore)
├── processed/                 # 処理済み画像 (gitignore)
├── failed/                    # 失敗画像 (gitignore)
├── data/                      # SQLite データベース (gitignore)
└── logs/                      # ログ (gitignore)
```

## 技術スタック

- **言語**: Python >= 3.11
- **管理**: uv (`uv run python main.py ...`)
- **OCR**: PaddleOCR (`paddleocr[doc-parser]`, lang=japan)
- **LLM**: 構造化 LLM (JSON 整形) + 命名抽出 LLM (クリニック名特定)
  - 現在は `--model=mock` でヒューリスティック実装のみ
- **Web**: FastAPI + uvicorn + Jinja2
- **DB**: SQLite (FK 有効化必須: `PRAGMA foreign_keys = ON`)
- **監視**: watchdog (inotify) / polling fallback
- **テスト**: pytest
- **整形**: black (line-length=119)

## DB スキーマ

source of truth: `docs/schema.sql`

| テーブル          | 役割                           |
| ----------------- | ------------------------------ |
| `users`           | ユーザー                       |
| `clinics`         | クリニックマスタ (name UNIQUE) |
| `receipts`        | 領収書レコード                 |
| `templates`       | クリニック別座標テンプレート   |
| `template_history`| テンプレート変更履歴           |
| `corrections`     | ユーザー修正履歴               |

マイグレーション:
```bash
python -m app.db_migrations init --db-path data/db.sqlite3
```

## 主要アーキテクチャパターン

### 1. 「座標逆引き」モデル (requirements 5 参照)

領収書解析の根幹となるロジック:

```
画像投入 -> OCR(テキスト+座標) -> クリニック名特定 ->
DBから過去テンプレート取得 -> 座標補正値適用 ->
ラベル・値リスト化 -> UI提示 -> ユーザー修正 ->
修正値をDBに学習(座標オフセット更新)
```

### 2. Hybrid Template Key Matching (3-stage fallback)

`app/structural_parser.py` の `ExtractionService._apply_template_corrections()`:

1. **Exact Name Match** - クリニック名で完全一致
2. **Text Similarity** - `difflib` (閾値 0.6)
3. **Layout-based Matching** - 50px 近接 + 60% フィールド一致

### 3. 2 つの processing パス

**パス A: 画像 -> 構造データ (CLI / Watcher)**
```
main.py -> processor.py -> ImageProcessingService
  -> ocr_pipeline.process_image()   # resize -> (前処理) -> predict
  -> coord_normalizer.normalize_coordinates()
  -> structural_parser.process_input_json()
    -> ExtractionService.extract()
    -> DataNormalizationService.normalize()
    -> ReceiptRepository.save()
    -> OutputWriter.write()
```

- 前処理の詳細（適用条件・モード・出力ファイル）は `.hermes/rules/ocr-preprocessing.md` を参照。

**パス B: JSON -> 構造データ (既存 OCR JSON 処理)**
```
main.py -> structural_parser.process_input_json()
  -> 同上 ExtractionService / Normalizer / Repository / Writer
```

### 4. Service Layer (SRP)

`app/services/` は Single Responsibility Principle に基づく分離:

- `ReceiptService` - Web UI 操作用オーケストレーター
- `ReceiptUpdater` - 更新フローの調整役
- `ReceiptFileRepository` / `ReceiptDatabaseRepository` - 分離されたリポジトリ
- `ReceiptNormalizer` - データ正規化
- `OCRCoordinateService` - 座標検索・フィードバック

## CLI 使い方

使い方と引数一覧は変更頻度が高いため、`.hermes/rules/cli.md` に分離している。詳細は以下を参照:

- [.hermes/rules/cli.md](./.hermes/rules/cli.md) — コマンド例・引数一覧・デフォルト値

主なコマンド例:

```bash
# 単一画像処理
uv run python main.py --input-dir ~/Downloads/receipts --image-name IMG_001.jpg

# 既存 OCR JSON から構造データ生成
uv run python main.py --input-json output_json/IMG-raw_data.json --db-path data/db.sqlite3

# フォルダ監視 (polling / watchdog)
uv run python main.py --watch --input-dir ~/Downloads/receipts --poll-interval 10
uv run python main.py --watch --use-watchdog --input-dir ~/Downloads/receipts

# 単一画像処理 + 前処理(低Confidence時に自動適用)
uv run python main.py --input-dir ~/Downloads/receipts --image-name IMG_001.jpg \
    --preprocess-mode clahe+adaptive

# Web UI 起動
uv run python main.py --serve --port 8000 --db-path data/db.sqlite3
```

※ Issue #36 の前処理オプション `--preprocess-mode` / `--preprocess-force` / `--target-short-side` の詳細は `.hermes/rules/ocr-preprocessing.md` と `.hermes/rules/cli.md` を参照。

## 開発規約

### コーディング規約

- `black` で整形: `black .` (pyproject.toml 設定に従う)
- 型注釈を付ける (Python 3.11 型ヒント: `X | Y` 構法)
- docstring は Google style (クラス/メソッド毎)
- 各モジュールに SRP を明示する docstring を付ける
- `from __future__ import annotations` をモジュール冒頭に付ける

### モジュール設計ルール

1. **CLI layer (`processor.py`, `args.py`)** - 引数検証、sys.exit、エラー表示のみ。ビジネスロジックを含まない
2. **Service layer (`services/`)** - 各クラスが単一責任を持つ。オーケストレーターはコンポーネントを調整するのみ
3. **Repository pattern** - データアクセスはリポジトリに閉じ込める
4. **Dependency Injection** - サービス間の結合はプロトコル/インターフェース経由

### テスト

```bash
# 全テスト
pytest

# 単一ファイル
pytest tests/test_db.py -v

# フィクスチャ利用 (conftest.py 参照)
pytest tests/test_feedback.py -v
```

テストファイルは `test_*.py` として `tests/` 配下に配置。
`pytest` 実行時は `PYTHONPATH=.` を有効化 (pyproject.toml で自動設定される場合あり)。

### ブランチ命名規則

```
feature/<issue-number>-<short-description>
fix/<issue-number>-<short-description>
docs/<issue-number>-<short-description>
refactor/<issue-number>-<short-description>
```

例: `feature/34-hybrid-template`, `fix/260716-layout-matching`

コミットメッセージ:
```
feat #<issue>: <description>
fix #<issue>: <description>
docs #<issue>: <description>
refactor #<issue>: <description>
```

### git workflow

- `main` が安定ブランチ
- 開発は `hermes-setting` など feature ブランチから PR 経由で merge
- 直接 main への push は避ける

## 重要な設計判断

### PaddleOCR は CPU デフォルト

`app/args.py` の `setup_args()` で `CUDA_VISIBLE_DEVICES=-1` を強制。
GPU なし環境でも動作する前提。

### Low Confidence Flag と OCR 前処理

座標正規化で最上部文字の confidence が 0.8 未満の場合:
- 正規化をスキップし `low_confidence: True` フラグを structured JSON に付与
- このフラグがある場合、テンプレート学習をスキップ (`receipt_updater.py` 行 98-108)
- 低 Confidence 時（または `--preprocess-force` 時）は、前処理 (CLAHE/適応的二値化) で OCR を 1 回再試行する

前処理の詳細（適用条件・モード・パラメータ・実装上注意）は変更頻度が高いため、
`.hermes/rules/ocr-preprocessing.md` に分離している。詳しくは以下を参照:
- [.hermes/rules/ocr-preprocessing.md](./.hermes/rules/ocr-preprocessing.md)

### 座標正規化の基準と date anchor

座標正規化は既定で **topmost/leftmost 基準**（全 box の最小 x/y を原点）。
clinic のテンプレートに `date` 座標が学習済みで、同一 clinic/layout の処理時に日付候補が
**信頼できれば**、選択した **date box の左上を基準に全 box を再正規化**する（date 基準）。

- date 基準への切替は画像OCR / watcher 経路のみ。`process_input_json`（`--input-json` 単体）は無変更（後方互換）。
- `templates` に座標基準を識別する `coord_basis` 列（`'topmost'` / `'date'`）を持つ。基準切替は一度だけ（二重変換防止）。
- anchor 後は構造化 parse を再実行せず、DB `receipts.ocr_json` を `update_receipt_ocr_json_by_source()` で
  date 基準へ更新する（再解析は receipt の二重登録を招くため行わない）。

**前方テンプレート補正の coord_basis ガード（Issue #40）**: 前方補正
（`ExtractionService._apply_template_corrections()`）は、受領時点の raw が topmost 基準であるのに
`coord_basis=='date'` の template 座標を照合する基底不一致があった。移行済み clinic
（`coord_basis=='date'`）では、座標ベースのフィールド上書き（`search_fields_by_proximity` による引き直し）
を**スキップ**して誤上書きを防ぐ。clinic 名の正しい名への上書きと新規 clinic 作成は
coord_basis に関係なく従来どおり実行する。未移行（`'topmost'` または旧データ）は従来動作。

**coord_basis の手動切替（Issue #42）**: 自動遷移が実データで効かない場合（`resolve_date_anchor()` が
同日付候補 2 件の曖昧で None）、Web 管理画面（`GET /admin/templates` /
`POST /admin/templates/{clinic_id}/basis`）から `coord_basis` を topmost → date へ**手動で一方向切替**できる。
シフト計算は自動遷移と共通の `coord_normalizer.shift_template_to_date_basis()` を使い、date box **学習済み**
template のみ切替許可（未学習は 409）。移行は `db.update_template_basis()`（`history_reason='manual_basis_toggle'`）を再利用し、
既に date なら no-op（冪等）。

**[中-1] 対応（date 基準補正を実際に走らせる）**: 手動切替で template を date 化しても raw は topmost のまま
だったため、対象 clinic（同日付候補 2 件）では date 基準の座標抽出が走らず「topmost では抽出できないデータを
抽出できた」という PR #45 の主張は誤りだった。修正後は `apply_date_anchor_normalization()` が:
- `resolve_date_anchor()` に `template_coords` を渡し、date 基準 template でも候補 2 件をレイアウト整合で解決する。
- 呼び出し側が `output_dir` を渡すと、date 基準へ再正規化した raw に対し座標ベース抽出を適用し
  `*-structured_data.json` へ反映する（レシートは二重登録しない）。
詳細は後述 rules を参照。

座標正規化・date anchor の判定条件・閾値・DB 移行・前方補正の coord_basis ガード詳細は変更頻度が高いため、
`.hermes/rules/coordinate-normalization.md` に分離している。詳しくは以下を参照:
- [.hermes/rules/coordinate-normalization.md](./.hermes/rules/coordinate-normalization.md)

## 出力ファイル命名

変更頻度が高く、Issue #36 で前処理済みファイルが追加されたため、
命名規則は `.hermes/rules/ocr-preprocessing.md` に分離している。

要点:
- raw JSON: `{image_stem}_{mtime}-raw_data.json`
- 前処理済み raw JSON: `{image_stem}_{mtime}-raw_data.preprocessed.json`
- structured JSON: `{image_stem}_{mtime}-structured_data.json`
- 前処理済み画像: `processed/preprocessed_{画像名}`

### ファイル安定性チェック

`watcher.py` / `file_repository.py` の `is_file_stable()`:
- ファイルサイズを 0.5秒間隔で 3 回チェック
- サイズが変動中なら処理をスキップ
- 書き込み完了を確実に判定

## 関連ファイル

- 要件定義書: `要件定義書.md`
- 開発環境: `docs/DEVELOPMENT.md`
- スキーマ: `docs/schema.sql`
- アーキテクチャスナップショット: `docs/architecture/` 配下
- 変更頻度の高いルール (`rules`):
  - CLI 使い方・引数一覧: `.hermes/rules/cli.md`
  - OCR 前処理・出力ファイル命名: `.hermes/rules/ocr-preprocessing.md`
  - 座標正規化・date anchor・DB coord_basis: `.hermes/rules/coordinate-normalization.md`

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**This project has a knowledge graph. Start with the code-review-graph
MCP tools to narrow scope, then read the source.** The graph is cheaper than scanning files and
gives you structural context (callers, dependents, test coverage) that file search cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

### Verify in the source

- Narrow scope with the graph, then read the source. Do not change code from graph output alone.
- For any non-trivial change, read the implementation and the relevant tests before concluding.
- Verify the exact source when touching behavior, database logic, migrations, retries, fallbacks,
  recovery, or compatibility code.
- When the graph and the source disagree, the source wins. The graph may be stale or may not
  model that relationship.
- An empty graph result can mean "not indexed" or "not statically visible", not "does not exist".

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. Rebuild the graph after source changes: `graph build` (full) once to initialize,
   then `graph build`/`update` (incremental) after each change. (No git hooks are
   installed in this repo; builds are manual.)
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
<!-- /code-review-graph MCP tools -->

<!-- better-code-review-graph MCP tools -->
## MCP Tools: better-code-review-graph

**This is the successor MCP server for the same knowledge graph.** ただし実 DB は
旧 `code-review-graph` と**共有ではなく別々** (実測 2026-10-26: better-CRG の CLI
`graph build` / `graph stats` が読み書きするのは `<repo>/.crg/graph.db` で、
旧ツールの `.code-review-graph/graph.db` は別物、`.better-code-review-graph/graph.db`
は better-CRG が参照しない残骸)。
集約 API (`config` / `graph` / `query` / `review` / `security`) を後継とする。
新規の操作では **better 版を優先**し、旧 `*_tool` 名はレガシーとして扱う。
※ セキュリティスキャンは `security` ツールで実施可能 (OWASP 系 sink 検出。
  現時点では未実施、作業候補として扱う)。

### When to use better-code-review-graph FIRST

- **Code review**: `review(action="context")` で変更 diff の影響範囲・ソース断片・
  レビュー指針を一度に生成 (旧 `detect_changes_tool` + `get_review_context_tool` に相当)
- **Refactor audit**: `review(action="delta", show_line_shifts=true)` で関数の行移動を
  検出し、純粋リファクタコミットの呼び出し箇所を洗い出す
- **Code relationship**: `query(pattern=callers_of / callees_of / imports_of / tests_for)`
- **Semantic / keyword search**: `query(action="search")` (embedding はローカル Qwen3 運用)
- **Blast radius**: `query(action="impact")` で変更ファイルの依存 BFS を実行
- **Decomposition audit**: `query(action="large_functions")` で長大関数・ファイルを検出
- **Security scanning** (作業候補): `security(action="scan")` で SQL 注入 / シェル注入 /
  パストラバーサル / eval 注入 / ハードコードシークレットを検出。結果は
  `nodes.security_tags` に永続化され、`report(format="sarif")` で GitHub 連携も可

### Key Tools

| Tool | Action | Use when |
| ------ | ---------- | ------ |
| `review` | `context` | 変更の影響範囲 + ソース断片 + レビュー指針を一度に得る |
| `review` | `delta` | 2 コミット間の add/remove/modify と関数行移動 (`show_line_shifts=true`) を監査 |
| `query` | `query` | callers_of / callees_of / imports_of / tests_for 等で関係を追跡 |
| `query` | `search` | 名前・キーワード・セマンティック検索 |
| `query` | `impact` | 変更ファイルの blast radius 分析 |
| `graph` | `build` / `update` / `embed` / `stats` | グラフ構築・更新・embedding・状態確認 |
| `security` | `scan` / `report` | セキュリティスキャン (現時点は未実施・作業候補) |

### Workflow

1. Code review は `review(action="context", base="origin/main")` でスコープを絞る
   (include_source=false でトークン節約可)。
2. 影響範囲を `query(action="impact")` で確認する。
3. テスト網羅は `query(pattern="tests_for", target=<func>)` で確認する。
4. 純粋リファクタ (ロジック不変) の監査は `review(action="delta", show_line_shifts=true)`
   を利用する。

### 出力サイズの抑え方（review / query の呼び出し規約）

実測（2026-10-22~~, PR #45 レビュー時~~）: `review(action="context", base="origin/main")` の既定呼び出しは **約 1.4MB** を返し、読み切れない。原因は `graph.edges`（約 112KB）と、変更ファイル全体の推移的な影響ノード。以下を既定にする。

- **変更ファイルを明示する**: `changed_files` に対象パスを列挙して渡す（自動検出に任せない）。明示指定 + `max_depth=1` + `include_source=false` で約 72KB まで下がった。
- **深さは 1 から始める**: `max_depth` の既定値 2 は影響ファイルを一気に広げる。必要な場合だけ 2 にする。
- **ソース断片は必要な関数だけ読む**: `include_source=true` は `max_lines_per_file` を小さく（例 50）し、詳細は `read_file` で該当行を読む。
- **大きな結果は再取得しない**: 出力は `~/.hermes/.../cache/spillover/*.txt` に保存される。`read_file` の offset/limit でページングするか、`execute_code` で JSON を分解して必要な項目だけ抽出する。同じ呼び出しを繰り返さない。
- **ツール呼び出しはファイル単位で分ける**: 変更が多い PR は `changed_files` をディレクトリ単位（例: `src/components/organisms/` → `src/lib/`）に分割して呼ぶ。

### グラフの鮮度

- グラフは手動ビルド。ソース変更後は `graph(action="update", base="origin/main")` で差分更新する。初期化やスキーマ変更時のみ `graph(action="build", full_rebuild=true)`。
- `graph(action="stats")` の `Last updated` が作業時点より古い場合は更新してから使う。
- レビュー対象が未コミットの作業ツリーなら、base を指定して差分を明示する。
- **better-CRG の実 DB は `<repo>/.crg/graph.db`**（自前の `.gitignore` が `*` を無視するため git 追跡対象外。`graph.build`/`graph stats` が読み書きするのはこのファイルで、`.code-review-graph/` や `.better-code-review-graph/` は参照しない）。CLI での更新・確認:
  ```bash
  # 差分更新（ソース変更後に実行。no git hooks のため手動）
  uvx --python 3.13 better-code-review-graph graph build --repo-root .
  # 全再構築（初回・大規模リファクタ・不整合時のみ）
  uvx --python 3.13 better-code-review-graph graph build --full-rebuild --repo-root .
  # 鮮度確認（Last updated を確認）
  uvx --python 3.13 better-code-review-graph graph stats --repo-root .
  ```
  stats の `Last updated` が作業時点より古い場合、または DB mtime と整合しない場合は更新してから使う。

### 役割の使い分け（レビュー時の順序）

1. 変更範囲の把握: `review(action="context")`（明示 `changed_files` + 小さい深さ）
2. 逆依存の確定: `search_files` で import を grep（TSX は graph が弱い）
3. 単一シンボルの定義・参照: Serena（`find_referencing_symbols`）
4. 結論はソース（`git diff origin/main...HEAD -- <path>` と該当ファイルの読解）で検証する

### Serena との使い分け

**Serena** (`serena` LSP) と知識グラフは**競合せず、役割で使い分ける**。

| 目的 | 使用ツール |
| --- | --- |
| **シンボルの定義元・呼び出し先・型定義を正確に特定** | **Serena** (`serena` LSP)。`find_declaration` / `find_referencing_symbols` / `find_implementations` / `find_symbol` などを使用。LSP が実ファイルから動的解決するため、インデックス不要で常に最新ソースに追従する |
| **影響範囲 (blast radius)・依存構造・テスト網羅の俯瞰** | **better-code-review-graph**。`query(action="impact")` / `query(pattern=callers_of|callees_of|imports_of|tests_for)` などを使用。ただしビルド済みグラフを参照するため、ソース変更後は `graph build` (差分) で最新化が必要 |
| 関数の実装・編集 (リネーム等) | **Serena** (`rename_symbol` / `replace_symbol_body` / `insert_before_symbol` など) |

使い分けの原則:
- **単一シンボルの正確な定義・呼び出し・型の解決は Serena を優先**する。LSP による解決はグラフ未ビルド時や変更直後でも正確。
- **プロジェクト全体の構造・影響範囲・テスト網羅は知識グラフ (`query(action="impact")` etc.) を使う**。
- グラフはビルド時点のスナップショットであり、ソースと食い違う場合がある。**ソースが正**。
<!-- /better-code-review-graph MCP tools -->