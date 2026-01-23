"""PDF text extraction using pdfplumber, camelot, and pdfminer.six."""

from itertools import zip_longest
from pathlib import Path
import sys
import threading
from typing import Iterator, Optional

import pdfplumber
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextBoxHorizontal, LTTextLineHorizontal

from .data_model import PageContent


class ExtractionWarning:
    """Tracks extraction warnings for diagnostic purposes.

    Thread-safe implementation using a lock for concurrent access.
    """
    _warnings: list[str] = []
    _lock = threading.Lock()

    @classmethod
    def add(cls, message: str):
        """Add a warning message (thread-safe)."""
        with cls._lock:
            cls._warnings.append(message)

    @classmethod
    def get_all(cls) -> list[str]:
        """Get all warning messages (thread-safe)."""
        with cls._lock:
            return cls._warnings.copy()

    @classmethod
    def clear(cls):
        """Clear all warnings (thread-safe)."""
        with cls._lock:
            cls._warnings.clear()

# Camelot is optional - only imported when needed
try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False

# Docling is optional - AI-powered table extraction
try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False

# img2table is optional - borderless table detection
try:
    from img2table.document import PDF as Img2TablePDF
    IMG2TABLE_AVAILABLE = True
except ImportError:
    IMG2TABLE_AVAILABLE = False

# pymupdf4llm is optional - fast layout-aware markdown extraction
try:
    import pymupdf4llm
    PYMUPDF4LLM_AVAILABLE = True
except ImportError:
    PYMUPDF4LLM_AVAILABLE = False


