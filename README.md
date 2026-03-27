# Catalog Data Extractor

Extract product data from PDF supplier catalogs using GLM-OCR vision-based AI with a web-based verification UI.

## Features

- **GLM-OCR extraction** - Vision-based table recognition via Ollama, handles both structured and unstructured catalogs
- **Drag-and-drop upload** - Add PDF catalogs directly in the browser
- **Split-view verification** - PDF page on left, extracted data on right
- **Inline editing** - Edit, add, or delete products per page
- **Region text extraction** - Draw a box on the PDF to pull text from a specific area
- **Confidence scoring** - Highlights low-confidence extractions for review
- **One-click CSV export** - Download results directly from the UI
- **Multi-catalog dashboard** - Manage multiple catalogs in one session

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Ollama](https://ollama.com/) with the GLM-OCR model

## Installation

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone <repo-url>
cd catalogdataextractor
uv sync

# Install GLM-OCR model (Q8 quantization, 1.6 GB)
ollama pull glm-ocr:q8_0
```

## Quick Start

### 1. Launch the Web UI

```bash
./start.sh
```

Opens browser at http://localhost:5001

### 2. Upload a Catalog

- Drag and drop a PDF onto the upload zone, or click to browse
- Click **Extract** to start — progress shows in the sidebar

### 3. Verify Extracted Data

- Click a catalog in the sidebar to open it
- Edit products inline, add missing ones, delete false positives
- Navigate pages with arrow keys or buttons
- Use **Start Verification** to cycle through fields one by one

### 4. Export to CSV

Click **Update CSV** to export. Files save to `processed/extractions/`.

## How Extraction Works

Each PDF page goes through this pipeline:

1. **Page rendering** - Page rendered to PNG at 108 DPI (zoom=1.5) via PyMuPDF
2. **GLM-OCR recognition** - Image sent to GLM-OCR via Ollama with "Table Recognition:" prompt
3. **Table parsing** - Response parsed as HTML tables (`<table>` elements) or markdown tables (pipe-delimited)
4. **Product extraction** - Tables converted to products via column detection (header patterns, content patterns, width heuristics)
5. **Text fallback** - If no tables found, regex patterns extract products from raw OCR text
6. **Validation** - Filters out false positives (spec data, measurements, standards codes mistaken for products)
7. **Retry** - On Ollama 500 errors, retries up to 2 times with backoff (2s, 4s)

### GLM-OCR Model

- **Model**: [GLM-OCR](https://huggingface.co/ucaslcl/GOT-OCR2_0) (0.9B params, MIT license)
- **Architecture**: CogViT encoder + GLM-0.5B decoder
- **Benchmark**: #1 on OmniDocBench V1.5 (94.62 score)
- **Output**: HTML `<table>` elements
- **Default quantization**: Q8 (`glm-ocr:q8_0`, 1.6 GB)
- **Speed**: ~23 sec/page on Apple M4

### Performance

| Catalog | Pages | Products | Time (Q8, M4) |
|---------|-------|----------|---------------|
| OTC catalog (36 pages, structured tables) | 36 | 960 | ~14 min |
| OTC catalog (40 pages, structured tables) | 40 | 955 | ~15 min |
| Surgical instruments (264 pages, unstructured) | 264 | 71 | ~48 min |

## Web UI

### Dashboard

The sidebar shows all catalogs with status badges:
- **Not extracted** - PDF uploaded, needs extraction
- **Extracting...** - Extraction in progress with page counter
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
| ← / → | Previous / next page (when not in verification mode) |

## CLI Commands

```bash
# Start web UI (dashboard mode, port 5001)
./start.sh

# Start with a specific catalog
uv run extractor web-verify catalog-name

# Auto-extract from CLI
uv run extractor auto catalogs/file.pdf

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name

# View raw page text
uv run extractor view catalogs/file.pdf --page 3
```

## Output Format

CSV files contain:

| Column | Example |
|--------|---------|
| product_name | Toothpaste, Crest Sensi-Relief |
| description | 4.1 oz. |
| item_no | O98 / 100032 |
| pkg | 1 |
| uom | ct |
| page_number | 10 |
| source_file | catalog.pdf |

## Troubleshooting

### GLM-OCR not available

Ensure Ollama is running and has the model:
```bash
ollama list | grep glm-ocr
# If not listed:
ollama pull glm-ocr:q8_0
```

### Empty extractions (0 products)

1. Check if it's a **product listing catalog** (has SKUs/item numbers) vs a **brochure** (just descriptions)
2. Brochures correctly return 0 products - they don't contain structured product data
3. Check GLM-OCR connectivity:
```bash
uv run python -c "from extractor.glm_ocr import GlmOcrClient; print(GlmOcrClient().check_availability())"
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
  auto_extractor.py     # GLM-OCR pipeline orchestrator + text fallback
  glm_ocr.py            # GLM-OCR client via Ollama HTTP API
  column_detection.py   # Column mapping and table-to-product extraction
  parsing_utils.py      # HTML/markdown table parsing, count/UOM helpers
  patterns.py           # Regex patterns and confidence constants
  product_validation.py # False-positive filtering
  multicolumn.py        # Two-column layout detection
  pdf_reader.py         # PDF reading and page rendering
  web_verifier.py       # Flask web UI (port 5001)
  data_model.py         # Product/Session data models
  cli.py                # CLI commands
  exporter.py           # CSV export
  templates/            # HTML templates
catalogs/               # Input PDF files
processed/
  sessions/             # Extraction sessions (.session.json)
  extractions/          # Output CSV files
```

## License

MIT
