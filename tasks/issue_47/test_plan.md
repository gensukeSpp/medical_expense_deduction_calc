# Issue #47 テスト計画: date anchor 実データ検証ツール

## スコープ

検証ツール `app/verify_date_anchor.py` の単体テストと、リポジトリ内サンプルによる手動実データ検証。
本番ロジック（`date_anchor.py` 等）の挙動は PR #46 でテスト済みのため変更しない。

## テストケース一覧

### 成功系

| id | ケース | 期待結果 | 対応受入条件 |
|----|--------|----------|--------------|
| S1 | date 基準 template + 正しい raw で、候補検索→解決→再正規化→抽出が走る | `precondition.ok=True`、`candidates>=1`、`resolution.ok=True`、`renormalization.ok=True`（offset は int/float）、`extraction` に name/amount 等が入る | 1,2,4,5,7 |
| S2 | 候補が複数の場合の layout 解決 | `resolution.anchor_box` が 1 件に確定（同点以外） | 3,5 |
| S3 | `--report` で JSON 出力される | 各段階結果が JSON に記録され、stdout にも表示される | 5,8 |
| S4 | 元の raw / DB / structured が変更されない | `verify_date_anchor` 実行前後で入力ファイルのバイト列が同一。DB にレシートが増えない | 6 |
| S5 | リポジトリ内サンプルで実データ検証（なの花薬局 + verify-01.db）| exit 0、各段階が出力される | 8 |

### 失敗系

| id | ケース | 期待結果 | 対応受入条件 |
|----|--------|----------|--------------|
| F1 | clinic に対象 template が無い | `precondition.ok=False`、理由に「見つかりません」、exit 2 | 3,7 |
| F2 | template の `coord_basis` が date でない | `precondition.ok=False`、理由に「topmost」等、exit 2。誤って date 扱いしない | 3,7 |
| F3 | date box 未学習 | `precondition.ok=False`、理由明示 | 7 |
| F4 | date 候補が同点で解決不能 | `resolution.ok=False`、`renormalization` を出力しない、exit 1（安全側フォールバック・誤候補を採用しない） | 3,5,7 |
| F5 | 入力ファイル（raw/structured/db）が存在しない | `FileNotFoundError` → exit 2 | 3,7 |

## テスト実装方針

- `tests/test_verify_date_anchor.py` に新規作成し、`git add -f` で追跡（`.gitignore` の素 `test_*.py` に
  一致するため。本リポジトリの既知ワークフロー）。
- ツール自体は「アプリの既存関数を順に呼ぶだけ」の薄いレイヤのため、テストは主に:
  - 集約結果の整合（`report` dict の各 key）
  - **非破壊**（raw / db のバイト列が不変、DB コピーを介して書いてもオリジナル不変）
  - 前提判定の分岐（basis / template 欠落 / date 未学習）
- テスト用の DB は `app.verify_date_anchor._seed_date_basis_template` 相当で直接作成し、date 基準 template を
  seed。`coord_basis='date'` にしておく（`_seed_date_basis_template` は overview.md / tasks.md の想定を踏襲）。
- 候補 box と template coords は **coherent な geometry** で組む（date-basis の template box オフセットを
  raw の anchor top-left に足して配置）。バラバラの座標だと「raw は再正規化できたのに抽出が反映されない」という
  偽失敗になるため（参照 `references/date-basis-manual-toggle-post-failure.md`）。

## 実行コマンド

```bash
# 新規テストのみ
uv run pytest tests/test_verify_date_anchor.py -v

# 全テスト（既知の既存失敗のみ許容）
uv run pytest
```

## 既知の既存失敗（本ツール起因ではない・修正しない）

- `tests/test_image_processing_service.py::TestRunOcr::test_calls_process_image_and_logs`
  （INFO ログが caplog に捕まらない assertion）
- `tests/test_image_processing_service.py::TestProcess::test_happy_path`
  （`_parse_structured` の mock アサーションの位置引数/キーワード引数の不一致）

これらは `origin/main` でも同様に失敗する事前失敗。本 Issue の範囲では「修正しない」。
新規ツールのテストだけが全て PASS していることを確認する。

## 実データ手動検証（sample run）

```bash
uv run python -m app.verify_date_anchor \
  --raw output_json/IMG_20261028_155506_1793242101-raw_data.json \
  --structured output_json/IMG_20261028_155506_1793242101-structured_data.json \
  --db data/verify-01.db \
  --workdir /tmp/verify-47 --report /tmp/verify-47/report.json
```

結果の読み方:
- `[段階1]` の候補一覧: 構造化 date と一致する raw box の text / box を列挙。
  1 件ならそのまま採用、複数なら date 基準の layout スコアで解決。
- `[段階2]` 採用候補: 実際に anchor として使われた box。`解決不能` なら同点（安全側フォールバック）で
  誤候補を採用していないことを示す。
- `[段階3]` 適用オフセット: anchor の top-left（min-x/min-y）。再正規化後は date box が原点付近に並ぶ。
- `[段階4]` 抽出結果: date 基準 raw に date 基準 template の coords を重ねて `search_fields_by_proximity`
  （閾値 50px）が拾ったフィールド値。これが「date 基準で座標抽出が効いた」ことの直接の証跡。

実行前後の `sha256sum`（raw / db）の一致をもって非破壊を確認する。

## 受入条件との対応

| 受入条件 | 検証手段 |
|----------|----------|
| 1. uv run で実行できる | Task1 Step3 / Task2 Step1 のコマンド |
| 2. 入力（raw/structured/db）指定 | CLI 引数 + S1/S5 |
| 3. 前提不成立は理由明示で終了 | F1/F2/F3/F5（exit 2） |
| 4. アプリ実装を呼ぶ（ロジック非複製） | コードレビュー: `verify_date_anchor` がアプリ関数のみを import/呼出、再実装なし |
| 5. 各段階の結果を出力 | S1/S3、report JSON |
| 6. 元データ・DB・既存出力を不変更 | S4（入カバイト列比較 + レシート非追加）、実データ手動検証の sha256 |
| 7. 成功 + 主要失敗条件のテスト | S1-S5 / F1-F5 |
| 8. サンプル手順と結果の読み方 | 本ファイル + overview / architecture / tasks のサンプル節 |