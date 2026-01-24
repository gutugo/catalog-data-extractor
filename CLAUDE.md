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

9 extraction methods available, ordered by confidence score:

| Method | Confidence | Library | Best For |
|--------|------------|---------|----------|
| Camelot | 1.0 | camelot-py | Bordered tables (requires ghostscript) |
| Docling | 0.98 | docling | Complex/bordered tables, scanned docs (IBM AI) |
| pdfplumber | 0.95 | pdfplumber | General tables (default) |
| PyMuPDF | 0.93 | pymupdf | Fast native table detection |
| Unstructured | 0.92 | unstructured | Document understanding, varied layouts |
| img2table | 0.90 | img2table | Borderless tables |
| pymupdf4llm | 0.85 | pymupdf4llm | Layout-aware markdown text |
| pdfminer | 0.80 | pdfminer.six | Text layout analysis |
| Regex | 0.50 | built-in | Text pattern fallback |

**Note:** Confidence scores are estimates. Actual accuracy depends on PDF structure - benchmark on your catalogs and adjust as needed.

### Extraction Modes

```python
# Standard (default) - pdfplumber tables → regex fallback
AutoExtractor(pdf_path, session_dir)

# Pipeline - tries methods in order of confidence, stops when good results found
AutoExtractor(pdf_path, session_dir, pipeline=True)

# Multi-method - all 9 methods, merge by confidence
AutoExtractor(pdf_path, session_dir, multi_method=True)

# Single-method modes for testing
AutoExtractor(pdf_path, session_dir, docling_only=True)
AutoExtractor(pdf_path, session_dir, unstructured_only=True)
AutoExtractor(pdf_path, session_dir, pymupdf_only=True)
```

## Web UI

### Method Selector

The web UI includes a dropdown to select extraction method before uploading/extracting:

| Option | Description |
|--------|-------------|
| **Standard (pdfplumber)** | Default. General table extraction with regex fallback. |
| **Pipeline (smart)** | Tries methods in order of confidence, stops when good results found. Best balance of speed and accuracy. |
| **Multi-method (all)** | Runs all 9 methods and merges results by confidence. Most thorough but slower. |
| **Docling AI** | IBM AI-powered table detection using TableFormer. |
| **Unstructured.io** | Document understanding with hi-res strategy. |
| **PyMuPDF** | Fast native table detection. |

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/catalogs` | List all catalogs with status |
| POST | `/api/extract/<name>` | Start extraction (accepts `method` in JSON body) |
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
// Start extraction with specific method
fetch('/api/extract/catalog-name', {
    method: 'POST',
    headers: {
        'X-CSRF-Token': csrfToken,
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({ method: 'pipeline' })  // or 'standard', 'multi_method', etc.
});
```

## Pipeline Mode

The pipeline tries extraction methods in order of confidence and stops early when good results are found:

1. **Camelot** (1.0) - bordered tables
2. **Docling** (0.98) - AI-powered
3. **pdfplumber** (0.95) - general purpose
4. **PyMuPDF** (0.93) - fast native
5. **Unstructured** (0.92) - document understanding
6. **img2table** (0.90) - borderless tables
7. **pymupdf4llm** (0.85) - layout-aware text
8. **pdfminer** (0.80) - text layout
9. **Regex fallback** (0.50) - last resort

Stops when a method finds products with confidence >= 0.85. Falls back to merging all results if no single method is sufficient.

## Docling (IBM AI Extraction)

### Model Cache
- Location: `~/.cache/huggingface/hub/`
- Models: `docling-layout-heron` (164 MB), `docling-models` (342 MB)
- Total: ~506 MB (downloaded once, cached)

### Behavior
- Processes **entire PDF at once**, caches result
- First run is slow (model download + full PDF conversion)
- Progress shows 0% until full document is processed

## Common Commands

```bash
# Start web UI (port 5001)
./start.sh
# or
uv run extractor web-verify --port 5001

# CLI extraction
uv run extractor auto catalogs/file.pdf
uv run extractor auto catalogs/file.pdf --multi-method
uv run extractor auto catalogs/file.pdf --pipeline

# Check extraction status
uv run extractor status

# Export to CSV
uv run extractor export catalog-name
```

## Dependencies

**Core (always available):**
- pdfplumber, pdfminer.six, flask, rich, typer, pandas

**Optional (for multi-method/pipeline):**
- camelot-py (requires system ghostscript)
- docling (downloads ~500MB AI models)
- unstructured[pdf] (document understanding)
- pymupdf (fast native tables)
- img2table
- pymupdf4llm

Install optional:
```bash
uv pip install docling img2table pymupdf4llm pymupdf "unstructured[pdf]"
```

## Troubleshooting

### Docling freezes on first run
AI models downloading (~500MB). Check progress:
```bash
du -sh ~/.cache/huggingface/hub/models--docling-project*
```

### Empty extractions
Try different extraction mode - Pipeline mode is recommended:
```bash
uv run extractor auto catalogs/file.pdf --pipeline
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
- `feature/multi-method-extraction` - New extraction methods
