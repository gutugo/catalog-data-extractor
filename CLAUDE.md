# Catalog Data Extractor

Semi-automatic extraction of product data from PDF supplier/manufacturer catalogs.

## Project Structure

```
catalogdataextractor/
├── pyproject.toml              # Project config (uv/hatch)
├── catalogs/                   # Input PDF catalogs
├── processed/
│   ├── sessions/               # Extraction session state (JSON)
│   └── extractions/            # Output CSV files
└── src/extractor/
    ├── cli.py                  # Typer CLI entry point
    ├── pdf_reader.py           # PDF text/table extraction (pdfplumber, camelot, pdfminer.six)
    ├── data_model.py           # Product, ExtractionSession, PageContent, FieldLocation
    ├── auto_extractor.py       # Table-aware automatic extraction
    ├── extractor.py            # Interactive extraction workflow
    ├── verifier.py             # Terminal-based verification
    ├── web_verifier.py         # Flask web UI for verification
    ├── exporter.py             # CSV export (pandas)
    └── templates/
        └── verify.html         # Web verification UI template
```

## Commands

```bash
# Run any command
uv run extractor <command>

# Auto-extract products (recommended first step)
uv run extractor auto catalogs/<file>.pdf

# Auto-extract with multi-method pipeline (higher accuracy, slower)
uv run extractor auto catalogs/<file>.pdf --multi

# Web-based verification UI (opens browser)
uv run extractor web-verify <catalog-name>

# Terminal-based verification
uv run extractor verify <catalog-name>

# Process a catalog interactively (manual mode)
uv run extractor process catalogs/<file>.pdf

# View a specific page
uv run extractor view catalogs/<file>.pdf --page N

# Check extraction status
uv run extractor status

# Resume incomplete extraction
uv run extractor resume <catalog-name>

# Export to CSV
uv run extractor export <catalog-name>

# Delete a catalog (no UI - manual removal)
rm catalogs/<name>.pdf processed/sessions/<name>.session.json processed/extractions/<name>.csv
```

## Extraction Workflow

### Recommended: Auto + Web Verify

1. **Auto-extract**: `uv run extractor auto catalogs/file.pdf`
   - Uses table-aware extraction (pdfplumber tables)
   - Falls back to regex for pages without tables
   - Parses pkg/uom from count strings (e.g., "32 ct." → pkg=32, uom=ct)

2. **Web verify**: `uv run extractor web-verify catalog-name`
   - Opens browser with split-view UI
   - Left: PDF page viewer with zoom
   - Right: Extracted products with edit/delete/add
   - Field-by-field verification mode
   - Update CSV button exports directly from UI
   - Exit button with unsaved changes detection

## Data Model

Session files are stored as JSON in `processed/sessions/`. The `from_dict()` methods handle missing optional fields gracefully, while required fields (`source_file`, `total_pages`) raise clear KeyError messages if missing.

### cli.py
- `_validate_source_file_path()` - Validates PDF paths to prevent path traversal
  - Strips directory components from `source_file`
  - Verifies resolved path stays within allowed directory

### data_model.py
- Atomic session saves with temp file + rename
- Windows compatibility: fallback to unlink+rename if `os.replace()` fails
- Product IDs preserved on reload (generates new ID if `None` or empty string)
- Cleanup failures during atomic save are logged to stderr

### FieldLocation
Each extracted field can have an associated `FieldLocation` storing its source position on the PDF:
- `x0, y0, x1, y1` - Bounding box in PDF coordinates
- `page_number` - Source page
- `confidence` - 1.0 for table extraction, lower for text fallback

Products store field locations in `field_locations: dict[str, FieldLocation]` with keys like `item_no`, `product_name`, `description`, `pkg`, `uom`.

CSV output columns:
- `product_name` - Full product name
- `description` - Size/quantity details (e.g., "1 ct.", "32 pk.")
- `item_no` - SKU/catalog number (4-5 digits)
- `pkg` - Package quantity (parsed from description)
- `uom` - Unit of measure (ct, pk, pack, bx, oz, gm, ml, lb, qt, pt, bag, roll, pr, dz, set, btl, tube, jar, can, box, ea, sheets, pair, kit)
- `page_number` - Source page in PDF
- `source_file` - Original PDF filename

## Key Implementation Details

### pdf_reader.py
- `extract_tables()` - Basic table extraction (text only)
- `extract_tables_with_positions()` - Table extraction with cell bounding boxes
  - Returns list of table dicts with `rows` (list of cell dicts with `text` and `bbox`)
  - Uses pdfplumber's `table.rows` for cell positions and `table.extract()` for text
- `extract_tables_camelot()` - Table extraction using Camelot (stream flavor)
  - Higher accuracy for some PDFs, requires Ghostscript
  - Returns same format as `extract_tables_with_positions()` for compatibility
- `extract_text_with_layout()` - Text block extraction using pdfminer.six
  - Returns text blocks with bounding boxes and individual lines
  - Uses LAParams for layout analysis
