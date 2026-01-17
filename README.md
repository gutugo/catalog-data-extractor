# Catalog Data Extractor

A Python CLI tool for semi-automatic extraction of product data from PDF supplier/manufacturer catalogs.

## Features

- **Auto-extraction** - Table-aware extraction using pdfplumber with field position tracking
- **Multi-method extraction** - Optional `--multi` flag uses Camelot + pdfplumber + pdfminer.six for best accuracy
- **Web verification UI** - Browser-based split view with PDF and extracted data
- **Field-by-field verification** - Cycle through each field for quick review
- **One-click CSV export** - Update CSV directly from web UI
- **Unsaved changes detection** - Warns before leaving with unsaved work
- **Interactive mode** - Terminal-based line selection and field mapping
- **Session persistence** - Quit and resume anytime
- **CSV export** - Consistent column structure with pkg/uom parsing

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Ghostscript](https://ghostscript.com/) (optional, for `--multi` mode with Camelot)

## Installation

1. Clone or download this repository

2. Install uv (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Install dependencies:
   ```bash
   uv sync
   ```

4. (Optional) Install Ghostscript for multi-method extraction:
   ```bash
   # macOS
   brew install ghostscript

   # Ubuntu/Debian
   apt install ghostscript

   # Windows: Download from https://ghostscript.com/
   ```

## Quick Start (Recommended Workflow)

### 1. Auto-Extract Products

Run automatic extraction on a PDF catalog:

```bash
uv run extractor auto catalogs/CY2025-OTC-Catalog.pdf
```

This uses table-aware extraction to identify product rows and parse fields automatically.

For higher accuracy on complex PDFs, use multi-method extraction:

```bash
uv run extractor auto catalogs/CY2025-OTC-Catalog.pdf --multi
```

Multi-method mode uses Camelot, pdfplumber, and pdfminer.six, then merges results by confidence score.

### 2. Verify with Web UI

Launch the browser-based verification interface:

```bash
uv run extractor web-verify CY2025-OTC-Catalog
```

**Web UI Features:**
- **Split view**: PDF page on left, extracted products on right
- **Field verification mode**: Click "Start Verification" to cycle through each field
  - Edit field values inline, press Enter to confirm
  - Tab to skip fields, Escape to exit verification
- **Edit products**: Click any product to edit fields
- **Add/Delete**: Add new products or remove incorrect ones
- **Navigation**: Arrow keys or buttons to move between pages
- **Zoom**: Adjust PDF zoom level (1x-4x)
- **Save**: Persist changes to session
- **Update CSV**: Export to CSV directly from web UI
- **Exit**: Close session with unsaved changes check

**Keyboard Shortcuts:**
| Key | Action |
|-----|--------|
| Enter | Confirm field and go to next |
| Tab | Skip field |
| ← / → | Navigate pages (or fields in verification mode) |
| Escape | Exit verification mode |

### 3. Export to CSV

```bash
uv run extractor export CY2025-OTC-Catalog
```

## All Commands

### Auto-Extract (Recommended)
```bash
uv run extractor auto catalogs/<file>.pdf
uv run extractor auto catalogs/<file>.pdf --multi  # Higher accuracy, slower
```

### Web Verification UI
```bash
uv run extractor web-verify <catalog-name>
uv run extractor web-verify <catalog-name> --port 8080  # Custom port
```

### Terminal Verification
```bash
uv run extractor verify <catalog-name>
uv run extractor verify <catalog-name> --page 10  # Start at page 10
```

### View a PDF Page
```bash
uv run extractor view catalogs/<file>.pdf --page 10
```

### Manual Interactive Processing
```bash
uv run extractor process catalogs/<file>.pdf
```

### Check Status
```bash
uv run extractor status
```

### Resume Session
```bash
uv run extractor resume <catalog-name>
```

### Export to CSV
```bash
uv run extractor export <catalog-name>
uv run extractor export <catalog-name> --output ~/Desktop/products.csv
```

### Process All Catalogs
```bash
uv run extractor process-all catalogs/
```

## Output Format

CSV files are saved to `processed/extractions/` with these columns:

| Column | Description | Example |
|--------|-------------|---------|
| product_name | Full product name | Toothpaste, Crest® Sensi-Relief, 4.1 oz. |
| description | Size/quantity details | 1 ct. |
| item_no | SKU/catalog number | 5811 |
| pkg | Package quantity | 1 |
| uom | Unit of measure | ct |
| page_number | Source page in PDF | 10 |
| source_file | Original PDF filename | CY2025-OTC-Catalog.pdf |

## Directory Structure

```
catalogdataextractor/
├── catalogs/                   # Place PDF catalogs here
├── processed/
│   ├── sessions/               # Extraction progress (auto-saved)
│   └── extractions/            # Output CSV files
└── src/extractor/
    ├── cli.py                  # CLI entry point
    ├── auto_extractor.py       # Table-aware automatic extraction
    ├── web_verifier.py         # Flask web UI
    ├── verifier.py             # Terminal verification
    ├── pdf_reader.py           # PDF text/table extraction
    ├── data_model.py           # Data models
    ├── exporter.py             # CSV export
    └── templates/
        └── verify.html         # Web UI template
```

## How Auto-Extraction Works

### Standard Mode
1. **Table detection**: Uses pdfplumber's `find_tables()` to find structured tables with cell positions
2. **Column detection**: Dynamically identifies which column contains count data
3. **Field parsing**: Extracts item_no, product_name, and count from table rows
4. **Position tracking**: Stores bounding box coordinates for each extracted field
5. **Count parsing**: Parses "32 ct." into pkg=32, uom=ct
6. **Fallback**: Uses regex-based extraction for pages without tables (no position data)

### Multi-Method Mode (`--multi`)
Uses three extraction methods and merges results:
1. **Camelot** (confidence: 1.0) - High-accuracy table extraction using stream flavor
2. **pdfplumber** (confidence: 0.95) - Table extraction with cell positions
3. **pdfminer.six** (confidence: 0.8) - Text layout analysis with bounding boxes

Products are matched by item_no and merged:
- **product_name**: Longest non-empty value (captures full name)
- **Other fields**: Highest confidence source wins
- **field_locations**: Best confidence per field

## Tips

- Use `auto` first, then `web-verify` to review and correct
- Use "Start Verification" mode to quickly cycle through all fields
- The web UI shows the actual PDF page, making it easy to compare
- Use "Update CSV" button to export changes without leaving the web UI
- Sessions auto-save, so you can quit anytime and resume later
- Use "Exit" button to safely close with unsaved changes check
- Use `status` to see which catalogs are complete
