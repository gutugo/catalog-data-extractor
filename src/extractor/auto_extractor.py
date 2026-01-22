"""Automatic extraction logic for catalog data using table-aware parsing."""

import re
from pathlib import Path
from collections import defaultdict

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .data_model import Product, ExtractionSession, PageContent, FieldLocation
from .pdf_reader import PDFReader, CAMELOT_AVAILABLE

console = Console()

# Confidence scores for different extraction methods
CONFIDENCE_CAMELOT = 1.0
CONFIDENCE_PDFPLUMBER = 0.95
CONFIDENCE_PDFMINER = 0.8
CONFIDENCE_REGEX = 0.5

# Patterns for identifying valid item numbers
# Matches:
#   - 4-5 digit numbers: 12345, 1234
#   - Alphanumeric with prefix + digits: PMS989803150181, BJ100120, DBTDCF-A2310EN
#   - Hyphenated codes with digits: TTRS-42, VR-1234, CS-2
#   - Letter codes with digits: TSTAG1, TSTAG2
# Must contain at least one digit to avoid matching plain words like "ABC-DEF"
ITEM_NO_PATTERN = re.compile(
    r'^('
    r'[A-Z]{0,4}\d{4,}[-\dA-Z]*'          # Prefix + 4+ digits (PMS989803150181, BJ100120)
    r'|[A-Z]{1,6}-(?=[\dA-Z-]*\d)[A-Z\d][\dA-Z-]*'  # Letter-hyphen-alphanumeric, must have digit (TTRS-42, CS-2)
    r'|[A-Z]{2,6}\d+[A-Z\d]*'             # Letters + digits (TSTAG1, BJ240120)
    r'|\d{4,5}'                            # 4-5 digit numbers
    r')$',
    re.IGNORECASE
)

# Patterns for parsing count/uom strings like "32 ct.", "100 pk", or "1,000 ct."
# Note: UOM_UNITS is the single source of truth for unit patterns
# Includes standard units and /RL, /EACH style formats found in various catalogs
UOM_UNITS = r'ct|pk|pack|bx|oz|gm|ml|lb|qt|pt|bag|roll|pr|dz|set|btl|tube|jar|can|box|ea|sheets?|pair|kit|rl|cs|each|case|carton|drum|gal|pail|tub'

COUNT_UOM_PATTERN = re.compile(
    rf'^([\d,]+)\s*({UOM_UNITS})\.?$',
    re.IGNORECASE
)

# Pattern for count column detection (unit is optional, for partial matches)
# Handles both "32 ct." and "2,500/RL" formats
COUNT_COLUMN_PATTERN = re.compile(
    rf'^[\d,]+\s*[/]?\s*({UOM_UNITS})?\.?$',
    re.IGNORECASE
)

# Pattern for single-line product extraction: item_no, description, count, price
PRODUCT_LINE_PATTERN = re.compile(
    rf'^(\d{{4,5}})\s+(.+?)\s+(\d+\s*(?:{UOM_UNITS})\.?)\s+\$(\d+\.?\d*)$',
    re.IGNORECASE
)

# Pattern for item_no, count, price on one line (multi-line product name above)
MULTILINE_ITEM_PATTERN = re.compile(
    rf'^(\d{{4,5}})\s+(\d+\s*(?:{UOM_UNITS})\.?)\s+\$(\d+\.?\d*)$',
    re.IGNORECASE
)

# Pattern: CODE $PRICE /UNIT (e.g., "PMS989803150181 $42.26 /EACH")
# Used for product cards in specialty catalogs
# Code must contain at least one digit to avoid matching plain words
CODE_PRICE_PATTERN = re.compile(
    r'^([A-Z]{2,4}(?=[\dA-Z-]*\d)[\dA-Z-]+)\s+\$([\d,]+\.?\d*)\s*/?(EACH|PAIR|RL|BX|CS|PK|EA|CT)\b',
    re.IGNORECASE
)

# Pattern: "Item #" or "Item#" followed by code (e.g., "Item # TTRS-42")
# Code must contain at least one digit
ITEM_PREFIX_PATTERN = re.compile(
    r'Item\s*#?\s*:?\s*([A-Z]{0,4}(?=[\dA-Z-]*\d)[\dA-Z][\dA-Z-]*)',
    re.IGNORECASE
)

