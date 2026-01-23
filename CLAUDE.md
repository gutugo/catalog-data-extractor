# Claude Code Project Guide

## Project Overview

Catalog Data Extractor - Extracts product data from PDF supplier catalogs using multiple extraction methods with a web-based verification UI.

## Key Directories

```
src/extractor/
  auto_extractor.py     # Multi-method extraction orchestration
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

## Extraction Methods

| Method | Confidence | Library | Best For |
|--------|------------|---------|----------|
| Camelot | 1.0 | camelot-py | Bordered tables (requires ghostscript) |
| Docling | 0.98 | docling | Complex/bordered tables, scanned docs |
| pdfplumber | 0.95 | pdfplumber | General tables (default) |
| img2table | 0.90 | img2table | Borderless tables |
| pymupdf4llm | 0.85 | pymupdf4llm | Layout-aware markdown text |
| pdfminer | 0.80 | pdfminer.six | Text layout analysis |
| Regex | 0.50 | built-in | Text pattern fallback |

### Extraction Modes

```python
# Standard (default) - pdfplumber tables → regex fallback
AutoExtractor(pdf_path, session_dir)

# Multi-method - all 6 methods, merge by confidence
AutoExtractor(pdf_path, session_dir, multi_method=True)

# Docling-only - for testing AI extraction
AutoExtractor(pdf_path, session_dir, docling_only=True)
```

## Docling (IBM AI Extraction)

### Model Cache
- Location: `~/.cache/huggingface/hub/`
- Models downloaded:
  - `docling-layout-heron` (164 MB) - Layout detection
  - `docling-models` (342 MB) - TableFormer table detection
- Total: ~506 MB (downloaded once, cached)

### Behavior
- Processes **entire PDF at once**, caches result in `PDFReader._docling_result`
- Filters tables by page number from cached result
- Progress shows 0% until full document is processed
- First run is slow (model download + full PDF conversion)

### Results Vary by Catalog Format
- OTC catalog with text tables: **1044 products** extracted
- Some catalogs: **0 products** (format not recognized)
- Works best with bordered/complex table structures

### Testing Docling
```bash
# Use test script
.venv/bin/python test_docling.py catalogs/file.pdf [page_num]

# Or direct Python
.venv/bin/python -c "
from extractor.pdf_reader import PDFReader
with PDFReader('catalogs/file.pdf') as reader:
    tables = reader.extract_tables_docling(1)
    print(f'Tables: {len(tables)}')
"
```

## Common Commands

```bash
# Start web UI (port 5001)
./start.sh
# or
uv run extractor web-verify --port 5001

# CLI extraction
uv run extractor auto catalogs/file.pdf
uv run extractor auto catalogs/file.pdf --multi-method

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name
```

## Web App

### Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/catalogs` | List all catalogs with status |
| POST | `/api/extract/<name>` | Start extraction (background) |
| GET | `/api/extract/<name>/status` | Check extraction progress |
| POST | `/api/switch/<name>` | Switch active catalog |
| GET | `/api/page/<num>` | Get page products |
| GET | `/api/page/<num>/image` | Get page as PNG |
| POST | `/api/save` | Save session |
| POST | `/api/export-csv` | Export to CSV |

### CSRF Protection
All POST endpoints require `X-CSRF-Token` header.

## Dependencies

**Core (always available):**
- pdfplumber, pymupdf, pdfminer.six, flask, rich, typer, pandas

**Optional (for multi-method):**
- camelot-py (requires system ghostscript)
- docling (downloads ~500MB AI models)
- img2table
- pymupdf4llm

Install optional:
```bash
uv pip install docling img2table pymupdf4llm
```

## Troubleshooting

### Docling freezes on first run
AI models downloading (~500MB). Check progress:
```bash
du -sh ~/.cache/huggingface/hub/models--docling-project*
```

### Progress stuck at 0%
Docling processes entire PDF before returning. Wait for completion.

### Empty extractions
Catalog format not recognized. Try different extraction mode:
```bash
# Standard mode (usually works best for OTC catalogs)
uv run extractor auto catalogs/file.pdf

# Or multi-method for complex documents
uv run extractor auto catalogs/file.pdf --multi-method
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
- `feature/multi-method-extraction` - New extraction methods (Docling, img2table, pymupdf4llm)
