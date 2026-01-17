"""Automatic extraction logic for catalog data using table-aware parsing."""

import re
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .data_model import Product, ExtractionSession, PageContent
from .pdf_reader import PDFReader

console = Console()

# Patterns for identifying valid item numbers
ITEM_NO_PATTERN = re.compile(r'^(\d{4,5})$')

# Patterns for parsing count/uom strings like "32 ct.", "100 pk", or "1,000 ct."
COUNT_UOM_PATTERN = re.compile(
    r'^([\d,]+)\s*(ct|pk|pack|bx|oz|gm|ml|lb|qt|pt|bag|roll|pr|dz|set|btl|tube|jar|can|box|ea|sheets?|pair|kit)\.?$',
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
        count_str: String like "32 ct.", "100 pk", "16 oz", "1,000 ct."

    Returns:
        Tuple of (package count, unit of measure)
        Returns ('', count_str) if pattern doesn't match
    """
    if not count_str:
        return '', ''

    count_str = count_str.strip()
    match = COUNT_UOM_PATTERN.match(count_str)
    if match:
        # Remove commas from package count (e.g., "1,000" -> "1000")
        pkg = match.group(1).replace(',', '')
        return pkg, match.group(2).lower().rstrip('.')

    # Try to extract just a number if present
    num_match = re.match(r'^([\d,]+)\s*(.*)$', count_str)
    if num_match:
        pkg = num_match.group(1).replace(',', '')
        return pkg, num_match.group(2).strip().rstrip('.')

    return '', count_str


def is_valid_item_no(value: str) -> bool:
    """Check if value looks like a valid item number (4-5 digits)."""
    if not value:
        return False
    return bool(ITEM_NO_PATTERN.match(value.strip()))


def is_header_row(row: list[str]) -> bool:
    """Check if row is a table header."""
    for cell in row:
        if not cell:
            continue
        for pattern in HEADER_PATTERNS:
            if pattern.match(cell.strip()):
                return True
    return False


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


def find_count_column(table: list[list[str]]) -> int:
    """Find which column contains count data (e.g., '32 ct.', '1 pk').

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
            cell = (row[col_idx] or '').strip()
            if not cell:
                continue
            total_cells += 1
            # Check if cell looks like a count (number + optional unit)
            # Note: Keep in sync with COUNT_UOM_PATTERN (supports comma-formatted numbers like "1,000")
            if re.match(r'^[\d,]+\s*(ct|pk|pack|bx|oz|gm|ml|lb|qt|pt|bag|roll|pr|dz|set|btl|tube|jar|can|box|ea|sheets?|pair|kit)?\.?$', cell, re.IGNORECASE):
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


def extract_products_from_table(table: list[list[str]], page_number: int, source_file: str) -> list[Product]:
    """Extract products from a single table.

    Expected column format: Item # | Description | Count | Price
    But we handle variations and different column counts.
    """
    products = []

    # Determine which column contains count data
    count_col = find_count_column(table)

    for row in table:
        # Skip header and footer rows
        if is_header_row(row) or should_skip_row(row):
            continue

        # Need at least 2 columns for item_no and description
        if len(row) < 2:
            continue

        # Try to find item number in first column
        item_no = (row[0] or '').strip()
        if not is_valid_item_no(item_no):
            continue

        # Extract product name (column 2)
        product_name = clean_product_name(row[1] if len(row) > 1 else '')
        if not product_name:
            continue

        # Extract count/uom from detected count column
        count_str = ''
        if count_col >= 0 and count_col < len(row):
            count_str = (row[count_col] or '').strip()

        pkg, uom = parse_count_uom(count_str)

        # Create product (we don't use price column for now)
        products.append(Product(
            product_name=product_name,
            description=count_str,  # Keep original count string as description
            item_no=item_no,
            pkg=pkg,
            uom=uom,
            page_number=page_number,
            source_file=source_file,
        ))

    return products


def extract_products_from_text_fallback(page: PageContent, source_file: str) -> list[Product]:
    """Fallback text-based extraction when no tables are found.

    Uses regex patterns to parse product lines from raw text.
    """
    products = []
    lines = page.lines

    # Pattern: item_no at start, description, count, price at end
    # Note: Keep unit list in sync with COUNT_UOM_PATTERN
    PRODUCT_LINE_PATTERN = re.compile(
        r'^(\d{4,5})\s+(.+?)\s+(\d+\s*(?:ct|pk|pack|bx|oz|gm|ml|lb|qt|pt|bag|roll|pr|dz|set|btl|tube|jar|can|box|ea|sheets?|pair|kit)\.?)\s+\$(\d+\.?\d*)$',
        re.IGNORECASE
    )

    # Pattern for item_no, count, price on one line (multi-line product name above)
    # Note: Keep unit list in sync with COUNT_UOM_PATTERN
    MULTILINE_ITEM_PATTERN = re.compile(
        r'^(\d{4,5})\s+(\d+\s*(?:ct|pk|pack|bx|oz|gm|ml|lb|qt|pt|bag|roll|pr|dz|set|btl|tube|jar|can|box|ea|sheets?|pair|kit)\.?)\s+\$(\d+\.?\d*)$',
        re.IGNORECASE
    )

    i = 0
    pending_description = []

    while i < len(lines):
        line = lines[i].strip()

        # Skip obvious non-product lines
        if any(pattern.search(line) for pattern in SKIP_PATTERNS):
            pending_description = []
            i += 1
            continue

        # Try single-line product pattern
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

        # Try multi-line item pattern
        multi_match = MULTILINE_ITEM_PATTERN.match(line)
        if multi_match and pending_description:
            item_no = multi_match.group(1)
            count_str = multi_match.group(2).strip()
            product_name = ' '.join(pending_description)

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
            pending_description = []
            i += 1
            continue

        # Could be part of multi-line product name
        if not line.startswith('$') and not re.match(r'^\d+$', line):
            # Don't accumulate section headers (capitalized multi-word phrases)
            # Short lines (<=3 chars) are unlikely to be meaningful headers
            is_section_header = re.match(r'^[A-Z][a-zA-Z\s&,\-]+$', line) and len(line) > 3
            if not is_section_header:
                pending_description.append(line)

        i += 1

    return products


class AutoExtractor:
    """Handles automatic extraction from catalogs using table-aware parsing."""

    def __init__(self, pdf_path: Path, session_dir: Path):
        self.pdf_path = Path(pdf_path)
        self.session_dir = session_dir
        self.fallback_pages: list[int] = []  # Track pages that needed text fallback

    def run(self) -> ExtractionSession:
        """Run automatic extraction on all pages."""
        console.print(f"[bold blue]Auto-extracting:[/bold blue] {self.pdf_path.name}")

        with PDFReader(self.pdf_path) as reader:
            session = ExtractionSession(
                source_file=self.pdf_path.name,
                total_pages=reader.total_pages,
                current_page=1,
            )

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

            session.completed = True
            session.save(self.session_dir)

            console.print(f"[green]Extracted {len(session.products)} products from {reader.total_pages} pages[/green]")

            if self.fallback_pages:
                console.print(f"[yellow]Note: {len(self.fallback_pages)} pages used text fallback (no tables found)[/yellow]")
                if len(self.fallback_pages) <= 10:
                    console.print(f"[dim]Fallback pages: {self.fallback_pages}[/dim]")

        return session

    def _extract_page(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Extract products from a single page, preferring table extraction."""
        # Try table extraction first
        tables = reader.extract_tables(page_num)

        if tables:
            products = []
            for table in tables:
                products.extend(extract_products_from_table(
                    table, page_num, self.pdf_path.name
                ))

            if products:
                return products

        # Fallback to text extraction
        self.fallback_pages.append(page_num)
        page_content = reader.get_page(page_num)
        return extract_products_from_text_fallback(page_content, self.pdf_path.name)