- `ExtractionWarning` - Thread-safe class for tracking extraction failures programmatically
  - Uses `threading.Lock()` for concurrent access safety
  - `add(message)` - Record a warning
  - `get_all()` - Get all warnings (returns copy)
  - `clear()` - Clear warnings

### auto_extractor.py
- `find_count_column()` - Dynamically detects which table column contains count data
  - Requires ≥50% match rate for tables with 3+ rows
  - Requires 100% match rate for small tables (1-2 rows)
  - Works with both string lists and dict lists (with `text`/`bbox` keys)
- `is_header_row()` - Detects table header rows
  - Small rows (≤3 cells): requires majority to be header-like
  - Larger rows: requires at least 2 header cells
- `parse_count_uom()` - Parses "1,000 ct." → (pkg="1000", uom="ct")
- `extract_products_from_table()` - Table-aware extraction with field position capture
  - Creates `FieldLocation` objects for each extracted field from cell bboxes
- `extract_products_from_text_fallback()` - Regex fallback for non-table pages
  - Section header detection: skips ALL CAPS lines to avoid false positives
  - Multi-line pattern works with or without pending description
- UOM patterns are synchronized across all extraction methods (COUNT_UOM_PATTERN, PRODUCT_LINE_PATTERN, MULTILINE_ITEM_PATTERN, find_count_column)

#### Multi-Method Extraction (`--multi` flag)
When enabled, uses multiple extraction methods and merges results:
- `_extract_page_multi()` - Orchestrates extraction from all methods
- `_try_camelot()` - Camelot extraction (confidence: 1.0)
- `_try_pdfplumber_tables()` - pdfplumber extraction (confidence: 0.95)
- `_try_pdfminer_layout()` - pdfminer.six extraction (confidence: 0.8)
- `_merge_extractions()` - Merges products by item_no, picks best fields by confidence
- `_merge_product_variants()` - For same item_no: longest product_name, highest confidence for other fields

### web_verifier.py
- Flask app with API endpoints for page images, products CRUD
- `/api/page/<n>` - Get page data and products
- `/api/page/<n>/image` - Renders PDF page as PNG via PyMuPDF (zoom param: 1x-5x)
- `/api/product` - POST to add, PUT/DELETE with id to update/remove
- `/api/stats` - Get total product count and session info
- `/api/save` - Save session to disk
- `/api/export-csv` - Export session to CSV file
- `/api/shutdown` - Graceful server shutdown (uses SIGINT for safe cleanup)
- PDF document auto-cleanup on exit via atexit

#### Security & Resource Management
- **CSRF Protection**: All state-changing endpoints (POST/PUT/DELETE) require `X-CSRF-Token` header
  - Token generated at server start via `_generate_csrf_token()` using `secrets.token_urlsafe(32)`
  - Token passed to template and included in all fetch requests from JavaScript
  - `_check_csrf()` helper validates token on each protected endpoint
- **Input Sanitization**: `_sanitize_product_field()` truncates input to prevent resource exhaustion
  - `MAX_PRODUCT_NAME_LENGTH = 1000` characters
  - `MAX_FIELD_LENGTH = 10000` characters (description)
  - Item numbers, pkg, uom limited to 50-100 characters
- **Path Traversal Prevention**:
  - `_validate_catalog_name()` - Uses `secure_filename` for catalog names
  - `_validate_source_file_path()` in cli.py - Validates PDF paths stay within allowed directories
- `_cleanup_completed_jobs()` - Removes finished extraction jobs after 5 minutes
- Explicit pixmap memory cleanup after page rendering
- Thread-safe state access with `_state_lock` and `_extraction_lock`

### verify.html (Web Verification UI)
- **Header Actions**:
  - Start Verification - Enter field-by-field verification mode
  - Add Product - Add new product manually
  - Save - Save session to disk
  - Update CSV - Export to CSV file
  - Exit - Close with unsaved changes check
- **Verification Mode**: Cycles through fields (item_no → product_name → description → pkg → uom) for each product
- **Unsaved Changes**: Tracks modifications, warns before leaving page
- **Keyboard Shortcuts**:
  - `Enter` - Confirm field and advance
  - `Tab` - Skip field
  - `←/→` - Navigate pages (or previous/next field in verification mode)
  - `Escape` - Exit verification mode

## Dependencies

- **pdfplumber** - PDF text/table extraction (includes pdfminer.six)
- **camelot-py** - High-accuracy table extraction (requires Ghostscript)
- **pymupdf** - PDF page rendering to images
- **flask** - Web verification UI
- **rich** - Terminal formatting
- **typer** - CLI framework
- **pandas** - CSV export

### System Dependencies
- **Ghostscript** - Required for Camelot table extraction
  - macOS: `brew install ghostscript`
  - Ubuntu: `apt install ghostscript`
  - Windows: Download from https://ghostscript.com/

## Development

```bash
# Install dependencies
uv sync

# Run tests (when added)
uv run pytest
```
