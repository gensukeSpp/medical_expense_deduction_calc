# Issue #47: date anchor 再正規化・座標抽出の実データ検証ツール

> **ステータス: 実装済み（PR #49。CLI・テスト5件・サンプル手順を含む）。**
> PR #49 レビュー指摘への対応は `tasks/pr_49/plan.md` に沿って実施。
> 特別な実装方式は tasks.md / architecture.md に記録済み（モジュール駆動・アプリ関数直接呼出・
> 作業コピーで非破壊・テストは `git add -f` で追跡）。

## 目的

PR #45 レビュー [中-1] / PR #46 レビュー [中-2] で指摘された「date 基準テンプレートへの切替後に
実際の raw OCR JSON から date anchor を解決し、date 基準への再正規化と座標ベース抽出まで正しく
走るかを、実データでも再現できるようにする」ための**検証用ツール**を追加する。

入力は「date anchor 適用前（topmost 基準）の raw JSON」「対応する structured JSON」「date 基準に
切替済みテンプレートを含む DB」。これらをアプリの**既存ロジック（date 候補検索・候補解決・座標再
正規化・座標ベース抽出）**に通し、各段階の結果（候補一覧・採用候補／解決不能理由・適用オフセット・
再正規化後の座標・抽出結果）を確認できる。検証ツール内部にロジックを複製せず、本番処理フローは
変更しない。

## 参照

