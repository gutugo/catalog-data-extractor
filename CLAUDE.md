# Claude Code Project Guide

## Project Overview

Catalog Data Extractor - Extracts product data from PDF supplier catalogs using GLM-OCR (vision-based AI) with a web-based verification UI.

## Key Directories

```
src/extractor/
  auto_extractor.py     # GLM-OCR pipeline orchestrator + text fallback
  glm_ocr.py            # GLM-OCR client via Ollama HTTP API
  patterns.py           # Regex patterns and confidence constants
  parsing_utils.py      # Shared parsing utilities (count/UOM, item validation, HTML/MD table parsing)
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
glm_ocr.py               (no internal deps — standalone HTTP client)
    ↑
auto_extractor.py        (imports all above + pdf_reader, re-exports everything)
```

`auto_extractor.py` re-exports all public names from submodules for backward compatibility.

## Extraction

Uses **GLM-OCR** (via Ollama) as the sole extraction method. Each page is rendered to a PNG image, sent to GLM-OCR for table recognition, and the response is parsed into Product objects.

### How It Works

1. **Page Rendering** — Each PDF page rendered to PNG (108 DPI, zoom=1.5) using PyMuPDF
2. **GLM-OCR Recognition** — Image sent to GLM-OCR via Ollama with "Table Recognition:" prompt
3. **Response Parsing** — Model returns HTML tables (parsed via `parse_html_tables()`) or markdown tables (parsed via `parse_markdown_tables()`)
4. **Product Extraction** — Parsed tables converted to Product objects via `extract_products_from_table()`
5. **Text Fallback** — If no tables found, regex-based text extraction on raw OCR output
6. **Validation** — Filters out false positives (spec data mistaken for products)
7. **Retry** — On Ollama 500 errors, retries up to 2 times with backoff (2s, 4s)

### GLM-OCR Setup

Runs on mini01 with Ollama. Default model is `glm-ocr:q8_0`.

```bash
# Install model (Q8 — default, 1.6 GB)
ollama pull glm-ocr:q8_0

# Verify
ollama list | grep glm-ocr
```

### GLM-OCR Model Details

- **Model**: GLM-OCR (0.9B params, MIT license)
- **Architecture**: CogViT encoder + GLM-0.5B decoder
- **Benchmark**: #1 on OmniDocBench V1.5 (94.62 score)
- **Output format**: HTML `<table>` elements (not markdown as documented)
- **Context length**: 131,072 tokens
- **Default quantization**: Q8 (`glm-ocr:q8_0`, 1.6 GB, ~23 sec/page on M4)

### Performance Benchmarks (tested on mini01 Mac Mini M4)

| Catalog | Pages | Products | Time (F16) | Time (Q8) |
|---------|-------|----------|------------|-----------|
| 2026-MSHO OTC | 36 | 960 | 17:20 | 13:35 |
| AETNA OTC | 40 | 955 | ~15:00 | — |
| Vascular Surgery | 264 | 71 | — | 47:43 |

**Bottleneck is model inference** (~23-29 sec/page), not network. Running on mini01 directly vs remote SSH tunnel makes minimal difference.

### GLM-OCR vs Old Pipeline Comparison

**Structured catalogs (OTC with tables):**

| Metric | Old (pdfplumber) | GLM-OCR |
|--------|------------------|---------|
| Products | 960 | 960 |
| Item format | SKU only (`100032`) | Catalog # + SKU (`O98 / 100032`) |
| SKU overlap | — | 99.3% match |
| Speed | ~30 sec | ~14 min |

**Unstructured catalogs (surgical instruments):**

| Metric | Old (regex fallback) | GLM-OCR |
|--------|---------------------|---------|
| Products | 93 (87 garbage) | 71 (all real) |
| Quality | Page headers as products | Valid catalog codes (tk*) |
| Speed | 2.5 min | 48 min |

**Summary**: GLM-OCR matches old pipeline on structured PDFs and dramatically outperforms on unstructured/visual catalogs. Trade-off is speed.

### Usage

```python
from extractor.auto_extractor import AutoExtractor

extractor = AutoExtractor(pdf_path, session_dir)
session = extractor.run()  # uses glm-ocr:q8_0 by default
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

## Column Detection

Defined in `column_detection.py`. Uses multi-signal approach for robust column mapping:

1. **Header patterns** - Matches column headers ("Item #", "Description", etc.)
2. **Content patterns** - Detects item_no, price, count patterns in cell data
3. **Column width heuristics** - Narrow columns often contain codes, wide columns contain descriptions
4. **Cross-row consistency** - Same pattern across multiple rows indicates field type

Key functions: `detect_column_mapping()`, `detect_columns_robust()`, `find_count_column()`, `extract_products_from_table()`

## Product Validation

Defined in `product_validation.py`. Filters out false positives from brochure-style catalogs that have specification tables instead of product listings. Rejects measurements, electrical specs, standards codes, time/range values, pure alphabetic values, multi-word descriptions.

## Parsing Utilities

Defined in `parsing_utils.py`. Shared functions used by multiple extraction modules:

- `parse_count_uom(count_str)` — Parses "32 ct." → ("32", "ct")
- `is_valid_item_no(value)` — Validates item number format
- `clean_product_name(name)` — Normalizes whitespace
- `combine_identifiers(upc, sku, item_no)` — Joins with " / " separator
- `parse_markdown_tables(text)` — Extracts tables from pipe-delimited markdown
- `parse_html_tables(html)` — Extracts tables from HTML `<table>` elements (GLM-OCR output)

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
- Retries: 2 retries with backoff on 500 errors
- Raises `GlmOcrError` on connection/parsing failures

## Testing

273 tests using pytest. Run with:

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=extractor --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_patterns.py -v
```

Test configuration is in `pyproject.toml` (`[tool.pytest.ini_options]`).

## Common Commands

All commands run on mini01 (`~/catalogdataextractor/`).

```bash
# Start web UI (binds 0.0.0.0:5001, accessible from LAN)
./start.sh

# CLI extraction
uv run extractor auto catalogs/file.pdf

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name
```

## Server Deployment (mini01)

Project runs on mini01 at `~/catalogdataextractor/`. Web UI accessible at `http://mini01:5001`.

```bash
# Sync code to mini01
rsync -avz --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
  /Users/sk/catalogdataextractor/ mini01:~/catalogdataextractor/

# Install deps on mini01
ssh mini01 "cd ~/catalogdataextractor && uv sync"
```

## Dependencies

**Core (always available):**
- pdfplumber, pdfminer.six, flask, rich, typer, pandas, pymupdf, pymupdf4llm

**Runtime (required for extraction):**
- Ollama with `glm-ocr:q8_0` model on mini01 (see GLM-OCR Setup above)

**Dev:**
- pytest, pytest-cov

## Troubleshooting

### GLM-OCR not available
Ensure Ollama is running on mini01 and has the model:
```bash
ollama list | grep glm-ocr
# If not listed:
ollama pull glm-ocr:q8_0
```

### Ollama 500 errors
The model may return HTTP 500 when overloaded. The client retries automatically (2 retries with 2s/4s backoff). If persistent, check Ollama logs:
```bash
journalctl -u ollama --tail 20
```

### Empty extractions
**If 0 products extracted:**
1. Check if it's a product listing catalog (has SKUs/item numbers) vs a brochure
2. Visual/image-heavy catalogs may have few parseable tables
3. Check GLM-OCR connectivity: `uv run python -c "from extractor.glm_ocr import GlmOcrClient; print(GlmOcrClient().check_availability())"`

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
