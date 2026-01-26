# Catalog Data Extractor

Extract product data from PDF supplier catalogs using smart automatic extraction with a web-based verification UI.

## Features

- **Smart automatic extraction** - Classifies PDFs and selects optimal extraction methods
- **Multi-method pipeline** - Tries multiple extraction methods with confidence scoring
- **Drag-and-drop upload** - Add PDF catalogs directly in the browser
- **Split-view verification** - PDF on left, extracted data on right
- **Field-by-field review** - Cycle through each field for quick verification
- **One-click CSV export** - Download results directly from the UI
- **Multi-catalog dashboard** - Manage multiple catalogs in one session

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone <repo-url>
cd catalogdataextractor
uv sync
```

### Optional Dependencies

For better extraction accuracy, install optional packages:

```bash
uv pip install docling img2table pymupdf4llm pymupdf "unstructured[pdf]"
```

For Camelot (bordered tables), install Ghostscript:

```bash
# macOS
brew install ghostscript

# Ubuntu/Debian
apt install ghostscript
```

## Quick Start

### 1. Launch the Web UI

```bash
./start.sh
```

Opens browser at http://127.0.0.1:5001

### 2. Upload a Catalog

- Drag and drop a PDF onto the upload zone, or click to browse
- Extraction starts automatically
- Progress shows in the sidebar

### 3. Verify Extracted Data

- Click a catalog in the sidebar to open it
- Use **Start Verification** to cycle through each field
- Edit products inline, add missing ones, delete errors
- Navigate pages with arrow keys or buttons

### 4. Export to CSV

Click **Update CSV** to export. Files save to `processed/extractions/`.

## How Extraction Works

### PDF Classification

The extractor automatically analyzes each PDF to detect:
- `has_text` - Whether extractable text is present
- `has_borders` - Whether tables have visible borders
- `is_scanned` - Whether the PDF is scanned/image-based
- `layout_type` - 'tabular', 'borderless', 'text-only', or 'mixed'

### Smart Method Selection

Based on classification, optimal methods are selected:

| PDF Type | Methods Used |
|----------|--------------|
| Digital + Bordered | Camelot → pdfplumber → PyMuPDF → pdfminer |
| Digital + Borderless | img2table → pdfplumber → Docling → pymupdf4llm |
| Scanned | Docling → unstructured |
| Text-only | pymupdf4llm → pdfminer |

### Available Methods

| Method | Confidence | Best For |
|--------|------------|----------|
| Camelot | 1.0 | Bordered tables (requires ghostscript) |
| Docling | 0.98 | Complex tables, scanned docs (IBM AI) |
| pdfplumber | 0.95 | General tables |
| PyMuPDF | 0.93 | Fast native table detection |
| Unstructured | 0.92 | Varied document layouts |
| img2table | 0.90 | Borderless tables |
| pymupdf4llm | 0.85 | Layout-aware markdown text |
| pdfminer | 0.80 | Text layout analysis |
| Regex | 0.50 | Text pattern fallback |

### Pipeline Behavior

1. **Early Stopping** - Stops when a method finds products with confidence >= 0.85
2. **Fallback** - Merges results from all methods if no single method is sufficient
3. **Validation** - Filters out false positives (spec data mistaken for products)

## Web UI Guide

### Dashboard

The sidebar shows all catalogs with status badges:
- **Not extracted** - PDF uploaded, needs extraction
- **Extracting...** - Extraction in progress
- **Ready** - Extracted, ready for verification
- **Exported** - CSV has been generated

### Verification Mode

Click **Start Verification** to enter field-by-field review:

| Key | Action |
|-----|--------|
| Enter | Confirm field, go to next |
| Tab | Skip field |
| ← / → | Previous / next field |
| Escape | Exit verification mode |

### Page Navigation

| Key | Action |
|-----|--------|
| ← / → | Previous / next page |

### Actions

- **Add Product** - Manually add a product to current page
- **Save** - Save session to disk
- **Update CSV** - Export to CSV file
- **Exit** - Close with unsaved changes check

## Output Format

CSV files contain:

| Column | Example |
|--------|---------|
| product_name | Toothpaste, Crest Sensi-Relief |
| description | 4.1 oz. |
| item_no | 5811 |
| pkg | 1 |
| uom | ct |
| page_number | 10 |
| source_file | catalog.pdf |

## CLI Commands

```bash
# Start web UI (port 5001)
./start.sh
# or
uv run extractor web-verify --port 5001

# Auto-extract without UI
uv run extractor auto catalogs/file.pdf

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name
```

## Troubleshooting

### Docling freezes on first run

AI models are downloading (~500MB). Check progress:
```bash
du -sh ~/.cache/huggingface/hub/models--docling-project*
```

### Empty extractions (0 products)

1. Check if it's a **product listing catalog** (has SKUs/item numbers) vs a **brochure** (just descriptions)
2. Brochures correctly return 0 products - they're not compatible with this tool
3. Check available methods:
```bash
uv run python -c "from extractor.pdf_reader import *; print('Docling:', DOCLING_AVAILABLE); print('Camelot:', CAMELOT_AVAILABLE)"
```

### Re-extract a catalog

Delete the session file and re-run:
```bash
rm processed/sessions/<catalog-name>.session.json
```

### Port already in use

```bash
uv run extractor web-verify --port 5002
```

## Project Structure

```
src/extractor/
  auto_extractor.py     # Smart pipeline extraction
  pdf_reader.py         # PDF reading and table extraction methods
  web_verifier.py       # Flask web UI (port 5001)
  data_model.py         # Product/Session data models
  cli.py                # CLI commands
  templates/            # HTML templates
catalogs/               # Input PDF files
processed/
  sessions/             # Extraction sessions (.session.json)
  extractions/          # Output CSV files
```

## License

MIT
