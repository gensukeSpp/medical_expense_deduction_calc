# Architecture Snapshot: 2026-10-30 (PR #46 / PR #45 Review Responses)

## Purpose
Document the architectural enhancements and bug fixes implemented in PR #46 (addressing feedback from PR #45 review), focusing on robust date-anchor coordinate normalization, layout consistency scoring for multi-candidate date boxes, Web admin form submission redirection (PRG pattern), and multi-box coordinate validation hardening.

## Overview
- **Layout Consistency Scoring for Date Anchor Resolution (`app/date_anchor.py`)**: Enhanced `resolve_date_anchor()` to accept `template_coords` and resolve multiple date candidates (dispensing date vs. issue date) via layout proximity scoring. Propagated `output_dir` through processing services to ensure date-anchor re-normalized coordinates are correctly persisted to `structured_data.json`.
- **Web Admin Redirection (`app/web/server.py`)**: Implemented 303 See Other redirection on template basis update form submissions, resolving the issue where submitting the admin form rendered raw JSON instead of returning to the template management interface.
- **Multi-Box & Coordinate Validation Hardening (`app/coord_normalizer.py`)**: Introduced `_has_any_numeric_point()` to properly handle multi-point coordinate structures, preventing incorrect "unlearned" error messages when some points are empty or non-numeric. Adapted `shift_template_to_date_basis()` polymorphically.
- **Service & Processing Alignment**: Ensured `image_processing_service.py` and `receipt_processor.py` pass the necessary output directories to downstream normalization hooks.

## Key Components Changed
- `app/date_anchor.py`: Added layout-based scoring to `resolve_date_anchor()` and integrated `output_dir` propagation for `apply_date_anchor_normalization()`.
- `app/coord_normalizer.py`: Strengthened coordinate point checking (`_has_any_numeric_point()`) and polymorphic shift handling for date-basis templates.
- `app/web/server.py`: Added 303 redirection to admin basis update endpoints.
- `app/services/image_processing_service.py` & `app/services/receipt_processor.py`: Propagated `output_dir` parameters.
- `tests/test_date_anchor.py` & `tests/test_image_processing_service.py`: Added unit and integration tests covering multi-candidate resolution, output directory reflection, and anchor mocks.
- `specs/2026-10-29-spec.md` & `tasks/pr_45/`: Added detailed technical specifications, plans, and task breakdowns for PR #45/46 implementation.

## Key Design Decisions
- **Layout Consistency over Heuristics**: When multiple date text boxes match criteria, template layout coordinates are used to score and select the most geometrically coherent candidate (e.g., distinguishing dispensing vs. issuance dates).
- **POST-Redirect-GET (PRG) Pattern for Admin Actions**: Web UI admin actions now respond with a 303 redirect back to `/admin/templates`, preventing browser confirmation warnings on refresh and avoiding raw JSON output leakage.
- **Robust Multi-Box Point Inspection**: Validation checks scan all coordinate points rather than assuming a single static structure, ensuring robustness against varying annotation formats.

## Changed Files
- `.github/reports/pr-45-review.md`
- `.hermes/rules/coordinate-normalization.md`
- `AGENTS.md`
- `app/coord_normalizer.py`
- `app/date_anchor.py`
- `app/services/image_processing_service.py`
- `app/services/receipt_processor.py`
- `app/web/server.py`
- `specs/2026-10-29-spec.md`
- `tasks/pr_45/plan.md`
- `tasks/pr_45/tasks.md`
- `tests/test_date_anchor.py`
- `tests/test_image_processing_service.py`
- `tests/test_migrations.py`

## Commits
- `51a7b50` docs #261030: pr-review update
- `ae64a9f` docs #261029: PR #45 レビュー結果、 Issue #42 実装結果ファイル
- `331ea80` fix #261029: PR #45 レビュー対応PR #45 [低-1] と [低-2]
- `9aca2ac` feat #42: date 基準補正を実際に走らせる（PR #45 [中-1] 対応）
