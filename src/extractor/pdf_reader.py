"""PDF text extraction using pdfplumber."""

from pathlib import Path
from typing import Iterator, Optional

import pdfplumber

from .data_model import PageContent


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
            import sys
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
            import sys
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


def quick_page_count(pdf_path: Path) -> int:
    """Get page count without keeping PDF open."""
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)
