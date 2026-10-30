# Architecture Snapshot: 2026-10-27 (Issue #42 / PR #45)

## Purpose
Document the implementation of manual coordinate basis switching (`coord_basis`: `topmost` → `date`) via Web administration interface (Issue #42 / PR #45), providing a verification fallback when automatic date anchor resolution cannot determine a unique anchor.

## Overview
- **Manual Coordinate Basis Toggle**: Enables administrators to manually switch a clinic template's `coord_basis` from `topmost` to `date` via the Web UI (`GET /admin/templates` and `POST /admin/templates/{clinic_id}/basis`).
- **Shared Shift Calculation (`shift_template_to_date_basis`)**: Extracted template coordinate shifting logic into `app/coord_normalizer.py`, ensuring automatic migration (Issue #39) and manual toggle (Issue #42) use identical shift semantics.
- **Strict Validation Guards**: Enforces mandatory safety checks before migration: database must be configured (503), clinic must exist (404), template must have a learned date box (409), basis value must be valid (`date`, 400), and already-migrated templates result in an idempotent no-op (200).
- **Service-Oriented Architecture**: Encapsulated manual transition logic in `TemplateAdminService` (`app/services/template_admin_service.py`), keeping FastAPI routing in `server.py` thin and testable.

## Key Components Changed
- `app/coord_normalizer.py`: Added `shift_template_to_date_basis()` helper with numeric point validation (`_is_numeric_point`).
- `app/date_anchor.py`: Refactored automatic template migration to use the shared `shift_template_to_date_basis()`.
- `app/db.py` & `app/services/receipt_database_repository.py`: Added `get_clinic_by_id()` for precise 404 error distinction.
- `app/services/template_admin_service.py` (New): Implements `TemplateAdminService` business logic for template management and manual basis toggling with `manual_basis_toggle` change reason tracking.
- `app/web/server.py`: Added admin routes (`GET /admin/templates`, `POST /admin/templates/{clinic_id}/basis`).
- `app/web/templates/admin_templates.html` (New): Web UI template displaying clinic templates, basis state, date box learning status, and toggle buttons.
- `app/web/templates/index.html`: Added navigation link to template administration.
- `tests/test_coord_normalizer.py`, `tests/test_db.py`, `tests/test_web.py`: Added comprehensive unit and integration tests for shifting logic, clinic ID lookup, and web admin endpoints.

## Key Design Decisions
- **Shared Shift Semantics**: Both automatic date anchor resolution and manual admin toggle execute the exact same coordinate transformation (`shift_template_to_date_basis`), guaranteeing coordinate consistency across migration paths.
- **Mandatory Date Box Prerequisite**: Manual switching to `date` basis is strictly forbidden if the template has not yet learned a valid `date` box, preventing structural corruption of downstream anchor corrections.
- **Idempotent Transition & History Tracking**: Re-triggering a toggle on an already migrated template returns a safe no-op response without duplicating history entries. Successful transitions record pre-migration coordinates in `template_history` with `change_reason='manual_basis_toggle'`.
- **Database Dependency for Admin**: Management features require an active SQLite database connection (returning 503 when running in JSON-only fallback mode).

## Changed Files
- `app/coord_normalizer.py`
- `app/date_anchor.py`
- `app/db.py`
- `app/services/receipt_database_repository.py`
- `app/services/template_admin_service.py`
- `app/web/server.py`
- `app/web/templates/admin_templates.html`
- `app/web/templates/index.html`
- `tests/test_coord_normalizer.py`
- `tests/test_db.py`
- `tests/test_web.py`
- `tasks/issue_42/...` (Issue specifications and tasks)

## Commits (HEAD)
- `c9bf4ae` docs #261027: Issue #42 仕様と変更履歴
- `5a28ca8` docs #261027: Issue #42 今実装計画とタスク
- `c397ed7` docs #261027: code-review-graph 使用方法の変更について
