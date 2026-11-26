# Issue #62: 抽出正規化の補正と Web UI の DB 選択動作を改善

## 目的

[ADR 001](../docs/adr/001_date-anchor-abandoned-topmost-maintained.md) の案 ii
（抽出結果の空値に過去の補正値を採用する）を判断する基準を `template_history` 参照で
定める前に、その土台となる抽出・正規化の改善を行う。同時に、Web UI の DB 選択で
「検証のたびに `data/` 配下へ `<name>.db` / `<name>_1.db` ... と DB が增殖する」問題を
解消する。

## 現状（コンテキスト）

- `app/llm_extractor.py::extract_name_from_lines()` は名前抽出のプレフィックスを
  `患者` / `患者:` / `患者：` で判定している。
- `app/normalization.py::parse_amount(text) -> Optional[int]` は金額先頭の `¥`（U+00A5）
  と `￥`（U+FFE5 全角）を処理しない。`¥3,800円` は「前方検索による非アンカーな `円` 抽出」で
  たまたま 3800 になるが、`¥3800` / `¥3,800`（`円` なし）は `None` になる。
- Web UI の DB 選択は `app/web/server.py` の `/config` で `db_file`（UploadFile）を受け取り、
  `app/services/watch_controller.py::save_db_upload()` が `data/` に保存する。
  同名ファイルがあると `_install_db()` が `name_1.db` 等の連番を作る（Issue #56 の設計）。
  → 検証のたびに DB ファイルが増殖する。

## 完了条件（Issue 本文より）

1. `氏名` を含む OCR 行から氏名を抽出できる。
2. `¥` 付き金額を整数に正規化でき、既存の金額表記（`3,800円` / `一万二千円` / プレーン数字）の
   処理は維持される。
3. Web UI で DB を選択しても同名 CSV / 連番コピーが増えず、選択した DB が以後の Web 操作で
   使用される。
4. 関連テストを追加・更新し、既存の対象テストが通る。

## 設計方針（対策の取捨選択）

| 観点 | 決定 | 理由 |
|------|------|------|
| 名前抽出プレフィックス | `氏名` / `氏名:` / `氏名：` を**先頭に追加**し、既存 `患者` 系は**下位互換として残す** | Issue 本文は「`患者` を `氏名` に変更」だが、`tasks/issue_4/ocr_sample_noisy.json` の `患者: 山田太郎` を `test_mock_extractor_noisy` が期待値にしており、`患者` を消すと既存テストが落ちる。`氏名` を優先チェックにし `患者` をフォールバックに残す（完了条件「`氏名` を含む行から氏名を抽出できる」は満たし、既存 `患者` 行も壊さない） |
| `¥` の扱い | `normalize_fw` 後に `t.lstrip("¥￥")` で先頭の通貨記号を除去 | 全角／半角とも 1 回の lstrip で処理。negative チェックは記号除去より前（`¥-500` は負値として拒否を維持） |
| DB 選択 | **案A（ユーザー回答済み）**: サーバ側パスのテキスト入力に変更し、既存ファイルを参照。`select_db_path` で検証＋migration。**ファイルアップロード機能は削除**。さらに **`create_db_path`（新規作成）を追加**し、初回起動時に空 DB を新規作成して使う | input_dir の既存パターンと同じ UX。連番コピー生成が構造的に消える。「最初にアプリを動かす場合、参照する DB がまだ存在せず選択できない」問題を解消（ユーザー指摘 2026-11-26 反映） |
| 参照先パス検証 | `_validate_uploaded_db`（SQLite ヘッダ＋必須テーブル）を流用し、`select_db_path` から呼ぶ | 既存の検証ロジックを再利用（DRY）。存在しないパス・DB でないファイルは ValueError |
| 空 DB の新規作成 | `create_db_path(path)` を追加。`_run_migrations` で空 DB を作成（`get_db_connection` が `parent.mkdir` するため新規パスも自動作成）し `_db_path` に設定。存在チェックはしない | 初回起動（既存 DB なし）で使えるようにする。`select_db_path` は「既存参照」、`create_db_path` は「新規作成」の役割分担。両者とも絶対パス＋`_scan_lock`（busy 409）ガード |
| 既存 `set_db_path` | 変更しない（`__init__` / `create_app` の互換用）。Web からの選択は新設 `select_db_path` 経由 | `set_db_path` は migration でファイルを生成する振る舞いを持つ。これを変えると CLI 起動経路と既存テストを壊す。YAGNI で新設メソッドに閉じる |
| アップロード廃止の影響 | `save_db_upload` / `_install_db` / `_atomic_install` を削除。`data_dir` 引数は互換のため残す（未使用化） | 案A の明示。`create_app` のシグネチャ互換（既存テストが `data_dir` を渡す）のため引数は残す |