class PDFReader:
    """Handles PDF text extraction with positional data."""

    def __init__(self, pdf_path: Path):
        self.pdf_path = Path(pdf_path)
        self._pdf: Optional[pdfplumber.PDF] = None
        self._docling_result = None  # Cache for Docling conversion (expensive)

    def __enter__(self) -> "PDFReader":
        self._pdf = pdfplumber.open(self.pdf_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._pdf:
            self._pdf.close()

    @property
    def total_pages(self) -> int:
        """Return total number of pages in the PDF."""
        if not self._pdf:
            raise RuntimeError("PDF not opened. Use context manager.")
        return len(self._pdf.pages)

    def get_page(self, page_number: int) -> PageContent:
        """Extract content from a specific page (1-indexed).

        Returns empty PageContent if extraction fails (e.g., encrypted page).
        """
        if not self._pdf:
            raise RuntimeError("PDF not opened. Use context manager.")

        if page_number < 1 or page_number > self.total_pages:
            raise ValueError(f"Page {page_number} out of range (1-{self.total_pages})")

        page = self._pdf.pages[page_number - 1]

        try:
            raw_text = page.extract_text() or ""
        except Exception as e:
            # Handle encrypted pages, malformed content, etc.
            warning_msg = f"Failed to extract text from page {page_number}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            raw_text = ""

        # Split into lines and clean up
        lines = []
        for line in raw_text.split("\n"):
            cleaned = line.strip()
            if cleaned:
                lines.append(cleaned)

        return PageContent(
            page_number=page_number,
            lines=lines,
            raw_text=raw_text,
        )

    def iter_pages(self, start_page: int = 1) -> Iterator[PageContent]:
        """Iterate through pages starting from a given page."""
        for page_num in range(start_page, self.total_pages + 1):
            yield self.get_page(page_num)

    def extract_tables(self, page_number: int) -> list[list[list[str]]]:
        """Extract tables from a specific page (1-indexed).

        Returns empty list if extraction fails.
        """
        if not self._pdf:
            raise RuntimeError("PDF not opened. Use context manager.")

        if page_number < 1 or page_number > self.total_pages:
            raise ValueError(f"Page {page_number} out of range (1-{self.total_pages})")

        page = self._pdf.pages[page_number - 1]

        try:
            tables = page.extract_tables() or []
        except Exception as e:
            # Handle extraction failures gracefully
            warning_msg = f"Failed to extract tables from page {page_number}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            return []

        # Clean up table cells
        cleaned_tables = []
        for table in tables:
            cleaned_table = []
            for row in table:
                cleaned_row = [cell.strip() if cell else "" for cell in row]
                if any(cleaned_row):  # Skip empty rows
                    cleaned_table.append(cleaned_row)
            if cleaned_table:
                cleaned_tables.append(cleaned_table)

        return cleaned_tables

    def extract_tables_with_positions(self, page_number: int) -> list[dict]:
        """Extract tables with cell bounding boxes for each cell.

        Returns a list of table dicts, each containing:
        - 'rows': list of rows, each row is a list of cell dicts with 'text' and 'bbox'
        - 'bbox': bounding box of entire table

        Args:
            page_number: 1-indexed page number

        Returns:
            List of table dicts with position data
        """
        if not self._pdf:
            raise RuntimeError("PDF not opened. Use context manager.")

        if page_number < 1 or page_number > self.total_pages:
            raise ValueError(f"Page {page_number} out of range (1-{self.total_pages})")

        page = self._pdf.pages[page_number - 1]

        try:
            tables = page.find_tables()
        except Exception as e:
            warning_msg = f"Failed to find tables on page {page_number}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            return []

        result = []
        for table in tables:
            table_data = {
                'bbox': table.bbox,  # (x0, y0, x1, y1)
                'rows': []
            }

            # Get extracted text for all rows
            extracted_rows = table.extract()

            # Validate row counts match
            if len(table.rows) != len(extracted_rows):
                print(
                    f"Warning: Row count mismatch on page {page_number}: "
                    f"{len(table.rows)} row objects vs {len(extracted_rows)} text rows",
                    file=sys.stderr
                )

            # Combine row bboxes with extracted text
            # Use zip_longest to handle potential mismatches without data loss
            for row_obj, row_text in zip_longest(table.rows, extracted_rows, fillvalue=None):
                row_data = []
                # Handle case where row_obj or row_text is None due to mismatch
                cells = row_obj.cells if row_obj else []
                texts = row_text if row_text else []

                # Validate cell counts match
                if cells and texts and len(cells) != len(texts):
                    print(
                        f"Warning: Cell count mismatch on page {page_number}: "
                        f"{len(cells)} cell bboxes vs {len(texts)} text cells",
                        file=sys.stderr
                    )

                # row_obj.cells contains bboxes for each cell in the row
                # row_text contains the extracted text for each cell
                for cell_bbox, cell_text in zip_longest(cells, texts, fillvalue=None):
                    row_data.append({
                        'text': cell_text.strip() if cell_text else '',
                        'bbox': cell_bbox  # (x0, y0, x1, y1) or None
                    })
                table_data['rows'].append(row_data)

            result.append(table_data)

        return result

    def extract_tables_camelot(self, page_number: int) -> list[dict]:
        """Extract tables using Camelot (higher accuracy for some PDFs).

        Returns same format as extract_tables_with_positions() for compatibility:
        - 'rows': list of rows, each row is a list of cell dicts with 'text' and 'bbox'
        - 'bbox': bounding box of entire table

        Args:
            page_number: 1-indexed page number

        Returns:
            List of table dicts with position data, or empty list if Camelot unavailable
        """
        if not CAMELOT_AVAILABLE:
            return []

        try:
            # Use stream flavor for tables without visible borders
            tables = camelot.read_pdf(
                str(self.pdf_path),
                pages=str(page_number),
                flavor='stream'
            )
        except Exception as e:
            warning_msg = f"Camelot failed on page {page_number}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            return []

        result = []
        for table in tables:
            # Get the table's bounding box from Camelot
            # Camelot stores bbox as (x0, y0, x1, y1) in _bbox attribute
            table_bbox = getattr(table, '_bbox', None)

            # Convert DataFrame to our dict format
            df = table.df
            if df.empty:
                continue

            table_data = {
                'bbox': table_bbox,
                'rows': []
            }

            # Get cell positions from Camelot's cells attribute if available
            # cells is a list of lists containing cell objects
            cell_positions = getattr(table, 'cells', None)

            for row_idx, row in df.iterrows():
                row_data = []
                for col_idx, cell_text in enumerate(row):
                    cell_bbox = None
                    # Try to get cell bbox from Camelot
                    if cell_positions and row_idx < len(cell_positions):
                        row_cells = cell_positions[row_idx]
                        if col_idx < len(row_cells):
                            cell = row_cells[col_idx]
                            # Check all four bbox attributes exist before using them
                            if (hasattr(cell, 'x1') and hasattr(cell, 'y1') and
                                    hasattr(cell, 'x2') and hasattr(cell, 'y2')):
                                cell_bbox = (cell.x1, cell.y1, cell.x2, cell.y2)

                    row_data.append({
                        'text': str(cell_text).strip() if cell_text else '',
                        'bbox': cell_bbox
                    })
                table_data['rows'].append(row_data)

            result.append(table_data)

        return result

    def extract_text_with_layout(self, page_number: int) -> list[dict]:
        """Extract text blocks with positions using pdfminer.six.

        Returns list of text blocks with bounding boxes:
        [{'text': str, 'bbox': (x0, y0, x1, y1), 'lines': [...]}, ...]

        Args:
            page_number: 1-indexed page number

        Returns:
            List of text block dicts with position data
        """
        laparams = LAParams(
            line_margin=0.3,       # Tighter line grouping for better row detection
            word_margin=0.15,      # Closer word grouping for column separation
            char_margin=2.0,
            boxes_flow=0.7,        # Stronger column separation for multi-column layouts
            detect_vertical=True,  # Enable vertical text detection
        )

        result = []

        try:
            # extract_pages yields page layouts
            for _page_idx, page_layout in enumerate(extract_pages(
                str(self.pdf_path),
                laparams=laparams,
                page_numbers=[page_number - 1]  # 0-indexed
            )):
                # Iterate through elements on the page
                for element in page_layout:
                    if isinstance(element, LTTextBoxHorizontal):
                        text = element.get_text().strip()
                        if text:
                            # Get individual lines within the text box
                            lines = []
                            for line in element:
                                if isinstance(line, LTTextLineHorizontal):
                                    line_text = line.get_text().strip()
                                    if line_text:
                                        lines.append({
                                            'text': line_text,
                                            'bbox': (line.x0, line.y0, line.x1, line.y1)
                                        })

                            result.append({
                                'text': text,
                                'bbox': (element.x0, element.y0, element.x1, element.y1),
                                'lines': lines
                            })
        except Exception as e:
            warning_msg = f"pdfminer failed on page {page_number}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)

        return result

    def extract_tables_docling(self, page_num: int) -> list[dict]:
        """Extract tables using Docling (IBM) - AI-powered table detection.

        Docling uses TableFormer AI for high-accuracy table structure recognition.
        Particularly effective for borderless and complex tables.

        Returns same format as extract_tables_with_positions():
        - 'rows': list of rows, each row is a list of cell dicts with 'text' and 'bbox'
        - 'bbox': bounding box of entire table

        Args:
            page_num: 1-indexed page number

        Returns:
            List of table dicts with position data, or empty list if Docling unavailable
        """
        if not DOCLING_AVAILABLE:
            return []

        try:
            # Cache Docling conversion result (expensive operation)
            if self._docling_result is None:
                converter = DocumentConverter()
                self._docling_result = converter.convert(str(self.pdf_path))

            result = self._docling_result
            tables = []

            # Docling uses iterate_items() to access document elements
            if hasattr(result, 'document') and hasattr(result.document, 'iterate_items'):
                for item, _level in result.document.iterate_items():
                    # Filter for table items only
                    if not hasattr(item, 'label') or item.label != 'table':
                        continue

                    # Filter by page number using provenance info
                    # prov is a list of ProvenanceItem with page_no, bbox
                    if hasattr(item, 'prov') and item.prov:
                        item_page = item.prov[0].page_no  # 1-indexed in Docling
                        if item_page != page_num:
                            continue
                        # Get bbox from provenance
                        prov_bbox = item.prov[0].bbox
                        table_bbox = (prov_bbox.l, prov_bbox.t, prov_bbox.r, prov_bbox.b) if prov_bbox else None
                    else:
                        table_bbox = None

                    table_data = {
                        'bbox': table_bbox,
                        'rows': []
                    }

                    # Try export_to_dataframe first (most reliable)
                    if hasattr(item, 'export_to_dataframe'):
                        try:
                            # Pass doc argument to avoid deprecation warning
                            df = item.export_to_dataframe(doc=result.document)
                            for _, row in df.iterrows():
                                row_data = []
                                for cell in row:
                                    cell_text = str(cell) if cell is not None else ''
                                    row_data.append({
                                        'text': cell_text.strip(),
                                        'bbox': None
                                    })
                                if row_data:
                                    table_data['rows'].append(row_data)
                        except Exception:
                            pass  # Fall through to data.table_cells

                    # Fallback: access data.table_cells directly
                    if not table_data['rows'] and hasattr(item, 'data') and item.data:
                        data = item.data
                        if hasattr(data, 'table_cells') and data.table_cells:
                            # Build rows from table_cells
                            # table_cells is a list of TableCell with row_span, col_span, text, etc.
                            num_rows = getattr(data, 'num_rows', 0)
                            num_cols = getattr(data, 'num_cols', 0)
                            if num_rows and num_cols:
                                # Initialize empty grid
                                grid = [['' for _ in range(num_cols)] for _ in range(num_rows)]
                                for cell in data.table_cells:
                                    if hasattr(cell, 'start_row_offset_idx') and hasattr(cell, 'start_col_offset_idx'):
                                        r, c = cell.start_row_offset_idx, cell.start_col_offset_idx
                                        if 0 <= r < num_rows and 0 <= c < num_cols:
                                            cell_text = getattr(cell, 'text', '') or ''
                                            grid[r][c] = cell_text
                                # Convert grid to our format
                                for row in grid:
                                    row_data = [{'text': cell.strip(), 'bbox': None} for cell in row]
                                    if any(cell['text'] for cell in row_data):
                                        table_data['rows'].append(row_data)

                    if table_data['rows']:
                        tables.append(table_data)

            return tables

        except Exception as e:
            warning_msg = f"Docling failed on page {page_num}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            return []

    def extract_tables_img2table(self, page_num: int) -> list[dict]:
        """Extract tables using img2table - specialized for borderless tables.

        img2table uses whitespace and proximity analysis for column detection,
        making it effective for tables without visible borders.

        Returns same format as extract_tables_with_positions():
        - 'rows': list of rows, each row is a list of cell dicts with 'text' and 'bbox'
        - 'bbox': bounding box of entire table

        Args:
            page_num: 1-indexed page number

        Returns:
            List of table dicts with position data, or empty list if img2table unavailable
        """
        if not IMG2TABLE_AVAILABLE:
            return []

        try:
            # img2table uses 0-indexed pages
            doc = Img2TablePDF(str(self.pdf_path), pages=[page_num - 1])

            # Extract tables with borderless detection enabled
            extracted = doc.extract_tables(borderless_tables=True)

            tables = []
            # extracted is a dict mapping page index to list of tables
            page_tables = extracted.get(page_num - 1, [])

            for table in page_tables:
                # Convert BBox object to tuple (x1, y1, x2, y2)
                table_bbox = None
                if hasattr(table, 'bbox') and table.bbox is not None:
                    bbox = table.bbox
                    table_bbox = (bbox.x1, bbox.y1, bbox.x2, bbox.y2)

                table_data = {
                    'bbox': table_bbox,
                    'rows': []
                }

                # img2table provides content as a DataFrame or list
                if hasattr(table, 'df') and table.df is not None:
                    df = table.df
                    for _, row in df.iterrows():
                        row_data = []
                        for cell in row:
                            cell_text = str(cell) if cell is not None else ''
                            row_data.append({
                                'text': cell_text.strip(),
                                'bbox': None
                            })
                        if row_data:
                            table_data['rows'].append(row_data)
                elif hasattr(table, 'content') and table.content:
                    for row in table.content:
                        row_data = []
                        for cell in row:
                            cell_text = str(cell) if cell is not None else ''
                            row_data.append({
                                'text': cell_text.strip(),
                                'bbox': None
                            })
                        if row_data:
                            table_data['rows'].append(row_data)

                if table_data['rows']:
                    tables.append(table_data)

            return tables

        except Exception as e:
            warning_msg = f"img2table failed on page {page_num}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            return []

    def extract_text_pymupdf4llm(self, page_num: int) -> str:
        """Extract text using pymupdf4llm - fast layout-aware markdown extraction.

        pymupdf4llm produces clean markdown from PDFs with better layout
        preservation than raw text extraction. Very fast (0.12s benchmark).

        Args:
            page_num: 1-indexed page number

        Returns:
            Markdown-formatted text from the page, or empty string if unavailable
        """
        if not PYMUPDF4LLM_AVAILABLE:
            return ""

        try:
            # pymupdf4llm uses 0-indexed pages
            markdown_text = pymupdf4llm.to_markdown(
                str(self.pdf_path),
                pages=[page_num - 1]
            )
            return markdown_text

        except Exception as e:
            warning_msg = f"pymupdf4llm failed on page {page_num}: {e}"
            print(f"Warning: {warning_msg}", file=sys.stderr)
            ExtractionWarning.add(warning_msg)
            return ""


def quick_page_count(pdf_path: Path) -> int:
    """Get page count without keeping PDF open."""
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)
