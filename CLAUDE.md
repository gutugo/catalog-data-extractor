# Claude Code Project Guide

## Project Overview

Catalog Data Extractor - Extracts product data from PDF supplier catalogs using smart automatic extraction with a web-based verification UI.

## Key Directories

```
src/extractor/
  auto_extractor.py     # GLM-OCR pipeline orchestrator + text fallback
  glm_ocr.py            # GLM-OCR client via Ollama HTTP API
  patterns.py           # Regex patterns and confidence constants
  parsing_utils.py      # Shared parsing utilities (count/UOM, item validation)
  product_validation.py # False-positive filtering
  multicolumn.py        # Two-column OTC layout detection and parsing
  column_detection.py   # Column mapping and table-to-product extraction
  pdf_reader.py         # PDF reading, table extraction, page rendering
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
  test_glm_ocr.py            # GLM-OCR client and integration tests
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

The extractor uses **GLM-OCR** (via Ollama) as the sole extraction method. Each page is rendered to a PNG image, sent to the GLM-OCR model for table recognition, and the returned markdown is parsed into Product objects.

### How It Works

1. **Page Rendering** — Each PDF page is rendered to a high-res PNG (144 DPI) using PyMuPDF
2. **GLM-OCR Recognition** — The image is sent to GLM-OCR via Ollama with a "Table Recognition:" prompt
3. **Markdown Parsing** — The model returns markdown tables which are parsed via `parse_markdown_tables()`
4. **Product Extraction** — Parsed tables are converted to Product objects via `extract_products_from_table()`
5. **Text Fallback** — If no tables are found in the markdown, regex-based text extraction is used
6. **Validation** — Filters out false positives (spec data mistaken for products)

### GLM-OCR Setup

GLM-OCR requires Ollama running with the `glm-ocr` model:

```bash
# Install model
ollama pull glm-ocr

# Verify
ollama list | grep glm-ocr
```

Configure the Ollama endpoint via environment variable:
```bash
# Default: localhost
export OLLAMA_HOST=http://localhost:11434

# Remote server (e.g., mini01)
export OLLAMA_HOST=http://mini01:11434

# Or use SSH tunnel
ssh -L 11434:localhost:11434 mini01
```

### GLM-OCR Model Details

- **Model**: GLM-OCR (0.9B params, MIT license)
- **Architecture**: CogViT encoder + GLM-0.5B decoder
- **Benchmark**: #1 on OmniDocBench V1.5 (94.62 score)
- **Speed**: ~1.86 pages/sec (GPU), ~0.67 images/sec
- **Confidence**: 0.97 (assigned to all extracted products)

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

**Runtime (required for extraction):**
- Ollama with `glm-ocr` model (see GLM-OCR Setup above)

**Dev:**
- pytest, pytest-cov

Install dev:
```bash
uv pip install pytest pytest-cov
```

## GLM-OCR Client

Defined in `glm_ocr.py`. Communicates with GLM-OCR via the Ollama HTTP API.

```python
from extractor.glm_ocr import GlmOcrClient

client = GlmOcrClient()  # uses OLLAMA_HOST env var or localhost:11434
client.check_availability()  # True if Ollama has glm-ocr model
markdown = client.recognize_table(png_bytes)  # "Table Recognition:" prompt
text = client.recognize_text(png_bytes)       # "Text Recognition:" prompt
```

- Uses `urllib.request` (no extra dependencies)
- Timeout: 120 seconds per request
- Raises `GlmOcrError` on connection/parsing failures

## Troubleshooting

### GLM-OCR not available
Ensure Ollama is running and has the model:
```bash
ollama list | grep glm-ocr
# If not listed:
ollama pull glm-ocr
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