## ファイル変更表

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `app/llm_extractor.py` | 修正 | `extract_name_from_lines()` に `氏名` プレフィックスを優先追加（`患者` は下位互換で残す） |
| `app/normalization.py` | 修正 | `parse_amount()` で先頭の `¥` / `￥` を除去してから数値展開 |
| `app/services/watch_controller.py` | 修正 | `select_db_path(path)`（既存参照）と `create_db_path(path)`（新規作成）を追加。`save_db_upload` / `_install_db` / `_atomic_install` 削除 |
| `app/web/server.py` | 修正 | `/config` の `db_file` 分岐を `db_path`（テキスト）分岐に置換 |
| `app/web/templates/index.html` | 修正 | DB 選択をファイル input からパステキスト input＋「適用」ボタンに変更、JS を更新 |
| `tests/test_llm_wrapper.py` | 修正 | `氏名` 抽出テストを追加 |
| `tests/test_normalization.py` | 修正 | `¥` 付き金額テストを追加 |
| `tests/test_watch_controller.py` | 修正 | `save_db_upload` 系 10 テストを `select_db_path` / `create_db_path` 系テスト（8 件）に置換 |
| `tests/test_web.py` | 修正 | `/config` の DB パス選択・新規作成の結合テストを追加 |
| `specs/db-select-path-spec.md` | 新規 | 実装後ドキュメント（動作・決定事項） |

## 実装順序（推奨）

Task 1（氏名抽出）→ Task 2（`¥` 正規化）→ Task 3（Web UI DB 選択）→ Task 4（ドキュメント）

各結合テスト・テスト期待数の詳細は `tasks.md` 参照。

## リスク・未確定事項

- **`set_db_path` の作成振る舞い**: CLI 経由 `create_app(db_path=...)` はファイルが無ければ
  migration が作る。Web 側は両対応にする — `select_db_path`（既存参照・存在チェック必須）と
  `create_db_path`（新規作成・存在チェックなし）。初回起動では「新規作成」を使う。
- **`save_db_upload` 削除の波及**: 既存 10 テストが参照するため、同じ Task でテストを
  `select_db_path` / `create_db_path` 系へ置換する（独立コミットで green を保つ）。
- **`data_dir` 引数の残置**: 未使用のまま互換のため残す。将来クリーンアップしても良いが、
  `create_app` シグネチャとテストヘルパーを壊すため本 Issue では触らない。
- **PDF / 複数ファイル**: 既存のスコープ外（Issue #56 のまま）。

## 追記（2026-11-26: PR #63 レビュー [P2]#1 対応）

上記「空 DB の新規作成」行の「存在チェックはしない」は見直した。「新規作成」ボタンに
既存 DB パスが入力された状態で押下すると、その DB にアプリのテーブル／migration が適用され
既存 DB がそのまま使用対象になってしまう副作用があるため、`create_db_path` は既存
ファイル／ディレクトリを ValueError で拒否し、`os.open(O_CREAT|O_EXCL)` で空ファイルを
原子的に作成する方式に変更した（作成時の競合も既存ファイルを上書き・変更しない）。
仕様は `specs/db-select-path-spec.md` を更新済み。
