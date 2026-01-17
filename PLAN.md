# Catalog Data Extractor - Implementation Plan

## Completed Features

### Phase 1: Table-Aware Auto-Extraction (Completed)

**Problem**: Original regex-based extraction had issues:
- Lost table column boundaries when extracting text line-by-line
- Multi-line product names got corrupted
- `pkg` and `uom` fields always empty

**Solution**: Rewrote `auto_extractor.py` to use pdfplumber's table extraction.

**Implementation**:
1. `extract_tables()` - Uses pdfplumber to identify table structures
2. `find_count_column()` - Dynamically detects which column contains count data (handles variable layouts)
3. `parse_count_uom()` - Parses count strings like "1,000 ct." → (pkg="1000", uom="ct")
4. `extract_products_from_table()` - Extracts products from table rows
5. `extract_products_from_text_fallback()` - Regex fallback for pages without tables

**Results**:
- 941 products extracted (vs 901 before)
- 99%+ of products have pkg/uom populated
- Clean multi-line product names

### Phase 2: Web Verification UI (Completed)

**Problem**: Terminal-based verification difficult to compare PDF content with extracted data.

**Solution**: Browser-based split-view UI with region selection.

**Implementation**:
1. `web_verifier.py` - Flask backend:
   - `/api/page/<n>/image` - Renders PDF page as PNG via PyMuPDF
   - `/api/page/<n>` - Returns products for page
   - `/api/product` - CRUD operations
   - `/api/extract-text` - Extracts text from selected region
   - `/api/save` - Persists session

2. `templates/verify.html` - Frontend:
   - Split view: PDF image (left) + products list (right)
   - Canvas overlay for drawing selection boxes
   - Zoom control (1x-4x)
   - Page navigation (arrows, direct input)
   - Product edit modal
   - Keyboard shortcuts (←/→ navigate, Esc clear)

3. CLI command: `uv run extractor web-verify <catalog-name>`

**Features**:
- Draw box on PDF → extracts text from region
- Click product → select for editing
- Edit/Add/Delete products
- Auto-save to session
- Responsive dark theme UI

---

## Future Enhancements

### Potential Improvements

1. **OCR Integration**
   - For scanned PDFs without text layer
   - Tesseract or cloud OCR API

2. **Field Auto-Detection**
   - ML model to identify field types
   - Learn from user corrections

3. **Batch Processing**
   - Process multiple catalogs in parallel
   - Progress dashboard

4. **Export Formats**
   - Excel export with formatting
   - JSON export for API integration

5. **Region Linking**
   - Save PDF regions with products
   - Visual highlighting of source

6. **Diff View**
   - Compare extractions between sessions
   - Track changes over time

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI (cli.py)                        │
├─────────────┬─────────────┬─────────────┬──────────────────┤
│    auto     │   verify    │ web-verify  │     export       │
├─────────────┼─────────────┼─────────────┼──────────────────┤
│ AutoExtract │  Verifier   │ WebVerifier │    Exporter      │
│   or.py     │    .py      │    .py      │      .py         │
├─────────────┴─────────────┴─────────────┴──────────────────┤
│                    PDFReader (pdf_reader.py)                │
│              pdfplumber (text/tables) + PyMuPDF (images)    │
├─────────────────────────────────────────────────────────────┤
│                  DataModel (data_model.py)                  │
│            Product, ExtractionSession, PageContent          │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

```
PDF Catalog
    │
    ▼
┌───────────────────┐
│  auto command     │
│  ───────────────  │
│  1. extract_tables│
│  2. find_count_col│
│  3. parse products│
│  4. save session  │
└─────────┬─────────┘
          │
          ▼
    Session JSON + CSV
          │
          ▼
┌───────────────────┐
│ web-verify command│
│ ────────────────  │
│ 1. Load session   │
│ 2. Serve PDF imgs │
│ 3. Edit products  │
│ 4. Save changes   │
└─────────┬─────────┘
          │
          ▼
    Updated CSV Export
```