- [Issue #47](https://github.com/gensukeSpp/medical_expense_deducation_calc/issues/47)
- PR #45 レビュー記録: `.github/reports/pr-45-review.md` の [中-1]、[中-2]
- PR #46 レビュー記録: `.github/reports/pr-46-review.md` の [中-2]（date 基準 2 候補の layout 解決）
- [Issue #42](../../tasks/issue_42/overview.md) — 手動 coord_basis 切替（検証ツールの前提となる date 基準 template の作り方）
- [Issue #39](../../tasks/issue_39/overview.md) — date 基準正規化の導入
- `.hermes/rules/coordinate-normalization.md`（coord_basis 列・date anchor の設計判断）
- 実装ロジック:
  - `app/date_anchor.py` — `find_date_candidates()` / `resolve_date_anchor()` / `_resolve_date_basis_candidate()` / `apply_date_anchor_normalization()`（PR #46 で date 基準 2 候補解決と `_apply_coordinate_extraction` 追加済み）
  - `app/coord_normalizer.py` — `normalize_coordinates_by_anchor()`
  - `app/coord_search.py` — `search_fields_by_proximity()`
  - `app/db.py` — `get_latest_template_by_clinic()`（`coord_basis` 返却）、clinic 名→id 解決
  - `app/structural_parser.py` — `DEFAULT_PROXIMITY_THRESHOLD`（50.0）

## スコープ

### 含むもの
- `uv run` で実行できる検証 CLI（`uv run python -m app.verify_date_anchor ...`）。
- 入力: `--raw`（topmost 基準 raw JSON）、`--structured`（対応 structured JSON）、`--db`（対象 DB のパス）。
- 前提条件チェック（`coord_basis == 'date'` かつ date box 学習済み かつ template 存在）と、不成立時の理由明示・終了。
- アプリ実装の **date 候補検索 → 候補解決 → 座標再正規化 → 座標ベース抽出** を順に呼び出し、各段階の結果を出力。
- 元の raw JSON / DB / structured を変更しないための**作業用コピー（一時領域）**運用。
- 主要な失敗条件（date 候補が同点で解決不能など）のテスト。サンプル実行手順と結果の読み方の記載。

### 含まないもの
- date anchor の候補解決アルゴリズム自体の改善（Issue #44 や検証結果に応じて別途）。
- 本番処理フロー（`image_processing_service.py` / `receipt_processor.py` / `main.py` の通常経路）の変更。
- `apply_date_anchor_normalization()` 自体の変更（`output_dir` 経路は PR #46 で実装済み。検証ツールは既存関数を再利用するのみ）。
- 実データ・個人情報のリポジトリへのコミット（サンプルは既存 `data/verify-01.db` と `output_json/` の既存ファイルを使用）。

## 対応方針（確定）

1. **作業用コピー**: `--db` と `--raw` を一時ディレクトリ（`tempfile.mkdtemp()`、または `--workdir` 指定）へ
   コピーし、**コピーだけ**を読み書きする。オリジナル DB / raw / structured には一切触れない。
   検証中に `process_input_json()` や `insert_receipt` を呼ばない（レシート重複登録を絶対に発生させない）。
2. **実処理経路の再利用（ロジック非複製）**: 本番の `apply_date_anchor_normalization()` と同じ順序・同じ
   関数を直接呼ぶ:
   - `find_date_candidates(entries, structured_date)` → raw 内の日付候補一覧
   - `resolve_date_anchor(entries, date, template_date_box, template_basis=template["coord_basis"],
     template_coords=coords, proximity_threshold=...)` → 採用 anchor box または None
   - `normalize_coordinates_by_anchor(raw_copy, anchor_box)` → 適用オフセットと再正規化（コピーを上書き）
   - `search_fields_by_proximity(entries_date, coords, threshold=...)` → 座標ベース抽出結果
   検証ツール内に上記の判定ロジックを再実装しない。
3. **前提条件チェック**: 対象 clinic の最新 template を読んで `coord_basis == 'date'` を確認。template 欠落・
   date box 未学習・basis が date でない場合は理由を出力して exit 2。候補解決が同点で None の場合は
   「解決不能（安全側フォールバック、誤った候補は採用しない）」を出力して exit 1。
4. **閾値・比較の再利用**: `proximity_threshold` は既存 `DEFAULT_PROXIMITY_THRESHOLD`（50.0）を既定値とし、
   新規パラメータを作らない。日付比較は既存 `normalization.parse_date()` を内部で使う（`find_date_candidates`）。
5. **出力**: 各段階の結果を標準出力に追跡可能な形で印字し、任意で `--report <path>` で JSON も書く。
   リポジトリ内の既存出力ファイルは書き換えない。

## 相談事項（実装前に確認）

#### Q1: 検証 CLI の配置

- **案A（推奨）**: 新モジュール `app/verify_date_anchor.py`（`argparse` + `main()` + 検証コア関数
  `verify_date_anchor()`）。`uv run python -m app.verify_date_anchor ...` で実行。`app` は名前空間
  パッケージ（`__init__.py` なし）で `-m` 実行可。PaddleOCR 等の重い依存を import せず軽量・単体テスト容易。
- **案B**: `main.py` に `--verify-date-anchor` サブコマンドを追加。`main.py` は既存の「CLI エントリ
  ポイント」という役割に合うが、`main()` が無条件に PaddleOCR import と `setup_directories`（ディレクトリ
  作成）を行うため、検証だけの実行に過剰な副作用が伴う。

#### Q2: 実処理経路の駆動方法（候補一覧・採用理由の出力粒度）

- **案A（推奨）**: 上記 4 つのアプリ関数を**順に直接呼ぶ**（`apply_date_anchor_normalization` 内部の順序と
  同一になるよう注記）。候補一覧・採用候補・解決不能理由を**段階ごと**に出力できる（Issue の「候補一覧、
  選択された候補または解決不能理由、適用オフセット、再正規化後の座標、抽出結果」を満たす）。
- **案B**: 高レベル `apply_date_anchor_normalization(raw_copy, structured, db_copy, output_dir=...)` を
  呼んで一括実行し、後からファイルを読み直して報告を復元。本番のオーケストレーションを完全に踏むが、
  候補一覧・解決理由は関数が返さないため、報告用に結局 primitives を再計算する必要がある（案A と同様に
  なる）。

#### Q3: テストの配置

- **案A（推奨）**: 新規 `tests/test_verify_date_anchor.py` を作成し `git add -f` で追跡（`.gitignore` の
  素の `test_*.py` に一致するため -f 必須。本リポジトリの既知・期待ワークフロー）。
- **案B**: 追跡済み `tests/test_date_anchor.py` に追記（`-f` 不要）。ただしツールの単体テストは date anchor
  本体と目的が異なり、ファイル肥大化が見込まれる。

> **確定済み（ユーザー確認 2026-10-30）: Q1=案A, Q2=案A, Q3=案A。**
> 本計画（overview / architecture / tasks / test_plan）はこの前提で記載済み。

## 受入条件（Issue #47 対応）

1. `uv run python -m app.verify_date_anchor ...` で実行できる検証コマンドが用意されている。
2. 入力として raw JSON・structured JSON・対象テンプレートを含む DB を指定できる。
3. 対象 template の `coord_basis == 'date'` などの前提条件を確認し、不成立時は理由を明示して終了する。
4. アプリ本体の date 候補検索・候補解決・座標再正規化・座標ベース抽出の実装を呼ぶ（ロジック非複製）。
5. 候補一覧・採用候補（または解決不能理由）・適用オフセット・再正規化後の座標・抽出結果を出力する。
6. 元の raw JSON・DB・既存出力を変更しない（作業用コピーを使用し、レシートを重複登録しない）。
7. 成功例に加え、候補が同点で解決不能になるケースなど主要な失敗条件もテストが通る。
8. リポジトリ内サンプルで実処理経路を通すコマンド例と、結果の読み方を記載する。

## 未確定事項（実装時に決めない・現状維持）

- 新規の閾値・比較ロジックは導入しない。`DEFAULT_PROXIMITY_THRESHOLD`（50.0）と既存 `parse_date()` を
  そのまま使う。
- DB スキーマ変更なし（`coord_basis` 列は #39 / #42 で存在済み。migration 不要）。
- 検証ツールは既存 `apply_date_anchor_normalization` / `_resolve_date_basis_candidate` 等の**挙動を変更しない**。

## 使用する技術・制約

- Python `argparse` + `tempfile` + `shutil.copy2`（作業用コピー）。
- 既存関数の再利用:
  - `app.date_anchor.find_date_candidates` / `resolve_date_anchor`
  - `app.coord_normalizer.normalize_coordinates_by_anchor`
  - `app.coord_search.search_fields_by_proximity`
  - `app.db.get_latest_template_by_clinic`（`coord_basis` 返却）
  - `app.structural_parser.DEFAULT_PROXIMITY_THRESHOLD`
- clinic 名→id は DB **読み取り**で解決（`get_or_create_clinic` は INSERT するため使わない。作業コピー上なら
  使っても実害はないが、読み取り専用で十分）。
- テストは `uv run pytest`（既知の既存失敗のみ許容 — 下記 test_plan.md 参照）。