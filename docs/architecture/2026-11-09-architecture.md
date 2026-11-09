# Architecture Snapshot: 2026-11-09 (Forced Date-Basis Extraction Fallback - Issue #50 / PR #53)

## Purpose
Document the architectural addition of forced date-basis coordinate extraction (`apply_date_basis_force`) when date-anchor resolution fails or returns no candidates (Issue #50 / PR #53).

## Overview
- **Forced Extraction Fallback (`app/date_anchor.py`)**: Implemented `apply_date_basis_force()` to apply clinic template `coords_corrections` directly via `search_fields_by_proximity` when date anchor resolution cannot resolve a date box (`None`), given that `coord_basis == 'date'` and the date box is learned.
- **Verification Tool Extension (`app/verify_date_anchor.py`)**: Added `--force` option to the date-anchor verification CLI and `verify_date_anchor()` to report fallback forced extraction results even when anchor resolution is `None`.
- **Pipeline Integration (`ImageProcessingService`, `ReceiptProcessor`)**: Integrated dual-path fallback so that if `apply_date_anchor_normalization()` returns `None`, `apply_date_basis_force()` is invoked.
- **Testing & Specification (`specs/2026-11-06-2-spec.md`, tests)**: Added unit tests and specification covering force extraction success, gate validation, and pipeline integration.

## Key Components Changed
- `app/date_anchor.py`: Added `apply_date_basis_force()` for un-anchored fallback extraction.
- `app/verify_date_anchor.py`: Added `--force` support and reporting.
- `app/services/image_processing_service.py` & `app/services/receipt_processor.py`: Integrated forced extraction fallback when anchor resolution is `None`.
- `.hermes/rules/coordinate-normalization.md`: Updated rules for forced extraction behavior.
- `specs/2026-11-06-2-spec.md`: Formal specification for Issue #50.
- `tasks/issue_50/`: Added task breakdown, test plan, architecture notes, and overview.
- `tests/`: Added unit tests in `test_date_anchor.py`, `test_verify_date_anchor.py`, `test_image_processing_service.py`, and `test_receipt_processor.py`.

## Key Design Decisions
- **Safe Scope Restriction**: Forced extraction is strictly gated to templates where `coord_basis == 'date'` and date box is learned, preventing conflicts with topmost templates (`#40` guard).
- **Consolidated Fallback**: Reuses existing `search_fields_by_proximity` and `update_receipt_ocr_json_by_source` to maintain data integrity and avoid duplicate DB records.

## PR vs. Source Comparison & Discrepancies
- **Alignment**: Inspected `gh pr view 53` and local branch diffs. All 15 PR files match implementation details precisely. No discrepancies found between PR description, spec (`specs/2026-11-06-2-spec.md`), and actual code/test changes.

## Changed Files
- `.hermes/rules/coordinate-normalization.md`
- `AGENTS.md`
- `app/date_anchor.py`
- `app/services/image_processing_service.py`
- `app/services/receipt_processor.py`
- `app/verify_date_anchor.py`
- `specs/2026-11-06-2-spec.md`
- `tasks/issue_50/architecture.md`
- `tasks/issue_50/overview.md`
- `tasks/issue_50/tasks.md`
- `tasks/issue_50/test_plan.md`
- `tests/test_date_anchor.py`
- `tests/test_image_processing_service.py`
- `tests/test_receipt_processor.py`
- `tests/test_verify_date_anchor.py`

## Commits
- `5a9a986` docs #261109: Issue #50 プラン・タスクファイル
- `9aed9aa` fix #50: --force 実行時の CLI 表示で KeyError になる不具合を修正
- `893c097` docs #50: 強制 date 基準補正の仕様・設計判断を記録
- `eaef657` feat #50: 両経路に apply_date_basis_force を統合（受入要件4）
- `a22b993` feat #50: date 基準補正の強制 apply_date_basis_force 追加（受入要件3）
- `63b50e8` feat #50: #47 検証ツールに --force モード追加（受入要件2）
- `df6fffa` docs #261106: PR #52 add 忘れ
