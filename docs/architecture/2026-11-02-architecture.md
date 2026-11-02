# Architecture Snapshot: 2026-11-02 (Date-Anchor Verification Tool & Spec - Issue #47 / PR #49)

## Purpose
Document the architectural implementation of the read-only CLI verification tool (`app/verify_date_anchor.py`) for date-anchor coordinate re-normalization and field extraction, introduced in Issue #47 (PR #49). This tool enables reproducible end-to-end verification of `coord_basis='date'` templates using real receipt data without modifying production databases or workflows.

## Overview
- **Read-Only Verification CLI (`app/verify_date_anchor.py`)**: Implemented a standalone, non-destructive CLI tool that directly invokes existing core functions (`find_date_candidates`, `resolve_date_anchor`, `normalize_coordinates_by_anchor`, `search_fields_by_proximity`) in the exact same sequence as production. It operates entirely on temporary working copies of raw JSON, databases, and structural data.
- **Unit Testing (`tests/test_verify_date_anchor.py`)**: Added comprehensive test cases covering success flows, `topmost` basis failure preconditions, missing templates, and ambiguous/tie candidate resolution (safe fallback).
- **Specification & Documentation**: Added formal specification (`specs/2026-11-02-spec.md`) and updated task architecture notes under `tasks/issue_47/`.

## Key Components Changed
- `app/verify_date_anchor.py`: New verification CLI module implementing safe copy isolation, multi-stage pipeline tracing, and JSON reporting.
- `tests/test_verify_date_anchor.py`: New unit tests for validating verification tool behaviors under various states (success, base mismatch, missing template, tie fallback).
- `specs/2026-11-02-spec.md`: Formal specification outlining requirements, invocation syntax, dataflow stages, and exit code meanings.
- `tasks/issue_47/`: Added architecture notes, task lists, and test plans.

## Key Design Decisions
- **Non-Destructive Isolation (Copy-on-Verify)**: All inputs (`--db`, `--raw`) are strictly duplicated into isolated working directories (`tempfile.mkdtemp` or custom `--workdir`), ensuring original files and SQLite databases remain completely immutable (`shutil.copy2` with SHA-256 integrity checks).
- **Reuse of Production Functions without Duplication**: Instead of rewriting parsing or normalization logic, the verification script directly imports and invokes the exact core functions used by the production OCR pipeline, guaranteeing absolute behavioral fidelity.
- **Safe Exit Code Contract**: Standardized exit codes (`0` for success, `1` for unresolved tie/fallback, `2` for precondition failures/missing files) to facilitate automated pipeline checks and debugging.

## Changed Files
- `AGENTS.md`
- `app/verify_date_anchor.py`
- `specs/2026-11-02-spec.md`
- `tasks/issue_47/architecture.md`
- `tasks/issue_47/overview.md`
- `tasks/issue_47/tasks.md`
- `tasks/issue_47/test_plan.md`
- `tests/test_verify_date_anchor.py`

## Commits
- `be29fd1` docs #47: 検証ツールの仕様・手順を記載
- `d036496` feat #47: date anchor 実データ検証ツール
