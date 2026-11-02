# PR #49 レビュー指摘対応: 実装計画

> 対象: `.github/reports/pr-49-review.md` の指摘事項 [中]×3 + [低]×1
> 対象モジュール: `app/verify_date_anchor.py`（Issue #47 検証 CLI）
> 前提: レビュー指摘はすべて**正しい**。ソース確認済み（下記「現状」参照）。

## Goal

Issue #47 検証 CLI（`app/verify_date_anchor.py`）について、PR #49 レビューで指摘された
4 点を解消する:

1. [中] `--workdir` 未指定時に `tempfile.mkdtemp()` で作成した一時領域（raw/DB コピー含む）が
   プロセス終了後も残り続ける → 正常終了・前提不成立・解決不能・例外のいずれでも確実に削除する。
2. [中] `--workdir` 指定時に固定名 `raw_data.json` / `db.sqlite3` を無警告で上書きする →
   実行ごとの専用サブディレクトリを作り、既存ファイルを破壊しない。
3. [中] 再正規化後の座標（date box・代表フィールド box）が標準出力・JSON レポートに含まれない →
   Issue #47 受入条件 5（「再正規化後の座標」の出力）を満たす。
4. [低] `tasks/issue_47/overview.md` のステータスが「未実装」のまま → 実装済み実態に合わせて更新。

## 現状（ソース確認済みの事実）

`app/verify_date_anchor.py`:

- L90: `tmp = Path(tempfile.mkdtemp(prefix="verify-date-anchor-"))` — 削除処理は**一切ない**。
  `verify_date_anchor()` は前提不成立（L118/121/123/142）、解決不能（L163）、再正規化失敗（L170）
  の各早期 return でも tmp を残す。例外時も同様。
- L95-100: `work / "raw_data.json"`、`work / "db.sqlite3"` に `shutil.copy2()` で上書きコピー。
  `--workdir` 指定時は既存同名ファイルを無警告で破壊する。
- L172: `report["renormalization"]` は offset のみ。L177: `report["extraction"]` は抽出値のみ。
  再正規化後の raw コピーの座標スナップショット（date box / 代表フィールド box）は stdout・JSON
  のどちらにも含まれない。
- `tasks/issue_47/overview.md` L3-4: 「ステータス: 計画確定…未実装」「実装は次回セッションに
  実施予定」と記載。実際は CLI・テスト 4 件・サンプル手順が PR #49 に含まれている。

## 対応方針（確定）

### 変更 1: 一時領域の確実な後始末（指摘 [中] 1）

`verify_date_anchor()` 内の作業領域生成を `tempfile.TemporaryDirectory` ベースに置き換える。

- `--workdir` **未指定**: `tempfile.TemporaryDirectory(prefix="verify-date-anchor-")` を使用し、
  関数を抜ける（正常 return・早期 return・例外のいずれ）ときに必ず削除される。
  実装は `contextlib.ExitStack` + `TemporaryDirectory` で、早期 return が多数ある現構造でも
  確実に後始末されるようにする（各 return を try/finally で囲むより単一 ExitStack が安全）。
- `--workdir` **指定**: ユーザーが「残したい」と明示した場合のみ、指定ディレクトリをそのまま使い
  削除しない（現行のヘルプ文言「残したい場合」と整合）。
- `main()` 側の変更は不要（`verify_date_anchor()` 内で完結）。例外時も TemporaryDirectory の
  コンテキストマネージャが削除を保証する。

### 変更 2: `--workdir` 内の既存ファイル保護（指摘 [中] 2）

レビュー対応案のうち「実行ごとの新規サブディレクトリを作る」を採用する（明示的失敗より、
連続実行・再実行で破壊が起きない方が安全側。既存ファイルの存在確認より誤破壊を構造的に防げる）。

- `--workdir` 指定時: `workdir / "verify-date-anchor-YYYYmmdd-HHMMSS"` 形式（必要に応じて
  `-N` 連番で一意化）のサブディレクトリを作成し、そこへ raw/DB コピーを置く。
  `workdir` 直下の既存ファイルには一切触れない。
- `report["workdir"]` には実際に使用したサブディレクトリのパスを記録する（stdout の
  「作業コピー:」表示も同じパスを示す）。
- `--workdir` 未指定時は変更 1 の TemporaryDirectory 内に置くため、衝突は構造的に発生しない。

### 変更 3: 再正規化後の座標をレポートに含める（指摘 [中] 3）

`tasks/issue_47/architecture.md` の設計（再正規化後の raw から代表 box を出力）に従う。

- 段階 3（再正規化）成功後、再正規化済み raw コピー（`raw_copy`）を再読込し、
  **date box と代表フィールド（name / clinic / amount のうち raw に存在するもの）の box** を
  `report["renormalization"]["normalized_boxes"]` として記録する。
  - date box は anchor box から offset 適用後の座標（= 再正規化後 raw 内の該当エントリ box）。
  - フィールド box は再正規化後 raw のエントリから、段階 4 の `search_fields_by_proximity`
    が採用したエントリの box を含める（抽出値と box の対応が追跡可能になる）。
