# Catalog Data Extractor

Extract product data from PDF supplier catalogs with a web-based UI.

## Features

- **Drag-and-drop upload** - Add PDF catalogs directly in the browser
- **Automatic extraction** - Table-aware extraction with field position tracking
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

For automation or scripting:

```bash
# Auto-extract without UI
uv run extractor auto catalogs/file.pdf

# Multi-method extraction (higher accuracy)
uv run extractor auto catalogs/file.pdf --multi

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name
```

## Optional: Ghostscript

For `--multi` mode extraction (higher accuracy):

```bash
# macOS
brew install ghostscript

# Ubuntu/Debian
apt install ghostscript
```

## License

MIT
