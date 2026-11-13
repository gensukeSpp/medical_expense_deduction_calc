# PR #55 レビュー指摘事項対応タスク

> 実施順に並べたタスク。各タスクは TDD / 最小テスト / 実装 / 検証の順で進める。
> 参照: `tasks/pr_53/tasks.md` をベースに、今回の対応対象を B-F / G に整理する。

---

## タスク 1: A の確認と現状整理

**Objective:** A は既に対応済みであることを確認し、作業の焦点を B-F に固定する。

**Files:**
- `app/services/receipt_list_service.py`
- `tests/test_calc.py`

**Checklist:**
- 日付不明行が `date_missing_rows` として分離されているか確認
- `date_missing_count` が取得可能か確認
- `test_get_page_context_date_missing_count` が実装と整合しているか確認
- B 以降の修正では A を再修正しない

**Validation:**
```bash
uv run pytest tests/test_calc.py -q
```

---

## タスク 2: 補填額の負値対策 (B)

**Objective:** 補填額に負の値が入ると、正数として扱われる問題を防ぐ。

**Files:**
- `app/services/receipt_list_service.py`
- `tests/test_calc.py`

**要件:**
- `_to_amount()` または `save_compensation()` で負の数値を弾く
- 0 未満の入力は 0 に丸める or validation error にする
- 既存の仕様と計算ロジックを崩さない

**Step:**
1. 負の補填額の既存挙動を再確認
2. 何を安全側とするか決定（0 に丸める / 400 で拒否）
3. 最小テストを追加

**Validation:**
```bash
uv run pytest tests/test_calc.py -q
```

---

## タスク 3: 氏名フィルタの安全化 (C)

**Objective:** 氏名にカンマを含むケースでも、氏名フィルタが壊れないようにする。

**Files:**
- `app/web/server.py`
- `app/web/templates/list_table.html`
- `tests/test_web.py`

**要件:**
- `names` を `join(',')` のまま送る設計を見直す
- `hx-vals` / `#list-state` の送信形式を安全にする
- `_parse_names()` が複数パラメータに対応している前提を維持する

**Step:**
1. カンマ付き氏名の現在の挙動を再確認
2. 送信形式の安全化を実施
3. 回帰テストを追加

**Validation:**
```bash
uv run pytest tests/test_web.py -q
```

---

## タスク 4: UI 配置と文言の整合 (D / E)

**Objective:** 氏名フィルタの配置とコメントの文言が要件と実装に一致するように整える。

**Files:**
- `app/web/templates/list_table.html`

**要件:**
- D: Issue #54 の要求に対してフィルタ配置が妥当かを確認
- E: 「古价格在混入しないようにする」の文字化けを修正

**Step:**
1. 実装上の配置の妥当性を確認
2. 要件とズレる場合は UI や spec を整理
3. コメントの明確化

**Validation:**
```bash
uv run pytest tests/test_web.py -q
```

---

## タスク 5: 計画・タスク文書の drift 解消 (F)

**Objective:** `tasks/issue_54/` の記述が実装の最終形と一致するように更新する。

**Files:**
- `tasks/issue_54/` 配下

**要件:**
- 旧来的な `if False else params` などの未完成記述を更新
- 実装に相当する `GET/PUT` 分岐や `#list-state` を反映
- 検証における意味のない `assert "200" in response.text` などを削除

**Validation:**
- 文書の整合確認
- 必要なら `pytest` の最小回帰を実施

---

## タスク 6: テスト不足の補完 (G)

**Objective:** 境界条件のテストを補完し、今回の指摘が再発しないことを確認する。

**Files:**
- `tests/test_calc.py`
- `tests/test_web.py`

**追加対象:**
- 日付欠損レコードの取り扱い
- 負の補填額入力
- カンマを含む氏名フィルタ
- 月ナビ / sort / names 維持

**Validation:**
```bash
uv run pytest tests/test_calc.py tests/test_web.py -q
```

---

## タスク 7: 最終確認

**Objective:** 修正した範囲の回帰確認を行い、指摘事項の対応状態をまとめる。

**Validation:**
```bash
uv run pytest tests/test_calc.py tests/test_web.py -q
```

**完成条件:**
- B, C, D, E, F が対応済み
- G が補完済み
- A については確認済みで再発防止の確認を残している
- 実装と文書が一致している