- 標準出力: 段階 3 の表示に「再正規化後 date box=...」を、段階 4 の各フィールド表示に
  `box=...` を追加する。
- JSON レポート（`--report`）: 上記 `normalized_boxes` / 各抽出フィールドの box が自動的に
  含まれる（report dict への追加のみで対応）。

### 変更 4: タスク概要のステータス更新（指摘 [低]）

`tasks/issue_47/overview.md` L3-6 のヘッダを更新する:

- 「ステータス: 計画確定・未実装」→「ステータス: 実装済み（PR #49。CLI・テスト 4 件・
  サンプル手順含む。PR #49 レビュー指摘の修正は tasks/pr_49/ で対応）」
- 「実装は次回セッションに実施予定（2026-10-30 記録）」の文言を実装完了の記録に置換。

## 変更ファイル一覧

| ファイル | 変更種別 | 内容 |
| --- | --- | --- |
| `app/verify_date_anchor.py` | Modify | 変更 1（TemporaryDirectory + ExitStack）、変更 2（workdir サブディレクトリ化）、変更 3（normalized_boxes を report/stdout に追加） |
| `tests/test_verify_date_anchor.py` | Modify | 変更 1・2・3 のテスト追加（下記テスト参照） |
| `tasks/issue_47/overview.md` | Modify | 変更 4（ステータス更新） |
| `tasks/pr_49/plan.md` | New | 本計画 |
| `app/date_anchor.py` / `app/coord_normalizer.py` / `app/coord_search.py` / `app/db.py` | 変更なし | 検証ツールは既存関数を再利用するのみ（Issue #47 の方針維持） |

## テスト / 検証

`tests/test_verify_date_anchor.py` に追加（既存 4 件の回帰も確認）:

1. **一時領域の後始末（変更 1）**:
   - `workdir=None` で正常完了後、`tempfile.gettempdir()` 配下に
     `verify-date-anchor-*` が残っていないことを確認（実行前後でディレクトリ一覧を比較）。
   - 前提不成立（DB 未指定 / template なし）・解決不能の各早期 return 経路でも残らないこと。
   - 例外を起こす入力（例: 不正 JSON を raw に指定）でも残らないこと。
2. **workdir 衝突防止（変更 2）**:
   - `--workdir` 相当のディレクトリに既存 `raw_data.json`（無関係な内容）を置いて実行し、
     そのファイルが**変更されない**こと。
   - 実行ごとに新しいサブディレクトリが作られ、`report["workdir"]` がそのパスを指すこと。
   - 2 回連続実行して互いのコピーが干渉しないこと。
3. **座標スナップショット（変更 3）**:
   - 成功ケースで `report["renormalization"]["normalized_boxes"]` に date box が含まれ、
     その座標が offset 適用後（date 基準原点近傍）に一致すること。
   - 抽出フィールド（name / amount）の box が report に含まれること。
   - stdout 表示（`main()` の capsys テストまたは report 経由の間接確認）に座標が現れること。
4. **全体**: `uv run pytest -q`（既存 4 件 + 追加分が全 PASS）。`black .`（line-length=119）。

## リスク・トレードオフ・未確定事項

- **workdir サブディレクトリ化の副作用**: 既存ユーザーが `--workdir` 直下の
  `raw_data.json` を期待していた場合、パスが変わる。ただし PR #49 が初回提供のため
  互換性破壊は実質なし。`report["workdir"]` と stdout 表示で実パスを明示する。
- **タイムスタンプ衝突**: 同一秒に複数実行した場合のサブディレクトリ衝突は `-N` 連番
  （または `mkdtemp` をサブディレクトリ生成に使用）で回避する。実装時に
  `tempfile.mkdtemp(prefix="verify-date-anchor-", dir=workdir)` を使えば一意性を
  標準ライブラリに委ねられる（推奨）。
- **normalized_boxes の粒度**: 「代表フィールド」を name/clinic/amount 全件にするか、
  段階 4 の抽出結果に現れたものに限定するか。→ 抽出結果に現れたもの + date box を
  含める（レポートが冗長にならず、抽出値と box の対応が追跡できる）。
- **スコープ外**: 本番処理フロー（`image_processing_service.py` / `receipt_processor.py` /
  `main.py`）は変更しない。`apply_date_anchor_normalization` 等の既存関数の挙動も変更しない。
- **ドキュメント**: `docs/architecture/2026-11-02-architecture.md` の SHA-256 記述は
  PR 変更ファイル外のため本計画では触れない（レビューの「資料との整合」欄の指摘だが、
  別途確認が必要な場合はユーザー指示を仰ぐ）。

## 実装ステップ（概要）

1. 失敗テスト追加（変更 1・2・3 のテストを先に書く）→ RED 確認。
2. `verify_date_anchor()` を ExitStack + TemporaryDirectory 構造に書き換え（変更 1）。
3. workdir サブディレクトリ化（変更 2、`tempfile.mkdtemp(dir=workdir)`）。
4. normalized_boxes の記録と stdout 追加（変更 3）。
5. `tasks/issue_47/overview.md` ステータス更新（変更 4）。
6. `uv run pytest -q` 全体 + `black .`。コミットはユーザー確認後に。
