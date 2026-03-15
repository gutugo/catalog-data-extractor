"""Shared parsing utility functions for catalog data extraction."""

from __future__ import annotations

import re

from .patterns import (
    COUNT_UOM_PATTERN,
    ITEM_NO_PATTERN,
    SLASH_UOM_PATTERN,
)


def parse_count_uom(count_str: str) -> tuple[str, str]:
    """Parse count string like '32 ct.' into (pkg, uom) tuple.

    Args:
        count_str: String like "32 ct.", "100 pk", "16 oz", "1,000 ct.", "2,500/RL"

    Returns:
        Tuple of (package count, unit of measure)
        Returns ('', count_str) if pattern doesn't match
    """
    if not count_str:
        return '', ''

    count_str = count_str.strip()

    # Try standard count/uom pattern (e.g., "32 ct.", "100 pk")
    match = COUNT_UOM_PATTERN.match(count_str)
    if match:
        # Remove commas from package count (e.g., "1,000" -> "1000")
        pkg = match.group(1).replace(',', '')
        return pkg, match.group(2).lower().rstrip('.')

    # Try slash-separated format (e.g., "2,500/RL", "100/EACH")
    slash_match = SLASH_UOM_PATTERN.match(count_str)
    if slash_match:
        pkg = slash_match.group(1).replace(',', '')
        return pkg, slash_match.group(2).lower()

    # Try to extract just a number if present
    num_match = re.match(r'^([\d,]+)\s*(.*)$', count_str)
    if num_match:
        pkg = num_match.group(1).replace(',', '')
        return pkg, num_match.group(2).strip().rstrip('.')

    return '', count_str


def is_valid_item_no(value: str) -> bool:
    """Check if value looks like a valid item number.

    Accepts:
        - 4-5 digit numbers: 12345, 1234
        - Alphanumeric codes: PMS989803150181, BJ100120
        - Hyphenated codes: TTRS-42, VR-1234
    """
    if not value:
        return False
    return bool(ITEM_NO_PATTERN.match(value.strip()))


def clean_product_name(name: str) -> str:
    """Clean up product name text."""
    if not name:
        return ''
    # Replace multiple spaces/newlines with single space
    cleaned = re.sub(r'\s+', ' ', name.strip())
    return cleaned


def combine_identifiers(upc: str, sku: str, item_no: str) -> str:
    """Combine identifiers with ' / ' separator. Priority: UPC > SKU > Item #.

    Args:
        upc: UPC/barcode value
        sku: SKU value
        item_no: Item number value

    Returns:
        Combined identifier string like "012345678901 / ABC123"
    """
    parts = []
    if upc:
        parts.append(upc.strip())
    if sku:
        sku_val = sku.strip()
        if sku_val not in parts:
            parts.append(sku_val)
    if item_no:
        item_val = item_no.strip()
        if item_val not in parts:
            parts.append(item_val)
    return ' / '.join(parts) if parts else ''


def parse_markdown_tables(text: str) -> list[list[list[str]]]:
    """Parse markdown tables from pymupdf4llm output.

    Detects tables formatted with | separators:
    | Header 1 | Header 2 |
    |----------|----------|
    | Cell 1   | Cell 2   |

    Args:
        text: Markdown text potentially containing tables

    Returns:
        List of tables, each table is a list of rows, each row is a list of cell strings
    """
    tables: list[list[list[str]]] = []
    current_table: list[list[str]] = []
    in_table = False

    for line in text.split('\n'):
        line = line.strip()

        # Check if line looks like a table row
        if '|' in line:
            # Skip separator rows (|---|---|)
            if re.match(r'^\|[\s\-:]+\|$', line) or re.match(r'^\|(\s*[-:]+\s*\|)+$', line):
                in_table = True
                continue

            # Parse cells between pipes
            if line.startswith('|'):
                cells = [c.strip() for c in line.split('|')[1:-1]]
            else:
                cells = [c.strip() for c in line.split('|')]

            # Filter out empty rows
            if cells and any(c for c in cells):
                current_table.append(cells)
                in_table = True
        else:
            # Non-table line - end current table if we were in one
            if in_table and current_table:
                # Only keep tables with at least 2 rows (header + data)
                if len(current_table) >= 2:
                    tables.append(current_table)
                current_table = []
                in_table = False

    # Don't forget the last table
    if current_table and len(current_table) >= 2:
        tables.append(current_table)

    return tables


def parse_html_tables(html: str) -> list[list[list[str]]]:
    """Parse HTML tables into rows of cell strings.

    Handles GLM-OCR output which returns <table> HTML instead of markdown.

    Args:
        html: HTML string potentially containing <table> elements

    Returns:
        List of tables, each table is a list of rows, each row is a list of cell strings
    """
    from html.parser import HTMLParser
    from html import unescape as html_unescape

    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables: list[list[list[str]]] = []
            self.current_table: list[list[str]] = []
            self.current_row: list[str] = []
            self.current_cell: str = ""
            self.in_cell = False

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag == 'table':
                self.current_table = []
            elif tag == 'tr':
                self.current_row = []
            elif tag in ('td', 'th'):
                self.current_cell = ""
                self.in_cell = True
            elif tag == 'br' and self.in_cell:
                self.current_cell += " "

        def handle_data(self, data):
            if self.in_cell:
                self.current_cell += data

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in ('td', 'th'):
                self.in_cell = False
                self.current_row.append(self.current_cell.strip())
            elif tag == 'tr':
                if self.current_row:
                    self.current_table.append(self.current_row)
                self.current_row = []
            elif tag == 'table':
                if self.current_table and len(self.current_table) >= 2:
                    self.tables.append(self.current_table)
                self.current_table = []

        def handle_entityref(self, name):
            if self.in_cell:
                self.current_cell += html_unescape(f'&{name};')

        def handle_charref(self, name):
            if self.in_cell:
                self.current_cell += html_unescape(f'&#{name};')

    if '<table' not in html.lower():
        return []

    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    # Catch any unclosed table
    if parser.current_table and len(parser.current_table) >= 2:
        parser.tables.append(parser.current_table)

    return parser.tables
