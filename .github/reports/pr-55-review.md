# PR #55 レビュー: feat #261112: Issue #54 抽出結果の一覧と合計額

対象: PR #55 (DRAFT) / head: feature/calc-amount-sum/54 / 比較基点: origin/main（main ブランチは存在せず origin/main を使用）
関連 Issue: #54 「[Feature] 抽出データの一覧表記と計算機能」(OPEN)
関連 task / 計画: tasks/issue_54/ (overview.md, tasks.md, architecture.md, test_plan.md) — 4 件すべて存在
関連 spec: specs/monthly-list-total-spec.md（本 PR で新規。機能起点命名）
関連 rules: .hermes/rules/monthly-list-calculation.md（本 PR で新規）
除外: AGENTS.md / .hermes/ は AI エージェント設定としてレビュー範囲外

## 参照資料の確認状況

- Issue #54: 存在・一致（PR の目的と一致）
- tasks/issue_54/*: 存在（4 件）。ただし内容が実装と乖離（後述 指摘 F）
- specs/monthly-list-total-spec.md: 存在・実装と整合
- docs/architecture/*: 本件対象には無し（Issue #54 では architecture は tasks/issue_54/architecture.md に置く方針）。docs/architecture 配下の未コミット変更は本 PR の範囲外
- 知識グラフ: review(action="context") は MCP 経由で失敗（DB マイグレーションが ~/.crg/graph.db を参照し、.git が見つからない）。CLI の graph stats は取得でき（last_updated 2026-11-12T15:03:47, files 64）、CLI 側で代替の影響確認は行わず、ソース読解と実行で検証した

## 総評

Issue #54 の受け入れ要件（1: 氏名選択で合計が動的に変わる、2: 補填額は行単位で控除し一括マイナスしない）は実装・テストともに満たしている。計算は app/calc.py の純粋関数に分離され、補填額は structured_data.json の compensation キーに保存、DB スキーマは変更なし。計画・spec・実装の方針は概ね一致している。

一方、実データで起きうる境界条件（日付欠損の領収書の無言除外、負の補填額入力、氏名にカンマを含む場合）と、計画書側の古い記述に問題がある。合計額が控除計算の根拠になる用途のため、指摘 A と B は修正を推奨する。

## 確認したテスト結果

- uv run pytest tests/test_calc.py tests/test_web.py -q → 53 passed（PR 記載の全体 342 passed / 1 failed は未再現。全体スイートは未実行）
- 境界値の実行確認（後述 指摘 A/B/C は実際に再現済み）

## 良い点

1. 計算の核心が純粋関数に分離されている（app/calc.py: compute_contribution = max(0, amount - compensation)、compute_total = Σ 行 contribution）。受け入れ要件 2 の「一括引きしない」が単体・結合の両方でテストされている（5000/6000 と 3000 → 3000）。
2. DB を使わず JSON 起点にした判断が spec / overview に理由付きで記録されている（未修正領収書の漏れ、DB と JSON の乖離を回避）。
3. 補填額の保存が ReceiptFileRepository.save_receipt（atomic 書き込み）を再利用しており、既存の詳細修正 PUT /{stem} で compensation が消えないことも結合テストで確認している。
4. htmx の PUT で query_params が空になる問題を _list_context で GET/PUT 分岐して解決している。またネストした form を避けるため、状態を #list-state に集約している。
5. 氏名の複数指定（カンマ区切り + 複数パラメータ）と重複除去（_parse_names）に対するテストがある。
6. 一覧の文脈構築が ReceiptListService に分離され、ReceiptService には触れていない（影響範囲が限定されている）。

## 指摘事項

重大度は以下の基準: 高 = 合計額や保存データが誤る可能性 / 中 = 機能要件の欠落や誤操作に繋がる / 低 = 保守性・文言・文書。

### [A] 高: 日付が不正・欠損の領収書が、一覧にも合計にも出ず無言で消える
- 場所: app/services/receipt_list_service.py load_rows()（parse_date が None のとき date = ""）、get_page_context()（r.date[:7] == month で絞るため date="" は全月から漏れる）
- 再現: 日付が空の structured_data（amount 2000）を置くと、load_rows では 3 件読めるが、2026-01 の表示・合計 6000 からは除外される（実行確認済）。
- 影響: 控除対象の領収書が、画面のどこにも表示されず合計から外れる。利用者は気付けない。
- 修正方針: 日付不明の行を「日付不明」として別枠で表示する、または一覧上部に「日付不明 N 件（合計対象外）」の警告を出す。少なくとも件数を画面に出し、テストを追加する（tests/test_calc.py に get_page_context の日付欠損ケースを追加）。

### [B] 中: 補填額に負の値を入力すると符号が消えて正の補填額として扱われる
- 場所: app/services/receipt_list_service.py _to_amount() → app/normalization.py parse_amount（"-500円" を 500 に変換）
- 再現: 補填額に「-500円」と入力すると 500 円の補填として保存・控除される（実行確認済）。数値 "-500" は 0 になり、動作が入力形式で不一致。
- 影響: 負の補填を入力すると、合計が増える方向に誤る可能性がある。
- 修正方針: 補填額は 0 以上の整数のみ受け付け、負の入力は 0 にするか 400 で拒否する。_to_amount の前段で符号を検出するか、save_compensation で value < 0 を弾く。テストを追加する。

### [C] 中: 氏名にカンマを含むと、氏名フィルタが機能しなくなる
- 場所: app/web/templates/list_table.html #list-state の names（カンマ連結）と、app/web/server.py _parse_names（カンマで分割）
- 再現: 氏名「鈴木, 花子」が一覧に候補として表示されるが、選択するとカンマで「鈴木」「花子」に分割され、該当行が表示されない（実行確認済）。
- 影響: 氏名データに記号が入る場合に絞り込みが失敗する。個人の氏名に記号が入る可能性は低いが、データ由来の値をエンコードせず区切り文字として使っている設計の問題。
- 修正方針: #list-state と hx-vals の names を、カンマ連結ではなく names を複数の hidden input（または繰り返しパラメータ）で送る。_parse_names は既に複数パラメータに対応しているため、サーバ側変更は不要。

### [D] 低: 氏名フィルタの配置が Issue 要件と異なる
- 場所: list_table.html の氏名フィルタは表の上の fieldset。Issue #54 機能要件は「表のタイトル部分（日付、氏名）に…氏名選択のチェックボックスを付ける」。
- 影響: 機能としては動くが、Issue の文言とは配置が異なる。UI として許容できるかは要確認。
- 対応: 意図的な変更なら spec に理由を追記する（現在の spec は配置を明記していない）。Issue 通りにするなら氏名列ヘッダにチェックボックス群を置く。

### [E] 低: テンプレートのコメントに文字化け（誤字）がある
- 場所: app/web/templates/list_table.html:61 `{# 昇順/降順トグル。状態を hx-vals で明示して古价格在混入しないようにする #}`
- 修正: 「古い値が混入しないようにする」へ修正。

### [F] 低: 計画書 tasks/issue_54/ の記述が実装と乖離している（文書の drift）
- tasks.md Task 3: 「if False else params」の未完成コード、_list_context の旧版が残存。実装は GET/PUT 分岐の最終版。
- tasks.md Task 4 / architecture.md / test_plan.md: 「hx-include='closest form'」と外側 form を前提とした記述。実装は #list-state + hx-put/hx-get の兄弟構造で、ネスト form は禁止に従い回避済み。
- tasks.md Task 7: `test_list_compensation_updates_json_and_keeps_positive(total)...` と書かれた構文不正のプレースホルダ、`assert "200" in response.text` という意味のないアサートが残存。実テスト（tests/test_web.py）には無い。
- 対応: 計画書は「実装の決定を反映した最終版」に更新するか、冒頭に「実装で変更された箇所（上記）」の注記を入れる。spec と .hermes/rules は実装と整合しており問題なし。

### [G] テスト不足（低）
- 日付欠損の行（指摘 A）、負の補填額（指摘 B）、氏名にカンマを含む場合（指摘 C）、氏名フィルタ・月ナビでの sort/names 維持、の各テストが無い。
- 既存の PR 記載「342 passed, 1 failed」の失敗（test_calls_process_image_and_logs）は本 PR 外の既知失敗と記載されているが、今回の検証では全体スイートを未実行のため未再現。

### [H] 注意: 作業ツリーに本 PR と無関係の未コミット変更がある（参考）
- git status: GEMINI.md / docs/architecture/README.md の変更、requirements.txt・test_logic*.py・docs/architecture/2026-11-12-architecture.md の未追跡、.vscode/mcp.json の削除。
- 本 PR のコミット（head）には含まれていない。マージ・コミット時にこれらを混入させないこと。

## 改善提案（優先順）

1. [A] 日付欠損の行を「日付不明」として表示し、合計対象外であることを画面で明示する。テスト追加。
2. [B] 補填額は 0 以上の整数のみ受け付ける（負は 0 または 400）。テスト追加。
3. [C] #list-state・hx-vals の names を複数 input / 繰り返しパラメータで送り、カンマ連結をやめる。
4. [E] list_table.html:61 の文字化けを修正する。
5. [D] 氏名フィルタの配置を Issue 通りにするか、意図的変更として spec に明記する。
6. [F] tasks/issue_54/ を実装の最終形に合わせて更新する（特に tasks.md Task 3/4/7）。
7. 全体スイートを実行し、PR 記載の既知失敗が main と同一であることを確認する。

## 判定

Issue #54 の要件（受け入れ要件 1・2 を含む）に対する実装は妥当。マージ前に最低限 [A] と [B] の対応を推奨する。[C]〜[G] は別タスクとして扱ってもよい。

---
Reviewer: pr-review profile (Hermes Agent). コードは変更していない。レビュー記録のみ作成。
