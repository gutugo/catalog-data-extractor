"""PDF text extraction using pdfplumber, camelot, and pdfminer.six."""

from itertools import zip_longest
from pathlib import Path
import sys
from typing import Iterator, Optional

import pdfplumber
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextBoxHorizontal, LTTextLineHorizontal, LTChar

from .data_model import PageContent

# Camelot is optional - only imported when needed
try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False


class PDFReader:
    """Handles PDF text extraction with positional data."""

    def __init__(self, pdf_path: Path):
        self.pdf_path = Path(pdf_path)
        self._pdf: Optional[pdfplumber.PDF] = None

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
            print(f"Warning: Failed to extract text from page {page_number}: {e}", file=sys.stderr)
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
            print(f"Warning: Failed to extract tables from page {page_number}: {e}", file=sys.stderr)
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
            print(f"Warning: Failed to find tables on page {page_number}: {e}", file=sys.stderr)
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
            print(f"Warning: Camelot failed on page {page_number}: {e}", file=sys.stderr)
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
                            if hasattr(cell, 'x1') and hasattr(cell, 'y1'):
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
            line_margin=0.5,
            word_margin=0.1,
            char_margin=2.0,
            boxes_flow=0.5,
        )

        result = []

        try:
            # extract_pages yields page layouts
            for page_idx, page_layout in enumerate(extract_pages(
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
            print(f"Warning: pdfminer failed on page {page_number}: {e}", file=sys.stderr)

        return result


def quick_page_count(pdf_path: Path) -> int:
    """Get page count without keeping PDF open."""
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)
