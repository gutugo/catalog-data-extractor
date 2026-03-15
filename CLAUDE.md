# Claude Code Project Guide

## Project Overview

Catalog Data Extractor - Extracts product data from PDF supplier catalogs using smart automatic extraction with a web-based verification UI.

## Key Directories

```
src/extractor/
  auto_extractor.py     # Pipeline orchestrator + text fallback extraction
  patterns.py           # Regex patterns and confidence constants
  parsing_utils.py      # Shared parsing utilities (count/UOM, item validation)
  product_validation.py # False-positive filtering
  multicolumn.py        # Two-column OTC layout detection and parsing
  column_detection.py   # Column mapping and table-to-product extraction
  pdf_reader.py         # PDF reading and table extraction methods
  web_verifier.py       # Flask web UI (port 5001)
  data_model.py         # Product/Session data models
  cli.py                # CLI commands
  exporter.py           # CSV export
  extractor.py          # Interactive (manual) extraction workflow
  templates/            # HTML templates
tests/
  conftest.py           # Shared fixtures
  test_data_model.py    # Product, Session, FieldLocation, PageContent tests
  test_patterns.py      # Regex pattern tests
  test_parsing_utils.py # Parsing utility tests
  test_product_validation.py # Validation/filtering tests
  test_multicolumn.py   # Multi-column layout tests
  test_column_detection.py   # Column mapping and table extraction tests
  test_auto_extractor.py     # Pipeline orchestrator tests
  test_web_verifier.py       # Flask API endpoint tests
  test_cli.py                # CLI helper tests
  test_exporter.py           # CSV export tests
catalogs/               # Input PDF files
processed/
  sessions/             # Extraction sessions (.session.json)
  extractions/          # Output CSV files
```

## Module Architecture

The extraction logic is split into focused modules with a clear dependency DAG (no circular imports):

```
patterns.py              (no internal deps)
    ↑
parsing_utils.py         (imports patterns)
    ↑
product_validation.py    (imports patterns)
multicolumn.py           (imports patterns, parsing_utils, data_model)
column_detection.py      (imports patterns, parsing_utils, data_model)
    ↑
auto_extractor.py        (imports all above + pdf_reader, re-exports everything)
```

`auto_extractor.py` re-exports all public names from submodules for backward compatibility. Code that imports from `auto_extractor` (e.g., `from extractor.auto_extractor import extract_products_from_table`) continues to work unchanged.

## Extraction

The extractor automatically classifies PDFs and selects the best extraction methods based on document characteristics. No user configuration needed.

### How It Works

1. **PDF Classification** - Analyzes the PDF to detect:
   - `has_text`: Whether extractable text is present
   - `has_borders`: Whether tables have visible borders
   - `is_scanned`: Whether the PDF is scanned/image-based
   - `layout_type`: 'tabular', 'borderless', 'text-only', or 'mixed'

2. **Multi-Column Detection** - Samples first 15 pages for two-column OTC-style layouts:
   - Builds word x-coverage histogram to find vertical gaps (≥10pt, density ≤1)
   - Verifies OTC item codes (`[A-Z]\d{1,3}`) appear on both sides of the gap
   - If detected, multicolumn extraction runs first on every page (confidence 0.95)
   - Falls back to single-column parsing for half-filled pages (e.g., last product page)

3. **Smart Method Selection** - Based on classification (if multicolumn not detected):

| PDF Type | Methods Used |
|----------|--------------|
| Multi-column OTC | multicolumn (word-level) → fallback to table methods |
| Digital + Bordered | Camelot → pdfplumber → PyMuPDF → pdfminer |
| Digital + Borderless | img2table → pdfplumber → Docling → pymupdf4llm |
| Scanned | Docling → unstructured |
| Text-only | pymupdf4llm → pdfminer |

4. **Early Stopping** - Stops when a method finds products with confidence >= 0.85

5. **Fallback** - Merges results from all methods if no single method is sufficient, then tries regex as last resort

6. **Validation** - Filters out false positives (spec data mistaken for products)

### Available Methods (by confidence)

