# Project: Medical Expense Deduction Calculator (`medical_exp_deduction_calc`)

This project is an automated OCR application designed to streamline the extraction and management of data from medical receipts for tax deduction purposes.

## Project Overview

The primary goal is to simplify data entry for medical expenses within a household. It follows a "Human-in-the-Loop" design, allowing the system to learn from user corrections to improve OCR accuracy over time, specifically for clinic-specific document layouts.

## Architecture & Technologies

- **Language**: Python (>=3.11)
- **OCR Engine**: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) (for text and coordinate extraction)
- **Image Processing**: OpenCV
- **LLM Components**:
    - **Structural LLM**: Extracted text from OCR is formatted into a structured JSON representation (date, amount, items, etc.).
    - **Naming Extractor LLM**: Identifies and extracts the clinic name from raw OCR text.
- **Data Management**:
    - **SQLite**: Local database (`data/db.sqlite3`) is used for storing receipts, clinic templates, coordinate correction offsets, user modifications, and template version history.

## Key Files & Directories

- `main.py`: Main entry point integrating the directory watcher, OCR pipeline, and LLM processing.
- `app/`: Contains core application logic.
    - `app/ocr_pipeline.py`: Logic for PaddleOCR-based text and coordinate extraction.
    - `app/image_preprocessing.py`: Image preprocessing utilities (CLAHE, adaptive thresholding).
    - `app/coord_normalizer.py`: Coordinate normalization, anchor-based relative normalization (`normalize_coordinates_by_anchor`), template date basis shifting (`shift_template_to_date_basis`), and topmost confidence checking (`get_topmost_confidence`).
    - `app/date_anchor.py`: Date anchor resolution and re-normalization orchestration using clinic template date coordinates.
    - `app/image_resize.py`: Image pre-processing utilities (resizing, scaling).
    - `app/watcher.py`: Directory watcher tracking incoming receipts and triggering processing.
    - `app/llm_extractor.py`: Handles interfacing with the LLM API for extraction.
    - `app/structural_parser.py`: Parses the LLM structural outputs.
    - `app/normalization.py`: Normalizes text, amounts, dates, and clinic names.
    - `app/error_logging.py`: Centralized error logging utility.
    - `app/services/`: Contains service-layer modules for handling business logic and cross-module interactions.
- `tasks/`:
    - `tasks/issue_4/run_e2e.py`: E2E test runner validating the extraction output.
- `tests/`: Contains Unit and E2E test suites.
- `要件定義書.md`: Core system requirements and functional specifications (Japanese).
- `pyproject.toml`: Project configuration and dependency specifications.

## Running the Application

Ensure your environment is set up (see `pyproject.toml`).

### Start the Directory Watcher:
```bash
python main.py
```

### Process an OCR JSON file directly (CLI Mode):
```bash
python main.py --input-json <path_to_ocr_json> --model <model_name>
```

### Run E2E Tests:
```bash
python tasks/issue_4/run_e2e.py
```

## Current Progress & Upcoming Tasks

### Implemented Features (as of 2026-10-08)
- Complete pipeline integration (OCR + LLM Extraction + Normalization + Automatic Parsing).
- **Manual Coordinate Basis Toggle & Admin (Issue #42)**:
  - Added Web administration interface (`GET /admin/templates` and `POST /admin/templates/{clinic_id}/basis`) to manually switch template `coord_basis` from `topmost` to `date` as a verification fallback.
  - Implemented `TemplateAdminService` (`app/services/template_admin_service.py`) for business logic encapsulation and safe transition validation (requires learned date box, records `manual_basis_toggle` in `template_history`).
  - Added shared template shifting helper `shift_template_to_date_basis` in `app/coord_normalizer.py` to ensure consistency between automatic and manual migrations.
- **Date-Based Coordinate Normalization (Issue #39)**:
  - Added `app/date_anchor.py` to resolve structured date to specific OCR text boxes using clinic template date coordinates as positional hints.
  - Implemented anchor-based coordinate re-normalization (`normalize_coordinates_by_anchor` in `app/coord_normalizer.py`) and template coordinate shifting (`shift_template_coords`).
  - Added `coord_basis` column (`'topmost'` or `'date'`) to `templates` table with idempotent migration (`app/db_migrations.py`).
  - Implemented transactional template basis migration (`update_template_basis`) preserving pre-migration snapshots in `template_history`.
  - Synchronized persisted DB OCR JSON coordinates (`update_receipt_ocr_json_by_source`) upon date-anchor re-normalization.
- **OCR Preprocessing & Retry (Issue #36)**:
  - Added image preprocessing utilities (CLAHE local contrast enhancement and adaptive thresholding in `app/image_preprocessing.py`).
  - Implemented configurable preprocessing modes (`--preprocess-mode`: `clahe`, `adaptive`, `clahe+adaptive`), force execution (`--preprocess-force`), and target short-side sizing (`--target-short-side`).
  - Added low-confidence conditional retry with preprocessed image generation (`processed/`) and raw JSON preservation (`*raw_data.preprocessed.json`).
- **Hybrid Template Key Matching (Issue #34)**:
  - Implemented 3-stage fallback for clinic template matching: (1) Exact Name Match, (2) Text Similarity (difflib, threshold 0.6), (3) Layout-based Matching (50px proximity, 60% match ratio).
- **Coordinate-based Extraction & Relative Normalization (Issue #32)**:
  - Implemented relative coordinate conversion (`normalize_coordinates`) to handle photography offsets.
  - Increased coordinate proximity threshold from 20px to **50px** for robust matching.
  - Added confidence-based gating: if top-element OCR confidence < 0.8, relative normalization is skipped and `low_confidence: true` is flagged in output.
  - Implemented template update gating: Web UI corrections for `low_confidence` receipts update the corrections table but skip template table updates.
- CLI integration for model and input JSON file configuration.
- Robust date, clinic name, and monetary value normalization.
- E2E testing framework with mockup validations.
- **Service Layer Refactoring & SRP**: Decoupled business logic into specialized services and repositories.

### Upcoming Tasks
- **Enhanced Coordinate Correction**: Further refine coordinate search and user feedback loop based on real-world usage.
- **Test Coverage Expansion**: Increase unit and integration test coverage for the service layer.

## Development Conventions

- **Type Safety**: Use type hints where appropriate.
- **Code Style**: Adhere to PEP 8 standards. Use `black` for formatting (line length 119).
- **Docstrings**: All functions should include descriptive docstrings detailing parameters and purpose.
- **Modularity**: Maintain clean separation between processing logic (OCR, resizing) and management logic (data persistence).
- **Security**: Never hardcode credentials, paths, or sensitive data. Use environment variables for configuration.
