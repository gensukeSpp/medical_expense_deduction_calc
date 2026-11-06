# Architecture Snapshot: 2026-11-06 (coord_basis Consistency Fix on Correction - Issue #51 / PR #52)

## Purpose
Document the architectural fix for `coord_basis` consistency during user correction feedback (Issue #51 / PR #52). When manual or web corrections are processed for templates operating under `coord_basis='date'`, the template basis is automatically reset to `'topmost'` because corrected coordinates are derived from raw OCR JSON which uses topmost coordinates.

## Overview
- **Template Upsert Support (`app/db.py`)**: Enhanced `upsert_template()` to optionally accept and persist `coord_basis` during INSERT/UPDATE operations while preserving backward compatibility (`None` preserves existing behavior). Fixed a SQL placeholder bug (`VALUES` duplication).
- **Correction Feedback Handling (`app/template_feedback.py`)**: Updated `process_correction_feedback()` to check if the template's current `coord_basis` is `'date'`. If so, it resets it to `'topmost'` during upsert to maintain coordinate system consistency.
- **Testing & Specification (`tests/test_feedback.py`, `tests/test_web.py`, `specs/2026-11-06-1-spec.md`)**: Added unit tests validating `coord_basis` transition from `date` to `topmost` upon feedback processing and web admin verification.

## Key Components Changed
- `app/db.py`: Added optional `coord_basis` parameter to `upsert_template()` and fixed SQL VALUES placeholder syntax.
- `app/template_feedback.py`: Added logic to reset `coord_basis` to `'topmost'` when processing corrections on date templates.
- `.hermes/rules/coordinate-normalization.md`: Updated rules reflecting `coord_basis` reset behavior on correction.
- `specs/2026-11-06-1-spec.md`: Added formal specification for Issue #51.
- `tasks/issue_51/`: Added task breakdown, test plan, architecture notes, and overview.
- `tests/test_feedback.py` & `tests/test_web.py`: Added tests verifying admin display and DB state after correction feedback.

## Key Design Decisions
- **Automatic Basis Fallback on Correction**: Because user corrections originate from OCR JSON coordinate extracts (which are topmost-relative), retaining `coord_basis='date'` after a correction would cause subsequent date-anchor re-normalizations to misalign. Resetting to `'topmost'` on correction ensures robust consistency.
- **Backward-Compatible Upsert**: `upsert_template` accepts `coord_basis: Optional[str] = None` so existing callers without basis specifications remain entirely unaffected.

## Changed Files
- `.hermes/rules/coordinate-normalization.md`
- `AGENTS.md`
- `app/db.py`
- `app/template_feedback.py`
- `specs/2026-11-06-1-spec.md`
- `tasks/issue_51/architecture.md`
- `tasks/issue_51/overview.md`
- `tasks/issue_51/tasks.md`
- `tasks/issue_51/test_plan.md`
- `tests/test_feedback.py`
- `tests/test_web.py`

## Commits
- `170bf04` fix #51: coord_basis の整合（修正時 topmost へ戻す）
- `0ae2569` docs #51: spec と rules に coord_basis リセット仕様を反映
- `7b36424` docs #261106: Issue #51 実装プラン・タスクファイル
