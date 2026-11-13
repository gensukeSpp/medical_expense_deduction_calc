# Architecture Snapshot: 2026-11-12 (Monthly List & Medical Expense Total Calculation - Issue #54 / PR #55)

## Purpose
Document the architectural implementation of monthly medical expense list viewing, individual contribution calculation, compensation management, and name/month filtering (Issue #54 / PR #55).

## Overview
- **Pure Calculation Engine (`app/calc.py`)**: Implemented pure functions for contribution calculation (`compute_contribution = max(0, amount - compensation)`), total calculation (`compute_total = Σ contribution`), sorting, and month/name filtering.
- **Receipt List Service (`app/services/receipt_list_service.py`)**: Encapsulates data loading (`load_rows()`), page context aggregation (`get_page_context()`), and compensation persistence (`save_compensation()`, storing compensation in `structured_data.json` without DB schema changes).
- **Web Interface & HTMX Fragments (`app/web/server.py`, `app/web/templates/`)**: Added routes (`GET /list`, `GET /list/table`, `PUT /list/items/{stem}/compensation`) and responsive templates supporting interactive htmx updates, sorting toggles, and month navigation.
- **Testing & Specification (`specs/monthly-list-total-spec.md`, `tests/test_calc.py`, `tests/test_web.py`)**: Added 20 unit tests for pure calculation/services and 8 integration tests for web endpoints.

## Key Components Changed
- `app/calc.py`: Pure math, filtering, and sorting logic for medical expense calculation.
- `app/services/receipt_list_service.py`: Service-layer orchestration for list loading and compensation saving.
- `app/web/server.py`: Added web endpoints for list views and inline compensation updates.
- `app/web/templates/`: Added `list.html`, `list_table.html`, and navigation link in `index.html`.
- `specs/monthly-list-total-spec.md`: Formal specification for monthly lists and totals.
- `.hermes/rules/monthly-list-calculation.md`: Design rules for monthly calculations.
- `tasks/issue_54/`: Task tracking, architecture notes, overview, and test plan.
- `tests/`: Added `test_calc.py` and `test_web.py` test cases.

## Key Design Decisions
- **Non-destructive Compensation Persistence**: Compensation amounts are stored directly within `structured_data.json` under the `compensation` key, avoiding complex database schema migrations while keeping data closely coupled with receipt extractions.
- **HTMX & Form Separation**: Isolated filter forms and table rows to prevent form nesting issues, using `hx-vals` for precise state preservation during sorting and filtering.

## PR vs. Source Comparison & Discrepancies
- **Alignment**: Inspected `gh pr view 55` and local branch diffs. All changed files and implementation details match the PR description and specifications precisely. No discrepancies found.

## Changed Files
- `.hermes/rules/monthly-list-calculation.md`
- `AGENTS.md`
- `app/calc.py`
- `app/services/receipt_list_service.py`
- `app/web/server.py`
- `app/web/templates/index.html`
- `app/web/templates/list.html`
- `app/web/templates/list_table.html`
- `specs/monthly-list-total-spec.md`
- `tasks/issue_54/architecture.md`
- `tasks/issue_54/overview.md`
- `tasks/issue_54/tasks.md`
- `tasks/issue_54/test_plan.md`
- `tests/test_calc.py`
- `tests/test_web.py`

## Commits
- `995696e` feat #54: 純粋計算関数 (contribution/total/filter/sort) を追加
- `e6c9d0a` feat #54: 一覧整形サービス (load_rows / get_page_context / save_compensation) を追加
- `7f2e6e7` feat #54: 一覧ページ・補填額保存のルートを追加
- `b79db04` feat #54: 月別一覧テンプレート (list / list_table) & トップページリンク追加
- `c41c455` test #54: 一覧表示・行単位補填・氏名フィルタの結合テストを追加
- `1526b7a` docs #54: 月別一覧・合計計算の仕様と設計判断を記録