# Pattern for quantity with slash-prefix UOM (e.g., "2,500/RL", "100/EACH")
SLASH_UOM_PATTERN = re.compile(
    rf'^([\d,]+)\s*/\s*({UOM_UNITS})$',
    re.IGNORECASE
)

# Header patterns to skip
HEADER_PATTERNS = [
    re.compile(r'^Item\s*#?$', re.IGNORECASE),
    re.compile(r'^Description$', re.IGNORECASE),
    re.compile(r'^Count$', re.IGNORECASE),
    re.compile(r'^Price$', re.IGNORECASE),
]

# Skip patterns for footer/note rows
SKIP_PATTERNS = [
    re.compile(r'See Page', re.IGNORECASE),
    re.compile(r'Please note', re.IGNORECASE),
    re.compile(r'Keep this catalog', re.IGNORECASE),
    re.compile(r'^\*', re.IGNORECASE),
]


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


def is_header_row(row: list[str]) -> bool:
    """Check if row is a table header.

    Requires at least 2 header-like cells to avoid false positives
    on product rows that happen to contain words like "Description".
    """
    header_count = 0
    non_empty_count = 0

    for cell in row:
        if not cell:
            continue
        non_empty_count += 1
        for pattern in HEADER_PATTERNS:
            if pattern.match(cell.strip()):
                header_count += 1
                break

    # Require at least 2 header cells for larger rows
    # For small rows (2-3 cells), require majority to be headers
    if non_empty_count <= 3:
        return header_count >= (non_empty_count // 2 + 1)  # Majority
    return header_count >= 2


def should_skip_row(row: list[str]) -> bool:
    """Check if row should be skipped (footer, note, etc)."""
    row_text = ' '.join(cell or '' for cell in row)
    for pattern in SKIP_PATTERNS:
        if pattern.search(row_text):
            return True
    return False


def clean_product_name(name: str) -> str:
    """Clean up product name text."""
    if not name:
        return ''
    # Replace multiple spaces/newlines with single space
    cleaned = re.sub(r'\s+', ' ', name.strip())
    return cleaned


def _get_cell_text(cell) -> str:
    """Get text from a cell, handling both string and dict formats."""
    if isinstance(cell, dict):
        return (cell.get('text') or '').strip()
    return (cell or '').strip()


def find_count_column(table: list[list]) -> int:
    """Find which column contains count data (e.g., '32 ct.', '1 pk').

    Works with both string lists and dict lists (with 'text' and 'bbox' keys).

    Returns the column index, or -1 if no valid count column found.
    """
    if not table:
        return -1

    # Check each column (skip first two: item#, description)
    num_cols = max(len(row) for row in table) if table else 0

    best_col = -1
    best_match_rate = 0

    for col_idx in range(2, num_cols):
        count_matches = 0
        total_cells = 0

        for row in table:
            if col_idx >= len(row):
                continue
            cell_text = _get_cell_text(row[col_idx])
            if not cell_text:
                continue
            total_cells += 1
            # Check if cell looks like a count (number + optional unit)
            if COUNT_COLUMN_PATTERN.match(cell_text):
                count_matches += 1

        # Need at least 50% match rate and at least 1 matching cell
        # For small tables (1-2 rows), require 100% match rate
        if total_cells > 0 and count_matches >= 1:
            match_rate = count_matches / total_cells
            min_rate = 1.0 if total_cells <= 2 else 0.5
            if match_rate >= min_rate and match_rate > best_match_rate:
                best_match_rate = match_rate
                best_col = col_idx

    return best_col


def _get_cell_bbox(cell) -> tuple | None:
    """Get bbox from a cell, handling both string and dict formats."""
    if isinstance(cell, dict):
        return cell.get('bbox')
    return None


def extract_products_from_table(table: list[list], page_number: int, source_file: str) -> list[Product]:
    """Extract products from a single table.

    Expected column format: Item # | Description | Count | Price
    But we handle variations and different column counts.

    Works with both string lists and dict lists (with 'text' and 'bbox' keys).
    """
    products = []

    # Determine which column contains count data
    count_col = find_count_column(table)

    # Convert row to string list for header/skip checks
    def row_to_strings(row):
        return [_get_cell_text(cell) for cell in row]

    for row in table:
        row_strings = row_to_strings(row)

        # Skip header and footer rows
        if is_header_row(row_strings) or should_skip_row(row_strings):
            continue

        # Need at least 2 columns for item_no and description
        if len(row) < 2:
            continue

        # Try to find item number in first column
        item_no = _get_cell_text(row[0])
        if not is_valid_item_no(item_no):
            continue

        # Extract product name (column 2)
        product_name = clean_product_name(_get_cell_text(row[1]) if len(row) > 1 else '')
        if not product_name:
            continue

        # Extract count/uom from detected count column
        count_str = ''
        count_bbox = None
        if count_col >= 0 and count_col < len(row):
            count_str = _get_cell_text(row[count_col])
            count_bbox = _get_cell_bbox(row[count_col])

        pkg, uom = parse_count_uom(count_str)

        # Build field locations from bboxes
        field_locations = {}

        # Item number location (column 0)
        item_bbox = _get_cell_bbox(row[0])
        if item_bbox:
            field_locations['item_no'] = FieldLocation(
                x0=item_bbox[0], y0=item_bbox[1],
                x1=item_bbox[2], y1=item_bbox[3],
                page_number=page_number,
                confidence=1.0
            )

        # Product name location (column 1)
        name_bbox = _get_cell_bbox(row[1]) if len(row) > 1 else None
        if name_bbox:
            field_locations['product_name'] = FieldLocation(
                x0=name_bbox[0], y0=name_bbox[1],
                x1=name_bbox[2], y1=name_bbox[3],
                page_number=page_number,
                confidence=1.0
            )

        # Count/description location (same cell for pkg, uom, description)
        if count_bbox:
            count_location = FieldLocation(
                x0=count_bbox[0], y0=count_bbox[1],
                x1=count_bbox[2], y1=count_bbox[3],
                page_number=page_number,
                confidence=1.0
            )
            field_locations['description'] = count_location
            if pkg:
                field_locations['pkg'] = count_location
            if uom:
                field_locations['uom'] = count_location

        # Create product
        products.append(Product(
            product_name=product_name,
            description=count_str,  # Keep original count string as description
            item_no=item_no,
            pkg=pkg,
            uom=uom,
            page_number=page_number,
            source_file=source_file,
            field_locations=field_locations,
        ))

    return products


def extract_products_from_text_fallback(page: PageContent, source_file: str) -> list[Product]:
    """Fallback text-based extraction when no tables are found.

    Uses regex patterns to parse product lines from raw text.
    Supports multiple catalog formats:
      - OTC-style: item_no description count price
      - Product cards: CODE $PRICE /UNIT
      - Item prefix: "Item # XXX" on separate line
    """
    products = []
    lines = page.lines

    i = 0
    pending_description = []

    while i < len(lines):
        line = lines[i].strip()

        # Skip obvious non-product lines
        if any(pattern.search(line) for pattern in SKIP_PATTERNS):
            pending_description = []
            i += 1
            continue

        # Try single-line product pattern (OTC-style)
        match = PRODUCT_LINE_PATTERN.match(line)
        if match:
            item_no = match.group(1)
            product_name = match.group(2).strip()
            count_str = match.group(3).strip()

            # Prepend any pending description
            if pending_description:
                product_name = ' '.join(pending_description) + ' ' + product_name
                pending_description = []

            pkg, uom = parse_count_uom(count_str)

            products.append(Product(
                product_name=product_name,
                description=count_str,
                item_no=item_no,
                pkg=pkg,
                uom=uom,
                page_number=page.page_number,
                source_file=source_file,
            ))
            i += 1
            continue

        # Try multi-line item pattern (works with or without pending description)
        multi_match = MULTILINE_ITEM_PATTERN.match(line)
        if multi_match:
            item_no = multi_match.group(1)
            count_str = multi_match.group(2).strip()
            # Use pending description if available, otherwise use empty string
            product_name = ' '.join(pending_description) if pending_description else ''

            pkg, uom = parse_count_uom(count_str)

            # Only create product if we have at least an item_no
            if item_no:
                products.append(Product(
                    product_name=product_name,
                    description=count_str,
                    item_no=item_no,
                    pkg=pkg,
                    uom=uom,
                    page_number=page.page_number,
                    source_file=source_file,
                ))
            pending_description = []
            i += 1
            continue

        # Try CODE $PRICE /UNIT pattern (product cards in specialty catalogs)
        code_price_match = CODE_PRICE_PATTERN.match(line)
        if code_price_match:
            item_no = code_price_match.group(1)
            uom = code_price_match.group(3).lower()

            # Use pending description as product name
            product_name = ' '.join(pending_description) if pending_description else ''
            pending_description = []

            products.append(Product(
                product_name=product_name,
                description=f'/{uom.upper()}',
                item_no=item_no,
                pkg='1',
                uom=uom,
                page_number=page.page_number,
                source_file=source_file,
            ))
            i += 1
            continue

        # Try "Item #" or "Item#" prefix pattern
        item_prefix_match = ITEM_PREFIX_PATTERN.search(line)
        if item_prefix_match:
            item_no = item_prefix_match.group(1)

            # Look ahead for product name and price info
            product_name = ''
            uom = ''

            # Check if there's more text on the same line after the item number
            rest_of_line = line[item_prefix_match.end():].strip()
            if rest_of_line:
                product_name = rest_of_line

            # Also use any pending description
            if pending_description:
                if product_name:
                    product_name = ' '.join(pending_description) + ' ' + product_name
                else:
                    product_name = ' '.join(pending_description)
                pending_description = []

            # Look ahead for price/uom on next lines
            j = i + 1
            while j < len(lines) and j < i + 5:
                next_line = lines[j].strip()
                # Check for price with UOM (use UOM_UNITS for consistency)
                price_uom_match = re.search(rf'\$[\d.]+\s*/?\s*({UOM_UNITS})\b', next_line, re.IGNORECASE)
                if price_uom_match:
                    uom = price_uom_match.group(1).lower()
                    break
                # Stop if we hit another item marker
                if ITEM_PREFIX_PATTERN.search(next_line) or is_valid_item_no(next_line.split()[0] if next_line.split() else ''):
                    break
                # Accumulate additional description
                if next_line and not next_line.startswith('$'):
                    if product_name:
                        product_name += ' ' + next_line
                    else:
                        product_name = next_line
                j += 1

            if item_no and is_valid_item_no(item_no):
                products.append(Product(
                    product_name=product_name,
                    description=f'/{uom.upper()}' if uom else '',
                    item_no=item_no,
                    pkg='1' if uom else '',
                    uom=uom,
                    page_number=page.page_number,
                    source_file=source_file,
                ))
            i += 1
            continue

        # Check for standalone alphanumeric item codes (e.g., "PMS989803150181")
        # is_valid_item_no already rejects short numbers (< 4 digits)
        if is_valid_item_no(line):
            # This might be a standalone item number
            # Look ahead for price/description
            item_no = line
            product_name = ''
            uom = ''

            j = i + 1
            while j < len(lines) and j < i + 5:
                next_line = lines[j].strip()
                # Check for price with UOM (use UOM_UNITS for consistency)
                price_uom_match = re.search(rf'\$[\d.]+\s*/?\s*({UOM_UNITS})\b', next_line, re.IGNORECASE)
                if price_uom_match:
                    uom = price_uom_match.group(1).lower()
                    break
                # Stop if we hit another item
                if is_valid_item_no(next_line):
                    break
                # Accumulate description
                if next_line and not next_line.startswith('$'):
                    if product_name:
                        product_name += ' ' + next_line
                    else:
                        product_name = next_line
                j += 1

            # Use pending description if we don't have a product name
            if not product_name and pending_description:
                product_name = ' '.join(pending_description)
            # Always clear pending_description after processing a product
            pending_description = []

            if item_no:
                products.append(Product(
                    product_name=product_name,
                    description=f'/{uom.upper()}' if uom else '',
                    item_no=item_no,
                    pkg='1' if uom else '',
                    uom=uom,
                    page_number=page.page_number,
                    source_file=source_file,
                ))
            i += 1
            continue

        # Could be part of multi-line product name
        if not line.startswith('$') and not re.match(r'^\d+$', line):
            # Don't accumulate section headers - must be ALL CAPS or match common header patterns
            # This avoids false positives on product names like "Baby Wipes" or "Hand Soap"
            is_section_header = (
                # All uppercase words (e.g., "CLEANING SUPPLIES", "OFFICE PRODUCTS")
                (re.match(r'^[A-Z][A-Z\s&,\-]+$', line) and len(line) > 3) or
                # Common catalog section header patterns
                re.match(r'^(Page \d+|Section \d+|Category:|Index|Table of Contents)$', line, re.IGNORECASE)
            )
            if not is_section_header:
                pending_description.append(line)

        i += 1

    return products


class AutoExtractor:
    """Handles automatic extraction from catalogs using table-aware parsing."""

    def __init__(self, pdf_path: Path, session_dir: Path, multi_method: bool = False):
        self.pdf_path = Path(pdf_path)
        self.session_dir = session_dir
        self.multi_method = multi_method
        self.fallback_pages: list[int] = []  # Track pages that needed text fallback (single-method)
        self.empty_pages: list[int] = []  # Track pages with no products found (multi-method)

    def run(self, progress_callback=None, show_console=True) -> ExtractionSession:
        """Run automatic extraction on all pages.

        Args:
            progress_callback: Optional callback function(page_num, total_pages, products_count)
                              Called after each page is processed.
            show_console: Whether to show console output (default True).
                         Set to False when running in background.
        """
        if show_console:
            mode = "[cyan](multi-method)[/cyan] " if self.multi_method else ""
            console.print(f"[bold blue]Auto-extracting:[/bold blue] {mode}{self.pdf_path.name}")
            if self.multi_method and not CAMELOT_AVAILABLE:
                console.print("[yellow]Note: Camelot not available (install ghostscript). Using pdfplumber + pdfminer.[/yellow]")

        with PDFReader(self.pdf_path) as reader:
            session = ExtractionSession(
                source_file=self.pdf_path.name,
                total_pages=reader.total_pages,
                current_page=1,
            )

            if show_console:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Processing pages...", total=reader.total_pages)

                    for page_num in range(1, reader.total_pages + 1):
                        products = self._extract_page(reader, page_num)
                        for product in products:
                            session.add_product(product)

                        session.current_page = page_num
                        progress.update(task, advance=1)

                        if progress_callback:
                            progress_callback(page_num, reader.total_pages, len(session.products))
            else:
                # Silent mode for background extraction
                for page_num in range(1, reader.total_pages + 1):
                    products = self._extract_page(reader, page_num)
                    for product in products:
                        session.add_product(product)

                    session.current_page = page_num

                    if progress_callback:
                        progress_callback(page_num, reader.total_pages, len(session.products))

            session.completed = True
            session.save(self.session_dir)

            if show_console:
                console.print(f"[green]Extracted {len(session.products)} products from {reader.total_pages} pages[/green]")

                if self.fallback_pages:
                    console.print(f"[yellow]Note: {len(self.fallback_pages)} pages used text fallback (no tables found)[/yellow]")
                    if len(self.fallback_pages) <= 10:
                        console.print(f"[dim]Fallback pages: {self.fallback_pages}[/dim]")

                if self.empty_pages:
                    console.print(f"[yellow]Note: {len(self.empty_pages)} pages had no products extracted[/yellow]")
                    if len(self.empty_pages) <= 10:
                        console.print(f"[dim]Empty pages: {self.empty_pages}[/dim]")

        return session

    def _extract_page(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Extract products from a single page, preferring table extraction."""
        # Use multi-method extraction if enabled
        if self.multi_method:
            return self._extract_page_multi(reader, page_num)

        # Try table extraction with positions first
        tables_with_positions = reader.extract_tables_with_positions(page_num)

        if tables_with_positions:
            products = []
            for table_data in tables_with_positions:
                # Convert to row format expected by extract_products_from_table
                products.extend(extract_products_from_table(
                    table_data['rows'], page_num, self.pdf_path.name
                ))

            if products:
                return products

        # Fallback to text extraction
        self.fallback_pages.append(page_num)
        page_content = reader.get_page(page_num)
        return extract_products_from_text_fallback(page_content, self.pdf_path.name)

    def _extract_page_multi(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Extract using multiple methods and merge best results."""
        all_products = []

        # 1. Try Camelot first (best table accuracy)
        camelot_products = self._try_camelot(reader, page_num)
        all_products.append(camelot_products)

        # 2. Try pdfplumber tables (current method)
        pdfplumber_products = self._try_pdfplumber_tables(reader, page_num)
        all_products.append(pdfplumber_products)

        # 3. Try pdfminer.six text layout
        layout_products = self._try_pdfminer_layout(reader, page_num)
        all_products.append(layout_products)

        # 4. Merge results - pick best confidence per product
        merged = self._merge_extractions(*all_products)

        # If no products found by any method, track as empty page
        if not merged:
            self.empty_pages.append(page_num)

        return merged

    def _try_camelot(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using Camelot."""
        if not CAMELOT_AVAILABLE:
            return []

        tables = reader.extract_tables_camelot(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
            # Update confidence to Camelot level
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_CAMELOT
            products.extend(extracted)

        return products

    def _try_pdfplumber_tables(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using pdfplumber tables."""
        tables = reader.extract_tables_with_positions(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
            # Update confidence to pdfplumber level
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_PDFPLUMBER
            products.extend(extracted)

        return products

    def _try_pdfminer_layout(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using pdfminer.six layout analysis."""
        text_blocks = reader.extract_text_with_layout(page_num)

        # Convert text blocks to lines for regex extraction
        all_lines = []
        for block in text_blocks:
            for line_data in block.get('lines', []):
                all_lines.append(line_data['text'])

        # Create a synthetic PageContent for the fallback extractor
        page_content = PageContent(
            page_number=page_num,
            lines=all_lines,
            raw_text='\n'.join(all_lines)
        )

        products = extract_products_from_text_fallback(page_content, self.pdf_path.name)

        # Update confidence for pdfminer extraction
        # Note: Position data from pdfminer is not used since regex fallback
        # doesn't track which text corresponds to which field
        # Only set confidence if no existing location or if existing has lower confidence
        for product in products:
            for field_name in ['item_no', 'product_name', 'description', 'pkg', 'uom']:
                if field_name not in product.field_locations:
                    product.field_locations[field_name] = FieldLocation(
                        x0=0, y0=0, x1=0, y1=0,
                        page_number=page_num,
                        confidence=CONFIDENCE_PDFMINER
                    )
                elif product.field_locations[field_name].confidence > CONFIDENCE_PDFMINER:
                    # Keep existing higher confidence
                    pass
                else:
                    product.field_locations[field_name].confidence = CONFIDENCE_PDFMINER

        return products

    def _merge_extractions(self, *product_lists: list[Product]) -> list[Product]:
        """Merge products from multiple extractors.

        Strategy:
        - Match products by item_no
        - For each field, pick highest confidence value
        - Combine field_locations from best sources
        """
        # Group products by item_no
        by_item_no: dict[str, list[Product]] = defaultdict(list)

        for product_list in product_lists:
            for product in product_list:
                if product.item_no:
                    by_item_no[product.item_no].append(product)

        merged_products = []

        for item_no, products in by_item_no.items():
            if len(products) == 1:
                # Only one extraction found this product
                merged_products.append(products[0])
                continue

            # Multiple extractions - merge them
            merged = self._merge_product_variants(products)
            if merged:
                merged_products.append(merged)

        return merged_products

    def _merge_product_variants(self, products: list[Product]) -> Product | None:
        """Merge multiple extractions of the same product."""
        if not products:
            return None

        # Start with the first product as base
        base = products[0]

        # For product_name: pick longest non-empty value (captures full name)
        best_name = base.product_name
        for p in products[1:]:
            if p.product_name and len(p.product_name) > len(best_name):
                best_name = p.product_name

        # For other fields: pick from highest confidence source
        def get_field_confidence(product: Product, field: str) -> float:
            loc = product.field_locations.get(field)
            return loc.confidence if loc else 0.0

        # Find best description
        best_desc = base.description
        best_desc_conf = get_field_confidence(base, 'description')
        for p in products[1:]:
            conf = get_field_confidence(p, 'description')
            if conf > best_desc_conf and p.description:
                best_desc = p.description
                best_desc_conf = conf

        # Find best pkg
        best_pkg = base.pkg
        best_pkg_conf = get_field_confidence(base, 'pkg')
        for p in products[1:]:
            conf = get_field_confidence(p, 'pkg')
            if conf > best_pkg_conf and p.pkg:
                best_pkg = p.pkg
                best_pkg_conf = conf

        # Find best uom
        best_uom = base.uom
        best_uom_conf = get_field_confidence(base, 'uom')
        for p in products[1:]:
            conf = get_field_confidence(p, 'uom')
            if conf > best_uom_conf and p.uom:
                best_uom = p.uom
                best_uom_conf = conf

        # Merge field_locations - keep highest confidence per field
        merged_locations = {}
        for p in products:
            for field, loc in p.field_locations.items():
                existing = merged_locations.get(field)
                if not existing or loc.confidence > existing.confidence:
                    merged_locations[field] = loc

        return Product(
            product_name=best_name,
            description=best_desc,
            item_no=base.item_no,
            pkg=best_pkg,
            uom=best_uom,
            page_number=base.page_number,
            source_file=base.source_file,
            field_locations=merged_locations,
        )