| Method | Confidence | Best For |
|--------|------------|----------|
| Camelot | 1.0 | Bordered tables (requires ghostscript) |
| Docling | 0.98 | Complex tables, scanned docs (IBM AI) |
| Multi-column | 0.95 | Two-column OTC catalogs with multi-line products |
| pdfplumber | 0.95 | General tables |
| PyMuPDF | 0.93 | Fast native table detection |
| Unstructured | 0.92 | Varied document layouts |
| img2table | 0.90 | Borderless tables |
| pymupdf4llm | 0.85 | Layout-aware markdown text |
| pdfminer | 0.80 | Text layout analysis |
| Regex | 0.50 | Text pattern fallback |

### Usage

```python
from extractor.auto_extractor import AutoExtractor

extractor = AutoExtractor(pdf_path, session_dir)
session = extractor.run()
```

## Web UI

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/catalogs` | List all catalogs with status |
| POST | `/api/extract/<name>` | Start extraction |
| GET | `/api/extract/<name>/status` | Check extraction progress |
| POST | `/api/switch/<name>` | Switch active catalog |
| GET | `/api/page/<num>` | Get page products |
| GET | `/api/page/<num>/image` | Get page as PNG |
| POST | `/api/save` | Save session |
| POST | `/api/export-csv` | Export to CSV |

### CSRF Protection
All POST endpoints require `X-CSRF-Token` header.

### Extract API Example

```javascript
fetch('/api/extract/catalog-name', {
    method: 'POST',
    headers: {
        'X-CSRF-Token': csrfToken,
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({})
});
```

## Column Detection

Defined in `column_detection.py`. Uses multi-signal approach for robust column mapping:

1. **Header patterns** - Matches column headers ("Item #", "Description", etc.)
2. **Content patterns** - Detects item_no, price, count patterns in cell data
3. **Column width heuristics** - Narrow columns often contain codes, wide columns contain descriptions
4. **Cross-row consistency** - Same pattern across multiple rows indicates field type

Key functions: `detect_column_mapping()`, `detect_columns_robust()`, `find_count_column()`, `extract_products_from_table()`

## Product Validation

Defined in `product_validation.py`. Filters out false positives from brochure-style catalogs that have specification tables instead of product listings. Rejects:

- **Measurements**: 75kg, 200cm, 10mm, dimensions (200x85cm)
- **Electrical specs**: 12V, 220V, 50Hz, IPX4 ratings
- **Standards codes**: BS 7177, EN 597-1, ISO 9001
- **Time/range values**: 10Minutes, 10-20
- **Pure alphabetic values**: Real SKUs almost always contain digits
- **Multi-word descriptions**: Text with spaces (unless combined identifiers like "UPC / SKU")

**Note:** This app is designed for **product listing catalogs** with SKUs, item numbers, prices, and quantities. Marketing brochures with product descriptions and spec tables will correctly return 0 products.

## Multi-Column Extraction

Defined in `multicolumn.py`. Handles two-column, multi-line product layouts (e.g., AETNA OTC catalogs) that break standard table extractors.

### How It Works

1. **Word-level extraction** — `PDFReader.extract_words()` gets each word with x/y position
2. **Gap detection** — Histogram of word x-coverage finds low-density vertical gaps (≥10pt wide, ≤1 word density) in the middle 25-75% of the page
3. **Layout verification** — Confirms OTC item codes (`[A-Z]\d{1,3}`) appear on both sides of the gap
4. **Column splitting** — Words assigned to left/right by center position relative to boundary
5. **Line reconstruction** — Words grouped into lines by y-proximity (±3pt tolerance)
6. **Product parsing** — Walks lines looking for the multi-line product pattern:
   - Line 1: `[Code: A1] [description words] [$Price]`
   - Line 2: `[Description continuation]` (optional)
   - Line 3: `[6-digit UPC] [description] [Size Unit]` (optional)

### Product Fields

- `item_no`: Combined as "A1 / 446761" (code + UPC via `combine_identifiers`)
- `product_name`: Cleaned description text
- `pkg` / `uom`: Parsed from size info (e.g., "8 OZ" → pkg="8", uom="oz")

### Key Functions

| Function | Module | Purpose |
|----------|--------|---------|
| `detect_column_gaps()` | `multicolumn.py` | Histogram-based vertical gap detection |
| `split_words_into_columns()` | `multicolumn.py` | Assign words to left/right columns |
| `reconstruct_lines_from_words()` | `multicolumn.py` | Group words into lines by y-position |
| `detect_multicolumn_layout()` | `multicolumn.py` | Verify two-column OTC layout |
| `parse_multicolumn_products()` | `multicolumn.py` | Parse multi-line products within a column |
| `AutoExtractor._try_multicolumn()` | `auto_extractor.py` | Pipeline method with single-column fallback |

## Patterns and Constants

Defined in `patterns.py`. All regex patterns and confidence constants live here as the single source of truth. Other modules import from this file. Key patterns:

- `ITEM_NO_PATTERN` — Validates item numbers (4-5 digit, alphanumeric, hyphenated)
- `UOM_UNITS` — Raw string of all recognized unit abbreviations
- `COUNT_UOM_PATTERN` — Parses "32 ct.", "100 pk" etc.
- `FALSE_POSITIVE_PATTERNS` — Detects spec data masquerading as products
- `OTC_*_PATTERN` — OTC catalog-specific patterns (item codes, SKUs, prices)
- `HEADER_PATTERNS`, `SKIP_PATTERNS` — Table header/footer detection

## Parsing Utilities

Defined in `parsing_utils.py`. Shared functions used by multiple extraction modules:

- `parse_count_uom(count_str)` — Parses "32 ct." → ("32", "ct")
- `is_valid_item_no(value)` — Validates item number format
- `clean_product_name(name)` — Normalizes whitespace
- `combine_identifiers(upc, sku, item_no)` — Joins with " / " separator
- `parse_markdown_tables(text)` — Extracts tables from markdown text

## Docling (IBM AI Extraction)

### Model Cache
- Location: `~/.cache/huggingface/hub/`
- Models: `docling-layout-heron` (164 MB), `docling-models` (342 MB)
- Total: ~506 MB (downloaded once, cached)

### Behavior
- Processes **entire PDF at once**, caches result
- First run is slow (model download + full PDF conversion)
- Progress shows 0% until full document is processed

## Testing

255 tests using pytest. Run with:

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=extractor --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_patterns.py -v

# Run a specific test class
uv run pytest tests/test_data_model.py::TestProduct -v
```

Test configuration is in `pyproject.toml` (`[tool.pytest.ini_options]`).

## Common Commands

```bash
# Start web UI (port 5001)
./start.sh
# or
uv run extractor web-verify --port 5001

# CLI extraction
uv run extractor auto catalogs/file.pdf

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name
```

## Dependencies

**Core (always available):**
- pdfplumber, pdfminer.six, flask, rich, typer, pandas, pymupdf, pymupdf4llm

**Optional (for better accuracy):**
- camelot-py (requires system ghostscript)
- docling (downloads ~500MB AI models)
- unstructured[pdf] (document understanding)
- img2table

**Dev:**
- pytest, pytest-cov

Install optional:
```bash
uv pip install docling img2table "unstructured[pdf]"
```

Install dev:
```bash
uv pip install pytest pytest-cov
```

## Troubleshooting

### Docling freezes on first run
AI models downloading (~500MB). Check progress:
```bash
du -sh ~/.cache/huggingface/hub/models--docling-project*
```

### Empty extractions
**If 0 products extracted:**
1. Check if it's a product listing catalog (has SKUs/item numbers) vs a brochure (just descriptions)
2. Brochures correctly return 0 products - they're not compatible with this tool
3. Check available methods - some require optional dependencies:
```bash
uv run python -c "from extractor.pdf_reader import *; print('Docling:', DOCLING_AVAILABLE); print('Camelot:', CAMELOT_AVAILABLE)"
```

### Re-extract a catalog
Delete session file and re-run:
```bash
rm processed/sessions/<catalog-name>.session.json
```

### Port already in use
```bash
uv run extractor web-verify --port 5002
```

## Git Branches

- `main` - Stable release
- `feature/multi-method-extraction` - Development branch
