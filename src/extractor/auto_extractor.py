"""Automatic extraction logic for catalog data using table-aware parsing."""

from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .data_model import Product, ExtractionSession, PageContent, FieldLocation
from .pdf_reader import (
    PDFReader,
    CAMELOT_AVAILABLE,
    DOCLING_AVAILABLE,
    IMG2TABLE_AVAILABLE,
    PYMUPDF4LLM_AVAILABLE,
    PYMUPDF_AVAILABLE,
    UNSTRUCTURED_AVAILABLE,
)

console = Console()

# Confidence scores for different extraction methods
CONFIDENCE_DOCLING = 0.98       # High - AI table detection (IBM TableFormer)
CONFIDENCE_CAMELOT = 1.0        # Highest - traditional table detection
CONFIDENCE_UNSTRUCTURED = 0.92  # High - document understanding with layout analysis
CONFIDENCE_PDFPLUMBER = 0.95    # Good - pdfplumber table extraction
CONFIDENCE_PYMUPDF = 0.93       # Good - fast native table detection
CONFIDENCE_IMG2TABLE = 0.90     # Good - borderless table specialist
CONFIDENCE_PYMUPDF4LLM = 0.85   # Good - layout-aware markdown text
CONFIDENCE_PDFMINER = 0.8       # Fair - layout analysis
CONFIDENCE_REGEX = 0.5          # Low - text pattern fallback

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

# Pattern for dual-identifier format: UPC SKU DESCRIPTION SIZE $PRICE
# Example: "A1 446761 ACNE CONTROL CLEANSER 8 OZ $16"
# UPC is short alphanumeric (A1, A11, B2, etc.), SKU is 5-6 digits
DUAL_ID_PATTERN = re.compile(
    rf'^([A-Z]\d{{1,3}})\s+(\d{{5,6}})\s+(.+?)\s+(\d+\s*(?:{UOM_UNITS})\.?)\s+\$(\d+\.?\d*)$',
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

# Header patterns to skip (used for detecting header rows)
HEADER_PATTERNS = [
    re.compile(r'^Item\s*#?$', re.IGNORECASE),
    re.compile(r'^Description$', re.IGNORECASE),
    re.compile(r'^Count$', re.IGNORECASE),
    re.compile(r'^Price$', re.IGNORECASE),
    re.compile(r'^SKU\s*#?$', re.IGNORECASE),
    re.compile(r'^UPC', re.IGNORECASE),
    re.compile(r'^Product\s*(Name|Code)?$', re.IGNORECASE),
]

# False positive patterns - specification values that look like item numbers
# These are commonly found in product brochures/spec sheets, not product listings
FALSE_POSITIVE_PATTERNS = [
    # Measurements with units (75kg, 200cm, 10mm, 5m, 12inches)
    re.compile(r'^\d+\.?\d*\s*(kg|g|lb|oz|cm|mm|m|inches?|in|ft|feet)\.?$', re.IGNORECASE),
    # Dimension patterns (200x85x203cm, 29x185cm, 10x20)
    re.compile(r'^\d+\.?\d*\s*x\s*\d+', re.IGNORECASE),
    # Dimension with slash (210/250mm, 100/200cm)
    re.compile(r'^\d+\s*/\s*\d+\s*(mm|cm|m|kg|g).*$', re.IGNORECASE),
    # Dimension with diameter (205mmdiameter)
    re.compile(r'^\d+\s*(mm|cm|m)\s*diameter$', re.IGNORECASE),
    # Time values (10Minutes, 5Hours, 30Seconds, 2hrs)
    re.compile(r'^\d+\.?\d*\s*(minutes?|mins?|hours?|hrs?|seconds?|secs?|days?)\.?$', re.IGNORECASE),
    # Comma-separated time/value lists (10, 15, 20, 25mins)
    re.compile(r'^[\d,\s]+\s*(mins?|hours?|secs?)$', re.IGNORECASE),
    # Percentage values (50%, 99.9%)
    re.compile(r'^\d+\.?\d*\s*%$'),
    # Temperature values (37°C, 98.6°F, 25C, 77F)
    re.compile(r'^\d+\.?\d*\s*°?[CF]$', re.IGNORECASE),
    # Voltage/current/power (12V, 220V, 5A, 100W, 50Hz)
    re.compile(r'^\d+\.?\d*\s*(V|A|W|Hz|kW|mA|VA)$', re.IGNORECASE),
    # Pressure values (10bar, 100psi, 5kPa)
    re.compile(r'^\d+\.?\d*\s*(bar|psi|kPa|MPa|Pa)$', re.IGNORECASE),
    # Capacity/volume (5L, 500ml, 10gal)
    re.compile(r'^\d+\.?\d*\s*(L|ml|gal|liters?|litres?)$', re.IGNORECASE),
    # Speed/rate values (100rpm, 50m/s)
    re.compile(r'^\d+\.?\d*\s*(rpm|m/s|km/h|mph)$', re.IGNORECASE),
    # Range patterns (10-20, 5~10)
    re.compile(r'^\d+\.?\d*\s*[-~]\s*\d+\.?\d*$'),
    # IP ratings (IPX4, IP65, IP67)
    re.compile(r'^IP[X\d]\d?$', re.IGNORECASE),
    # Class ratings (Class1, Class 2, ClassII)
    re.compile(r'^Class\s*[1-9IVX]+$', re.IGNORECASE),
    # Standards codes (BS 7177, EN 597-1, ISO 9001)
    re.compile(r'^(BS|EN|ISO|IEC|ANSI|UL|CE|CSA)\s*\d+', re.IGNORECASE),
    # Spec labels ending with colon (Weight:, Size:, Dimensions:)
    re.compile(r'^[A-Za-z\s]+:$'),
    # Pure descriptive words that might slip through
    re.compile(r'^(Yes|No|N/?A|None|Standard|Optional|Included|Available)$', re.IGNORECASE),
    # Descriptive phrases with measurements embedded (20 cm side bolster, High MVTR 4 stretch)
    re.compile(r'^\d+\s*(cm|mm|m)\s+\w+', re.IGNORECASE),
    # Text with embedded numbers that aren't codes (High MVTR 4 stretch PU)
    re.compile(r'^[A-Za-z]+\s+[A-Za-z]*\s*\d+\s+[A-Za-z]+', re.IGNORECASE),
]

# Identifier column header patterns - maps header text to field name
# Order matters: more specific patterns should come first
IDENTIFIER_HEADER_PATTERNS = {
    'upc': [
        re.compile(r'^UPC\s*(Code|#)?$', re.IGNORECASE),
        re.compile(r'^Universal\s*Product\s*Code$', re.IGNORECASE),
        re.compile(r'^Barcode$', re.IGNORECASE),
        re.compile(r'^GTIN$', re.IGNORECASE),
        re.compile(r'^EAN(-13)?$', re.IGNORECASE),
    ],
    'sku': [
        re.compile(r'^SKU\s*(#|No\.?)?$', re.IGNORECASE),
        re.compile(r'^Stock\s*(Keeping\s*Unit|#|No\.?)?$', re.IGNORECASE),
        re.compile(r'^Vendor\s*(#|No\.?)?$', re.IGNORECASE),
    ],
    'item_no': [
        re.compile(r'^Item\s*(#|No\.?|Number)?$', re.IGNORECASE),
        re.compile(r'^Part\s*(#|No\.?|Number)?$', re.IGNORECASE),
        re.compile(r'^Catalog\s*(#|No\.?|Number)?$', re.IGNORECASE),
        re.compile(r'^Cat\s*(#|No\.?)?$', re.IGNORECASE),
        re.compile(r'^Product\s*(#|Code|ID)$', re.IGNORECASE),
        re.compile(r'^Model\s*(#|No\.?|Number)?$', re.IGNORECASE),
        re.compile(r'^Code$', re.IGNORECASE),
        re.compile(r'^ID$', re.IGNORECASE),
        re.compile(r'^NDC$', re.IGNORECASE),  # National Drug Code
        re.compile(r'^MPN$', re.IGNORECASE),  # Manufacturer Part Number
    ],
}

# Product name/description header patterns
PRODUCT_NAME_HEADER_PATTERNS = [
    re.compile(r'^Description$', re.IGNORECASE),
    re.compile(r'^Product\s*(Name)?$', re.IGNORECASE),
    re.compile(r'^Item\s*(Name|Description)$', re.IGNORECASE),
    re.compile(r'^Name$', re.IGNORECASE),
]

# Count/quantity header patterns
COUNT_HEADER_PATTERNS = [
    re.compile(r'^Count$', re.IGNORECASE),
    re.compile(r'^Qty\.?$', re.IGNORECASE),
    re.compile(r'^Quantity$', re.IGNORECASE),
    re.compile(r'^Pack\s*(Size)?$', re.IGNORECASE),
    re.compile(r'^Size$', re.IGNORECASE),
    re.compile(r'^Unit$', re.IGNORECASE),
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


def is_false_positive_item_no(value: str) -> bool:
    """Check if value is a false positive - looks like item_no but is actually spec data.

    Detects specification values commonly found in product brochures:
        - Measurements: 75kg, 200cm, 10mm
        - Dimensions: 200x85x203cm, 29x185cm
        - Time values: 10Minutes, 5Hours
        - Electrical: 12V, 220V, 5A
        - Plain words without digits: "Nylon", "Black", "Analog Pump"
        - And other spec patterns

    Args:
        value: The candidate item_no string

    Returns:
        True if this looks like specification data (false positive)
    """
    if not value:
        return False

    # Clean up the value - remove newlines, extra whitespace
    cleaned = re.sub(r'\s+', '', value.strip())

    for pattern in FALSE_POSITIVE_PATTERNS:
        if pattern.match(cleaned):
            return True

    # Check for measurements embedded in text (20cmsidebolster, 100mmwidth)
    if re.search(r'\d+(cm|mm|m|kg|g|L|ml)\w+', cleaned, re.IGNORECASE):
        return True

    # Check for long concatenated words that look like descriptions, not codes
    # Real SKUs are typically short (< 20 chars) and use specific patterns
    if len(cleaned) > 15 and re.match(r'^[A-Za-z]+\d+[A-Za-z]+', cleaned):
        # Contains letters-digits-letters pattern and is long - likely concatenated description
        return True

    # Additional heuristic: if it contains newlines, likely a spec cell
    if '\n' in value:
        return True

    # Real SKUs/item numbers almost always contain at least one digit
    # Pure alphabetic values like "Nylon", "Black", "Analog Pump" are specs
    if not any(c.isdigit() for c in cleaned):
        return True

    # Real item numbers rarely contain spaces - if it has multiple words, likely a description
    # Exception: combined identifiers like "UPC / SKU" format
    value_stripped = value.strip()
    if ' ' in value_stripped and ' / ' not in value_stripped:
        # Has spaces but isn't a combined identifier
        # Check if it looks like a sentence/description (3+ words)
        words = value_stripped.split()
        if len(words) >= 3:
            return True
        # Check if it has lowercase words (descriptions often do, codes don't)
        if any(w.islower() or (w[0].isupper() and w[1:].islower()) for w in words if len(w) > 1):
            return True

    return False


def validate_product(product: 'Product') -> bool:
    """Validate that a product looks like a real product, not spec data.

    Args:
        product: Product to validate

    Returns:
        True if product appears valid, False if it's likely a false positive
    """
    # Check if item_no is a false positive
    if is_false_positive_item_no(product.item_no):
        return False

    # Check if product_name looks like a spec label (ends with :)
    if product.product_name and product.product_name.strip().endswith(':'):
        return False

    # Check if product_name is too short (likely a spec label)
    if product.product_name and len(product.product_name.strip()) < 3:
        return False

    return True


def filter_valid_products(products: list['Product']) -> list['Product']:
    """Filter out false positive products from extraction results.

    Args:
        products: List of extracted products

    Returns:
        Filtered list with false positives removed
    """
    return [p for p in products if validate_product(p)]


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


def detect_column_mapping(table: list[list]) -> dict[str, int]:
    """Detect column types based on header row patterns.

    Returns a dict mapping field names to column indices:
    - 'item_no': generic item number column
    - 'sku': SKU column
    - 'upc': UPC/barcode column
    - 'product_name': product description column
    - 'count': count/quantity column

    Falls back to position-based detection if no headers found.
    """
    if not table:
        return {}

    mapping = {}

    # Check first few rows for headers
    for row_idx, row in enumerate(table[:3]):
        row_strings = [_get_cell_text(cell) if not isinstance(cell, str) else cell for cell in row]

        for col_idx, cell_text in enumerate(row_strings):
            if not cell_text:
                continue
            cell_text = cell_text.strip()

            # Check for identifier columns (upc, sku, item_no)
            for field_name, patterns in IDENTIFIER_HEADER_PATTERNS.items():
                for pattern in patterns:
                    if pattern.match(cell_text):
                        if field_name not in mapping:
                            mapping[field_name] = col_idx
                        break

            # Check for product name column
            for pattern in PRODUCT_NAME_HEADER_PATTERNS:
                if pattern.match(cell_text):
                    if 'product_name' not in mapping:
                        mapping['product_name'] = col_idx
                    break

            # Check for count column
            for pattern in COUNT_HEADER_PATTERNS:
                if pattern.match(cell_text):
                    if 'count' not in mapping:
                        mapping['count'] = col_idx
                    break

    return mapping


# Patterns for robust column detection (content-based)
PRICE_PATTERN = re.compile(r'^\$[\d,]+\.?\d*$')
NUMERIC_ONLY_PATTERN = re.compile(r'^[\d,]+$')


def detect_columns_robust(table: list[list], sample_size: int = 10) -> dict[str, int]:
    """Detect column types using multi-signal approach.

    Uses multiple signals instead of just header matching:
    1. Header text patterns (existing approach)
    2. Content pattern matching - detect item_no, price, count patterns
    3. Column width heuristics - narrow=code, wide=description
    4. Cross-row consistency - same pattern across rows

    Args:
        table: List of rows (each row is a list of cells)
        sample_size: Number of data rows to sample for pattern detection

    Returns:
        Dict mapping field names to column indices
    """
    if not table:
        return {}

    # First try header-based detection
    header_mapping = detect_column_mapping(table)

    # Get number of columns
    num_cols = max(len(row) for row in table) if table else 0
    if num_cols == 0:
        return header_mapping

    # Score columns by content patterns
    col_scores: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    col_widths: dict[int, list[int]] = defaultdict(list)

    # Skip header rows, sample data rows
    data_rows = []
    for row in table:
        row_strings = [_get_cell_text(cell) if not isinstance(cell, str) else cell for cell in row]
        if not is_header_row(row_strings):
            data_rows.append(row)
        if len(data_rows) >= sample_size:
            break

    for row in data_rows:
        for col_idx in range(min(len(row), num_cols)):
            cell = row[col_idx]
            text = _get_cell_text(cell) if not isinstance(cell, str) else cell
            text = text.strip() if text else ''

            if not text:
                continue

            # Track column widths
            col_widths[col_idx].append(len(text))

            # Score by content patterns
            # Item number patterns
            if ITEM_NO_PATTERN.match(text):
                col_scores[col_idx]['item_no'] += 1.0

            # Price patterns ($xx.xx)
            if PRICE_PATTERN.match(text):
                col_scores[col_idx]['price'] += 1.0

            # Count/UOM patterns (32 ct., 100 pk)
            if COUNT_UOM_PATTERN.match(text) or COUNT_COLUMN_PATTERN.match(text):
                col_scores[col_idx]['count'] += 1.0

            # Product name heuristics: longer text, mixed case, no special patterns
            if len(text) > 15 and not ITEM_NO_PATTERN.match(text) and not PRICE_PATTERN.match(text):
                col_scores[col_idx]['product_name'] += 0.5

            # Short alphanumeric codes (potential SKU/UPC)
            if len(text) <= 15 and text.isalnum() and any(c.isdigit() for c in text):
                if len(text) >= 10:  # UPC-like (10+ digits)
                    col_scores[col_idx]['upc'] += 0.8
                elif len(text) >= 4:  # SKU-like
                    col_scores[col_idx]['sku'] += 0.5

    # Calculate average column widths
    avg_widths = {}
    for col_idx, widths in col_widths.items():
        avg_widths[col_idx] = sum(widths) / len(widths) if widths else 0

    # Boost product_name score for wide columns
    if avg_widths:
        max_width = max(avg_widths.values())
        for col_idx, width in avg_widths.items():
            if width > max_width * 0.6:  # Column is relatively wide
                col_scores[col_idx]['product_name'] += 0.5

    # Assign columns by highest score, avoiding duplicates
    result = dict(header_mapping)  # Start with header-based mapping
    assigned_cols = set(result.values())

    # For each field type, find best unassigned column
    field_priority = ['item_no', 'upc', 'sku', 'product_name', 'count', 'price']

    for field_name in field_priority:
        if field_name in result:
            continue

        best_col = -1
        best_score = 0.0

        for col_idx in range(num_cols):
            if col_idx in assigned_cols:
                continue
            score = col_scores[col_idx].get(field_name, 0)
            if score > best_score:
                best_score = score
                best_col = col_idx

        # Require minimum score threshold
        if best_col >= 0 and best_score >= 0.5:
            result[field_name] = best_col
            assigned_cols.add(best_col)

    return result


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
            # Handle both | cell | cell | and cell | cell formats
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


def _get_cell_text(cell) -> str:
    """Get text from a cell, handling both string and dict formats."""
    if isinstance(cell, dict):
        return (cell.get('text') or '').strip()
    return (cell or '').strip()


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


def extract_products_from_table(table: list[list], page_number: int, source_file: str,
                                 use_robust_detection: bool = True) -> list[Product]:
    """Extract products from a single table.

    Supports multiple column formats:
    - Item # | Description | Count | Price
    - UPC | SKU | Description | Size | Price
    - And other variations

    Uses header detection to map columns, with optional robust content-based
    detection as fallback.
    Works with both string lists and dict lists (with 'text' and 'bbox' keys).

    Args:
        table: List of rows (each row is a list of cells)
        page_number: Page number for product location
        source_file: Source PDF filename
        use_robust_detection: Use multi-signal column detection (default True)
    """
    products = []

    # Detect column mapping - use robust detection if enabled
    if use_robust_detection:
        col_mapping = detect_columns_robust(table)
    else:
        col_mapping = detect_column_mapping(table)

    # Determine which column contains count data (fallback detection)
    count_col = col_mapping.get('count', -1)
    if count_col < 0:
        count_col = find_count_column(table)

    # Convert row to string list for header/skip checks
    def row_to_strings(row):
        return [_get_cell_text(cell) for cell in row]

    # Determine identifier columns - use mapping or fallback to position
    # Priority: first valid identifier column found
    id_cols = {}
    if 'upc' in col_mapping:
        id_cols['upc'] = col_mapping['upc']
    if 'sku' in col_mapping:
        id_cols['sku'] = col_mapping['sku']
    if 'item_no' in col_mapping:
        id_cols['item_no'] = col_mapping['item_no']

    # Product name column
    name_col = col_mapping.get('product_name', -1)

    # If no column mapping found, use position-based fallback
    use_positional = len(id_cols) == 0

    for row in table:
        row_strings = row_to_strings(row)

        # Skip header and footer rows
        if is_header_row(row_strings) or should_skip_row(row_strings):
            continue

        # Need at least 2 columns
        if len(row) < 2:
            continue

        # Extract identifiers based on mapping
        item_no = ''
        sku = ''
        upc = ''
        field_locations = {}

        if use_positional:
            # Fallback: first column is identifier, second is product name
            item_no = _get_cell_text(row[0])
            if not is_valid_item_no(item_no):
                continue
            name_col = 1

            # Set field location for item_no
            item_bbox = _get_cell_bbox(row[0])
            if item_bbox:
                field_locations['item_no'] = FieldLocation(
                    x0=item_bbox[0], y0=item_bbox[1],
                    x1=item_bbox[2], y1=item_bbox[3],
                    page_number=page_number,
                    confidence=1.0
                )
        else:
            # Use column mapping
            has_valid_id = False

            if 'upc' in id_cols and id_cols['upc'] < len(row):
                upc = _get_cell_text(row[id_cols['upc']])
                if upc:
                    has_valid_id = True
                    upc_bbox = _get_cell_bbox(row[id_cols['upc']])
                    if upc_bbox:
                        field_locations['upc'] = FieldLocation(
                            x0=upc_bbox[0], y0=upc_bbox[1],
                            x1=upc_bbox[2], y1=upc_bbox[3],
                            page_number=page_number,
                            confidence=1.0
                        )

            if 'sku' in id_cols and id_cols['sku'] < len(row):
                sku = _get_cell_text(row[id_cols['sku']])
                if sku:
                    has_valid_id = True
                    sku_bbox = _get_cell_bbox(row[id_cols['sku']])
                    if sku_bbox:
                        field_locations['sku'] = FieldLocation(
                            x0=sku_bbox[0], y0=sku_bbox[1],
                            x1=sku_bbox[2], y1=sku_bbox[3],
                            page_number=page_number,
                            confidence=1.0
                        )

            if 'item_no' in id_cols and id_cols['item_no'] < len(row):
                item_no = _get_cell_text(row[id_cols['item_no']])
                if item_no and is_valid_item_no(item_no):
                    has_valid_id = True
                    item_bbox = _get_cell_bbox(row[id_cols['item_no']])
                    if item_bbox:
                        field_locations['item_no'] = FieldLocation(
                            x0=item_bbox[0], y0=item_bbox[1],
                            x1=item_bbox[2], y1=item_bbox[3],
                            page_number=page_number,
                            confidence=1.0
                        )

            if not has_valid_id:
                continue

            # If no explicit product_name column, find the first text-like column
            # that isn't an identifier or count column
            if name_col < 0:
                used_cols = set(id_cols.values())
                if count_col >= 0:
                    used_cols.add(count_col)
                for idx in range(len(row)):
                    if idx not in used_cols:
                        cell_text = _get_cell_text(row[idx])
                        # Skip if looks like a price
                        if cell_text and not cell_text.startswith('$'):
                            name_col = idx
                            break

        # Extract product name
        product_name = ''
        if name_col >= 0 and name_col < len(row):
            product_name = clean_product_name(_get_cell_text(row[name_col]))
            name_bbox = _get_cell_bbox(row[name_col])
            if name_bbox:
                field_locations['product_name'] = FieldLocation(
                    x0=name_bbox[0], y0=name_bbox[1],
                    x1=name_bbox[2], y1=name_bbox[3],
                    page_number=page_number,
                    confidence=1.0
                )

        if not product_name:
            continue

        # Extract count/uom from detected count column
        count_str = ''
        count_bbox = None
        if count_col >= 0 and count_col < len(row):
            count_str = _get_cell_text(row[count_col])
            count_bbox = _get_cell_bbox(row[count_col])

        pkg, uom = parse_count_uom(count_str)

        # Add count field locations
        if count_bbox:
            count_location = FieldLocation(
                x0=count_bbox[0], y0=count_bbox[1],
                x1=count_bbox[2], y1=count_bbox[3],
                page_number=page_number,
                confidence=1.0
            )
            if pkg:
                field_locations['pkg'] = count_location
            if uom:
                field_locations['uom'] = count_location

        # Combine identifiers into single item_no field
        combined_item_no = combine_identifiers(upc, sku, item_no)

        # Create product with combined identifier
        products.append(Product(
            product_name=product_name,
            description='',
            item_no=combined_item_no,
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

        # Try dual-identifier pattern first (e.g., "A1 446761 DESCRIPTION SIZE $PRICE")
        dual_match = DUAL_ID_PATTERN.match(line)
        if dual_match:
            upc_code = dual_match.group(1)  # e.g., "A1"
            sku_code = dual_match.group(2)  # e.g., "446761"
            product_name = dual_match.group(3).strip()
            count_str = dual_match.group(4).strip()

            # Combine UPC and SKU into item_no
            combined_item_no = combine_identifiers(upc_code, sku_code, '')

            # Prepend any pending description
            if pending_description:
                product_name = ' '.join(pending_description) + ' ' + product_name
                pending_description = []

            pkg, uom = parse_count_uom(count_str)

            products.append(Product(
                product_name=product_name,
                description='',
                item_no=combined_item_no,
                pkg=pkg,
                uom=uom,
                page_number=page.page_number,
                source_file=source_file,
            ))
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
                description='',
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
                    description='',
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
                description='',
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
                next_line_parts = next_line.split()
                if ITEM_PREFIX_PATTERN.search(next_line) or is_valid_item_no(next_line_parts[0] if next_line_parts else ''):
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
                    description='',
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
                    description='',
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
    """Handles automatic extraction from catalogs using smart pipeline.

    The extractor automatically classifies the PDF and selects the best
    extraction methods based on document characteristics (bordered tables,
    borderless tables, scanned documents, etc.).
    """

    def __init__(self, pdf_path: Path, session_dir: Path):
        self.pdf_path = Path(pdf_path)
        self.session_dir = session_dir
        self.empty_pages: list[int] = []  # Track pages with no products found
        self.pipeline_stats: dict[str, int] = defaultdict(int)  # Track which methods succeeded

    def run(self, progress_callback=None, show_console=True) -> ExtractionSession:
        """Run automatic extraction on all pages.

        Args:
            progress_callback: Optional callback function(page_num, total_pages, products_count)
                              Called after each page is processed.
            show_console: Whether to show console output (default True).
                         Set to False when running in background.
        """
        if show_console:
            console.print(f"[bold blue]Auto-extracting:[/bold blue] {self.pdf_path.name}")
            # Show availability of optional extractors
            unavailable = []
            if not CAMELOT_AVAILABLE:
                unavailable.append("Camelot")
            if not DOCLING_AVAILABLE:
                unavailable.append("Docling")
            if not UNSTRUCTURED_AVAILABLE:
                unavailable.append("unstructured")
            if not IMG2TABLE_AVAILABLE:
                unavailable.append("img2table")
            if not PYMUPDF4LLM_AVAILABLE:
                unavailable.append("pymupdf4llm")
            if unavailable:
                console.print(f"[yellow]Note: {', '.join(unavailable)} not available[/yellow]")

        with PDFReader(self.pdf_path) as reader:
            session = ExtractionSession(
                source_file=self.pdf_path.name,
                total_pages=reader.total_pages,
                current_page=1,
            )

            # Classify PDF and show info
            if show_console:
                pdf_info = reader.classify_pdf()
                layout_desc = {
                    'tabular': 'bordered tables',
                    'borderless': 'borderless tables',
                    'text-only': 'text-only layout',
                    'mixed': 'mixed layout',
                }.get(pdf_info['layout_type'], 'unknown')
                scanned_note = " (scanned)" if pdf_info['is_scanned'] else ""
                console.print(f"[dim]PDF classification: {layout_desc}{scanned_note}[/dim]")

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

                if self.empty_pages:
                    console.print(f"[yellow]Note: {len(self.empty_pages)} pages had no products extracted[/yellow]")
                    if len(self.empty_pages) <= 10:
                        console.print(f"[dim]Empty pages: {self.empty_pages}[/dim]")

                if self.pipeline_stats:
                    console.print("[cyan]Extraction method usage:[/cyan]")
                    for method, count in sorted(self.pipeline_stats.items(), key=lambda x: -x[1]):
                        console.print(f"  {method}: {count} pages")

        return session

    def _extract_page(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Extract products from a single page using smart pipeline.

        Automatically selects best extraction methods based on PDF classification.
        """
        return self._extract_page_pipeline(reader, page_num)

    def _extract_page_pipeline(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Extract using pipeline: try methods in order, stop when good results found.

        Uses PDF classification to select optimal method order:
        - Digital + Bordered: Camelot → pdfplumber → PyMuPDF → pdfminer → regex
        - Digital + Borderless: img2table → pdfplumber → Docling → pymupdf4llm → regex
        - Scanned: Docling → unstructured
        - Text-only: pymupdf4llm → pdfminer → regex

        Stops early if a method finds products with sufficient confidence.
        Falls back to merging all results if no single method is good enough.
        """
        MIN_PRODUCTS_THRESHOLD = 1  # Minimum products to consider method successful
        all_results: list[tuple[str, list[Product]]] = []

        # Get PDF classification for smart method selection
        pdf_info = reader.classify_pdf()

        # Select pipeline order based on PDF characteristics
        if pdf_info['is_scanned']:
            # Scanned documents - use AI/vision methods
            pipeline_methods = [
                ('docling', self._try_docling, DOCLING_AVAILABLE),
                ('unstructured', self._try_unstructured, UNSTRUCTURED_AVAILABLE),
            ]
        elif pdf_info['has_borders']:
            # Digital PDF with bordered tables
            pipeline_methods = [
                ('camelot', self._try_camelot, CAMELOT_AVAILABLE),
                ('pdfplumber', self._try_pdfplumber_tables, True),
                ('pymupdf', self._try_pymupdf, PYMUPDF_AVAILABLE),
                ('pdfminer', self._try_pdfminer_layout, True),
            ]
        elif pdf_info['layout_type'] == 'borderless':
            # Borderless tables
            pipeline_methods = [
                ('img2table', self._try_img2table, IMG2TABLE_AVAILABLE),
                ('pdfplumber', self._try_pdfplumber_tables, True),
                ('docling', self._try_docling, DOCLING_AVAILABLE),
                ('pymupdf4llm', self._try_pymupdf4llm, PYMUPDF4LLM_AVAILABLE),
            ]
        elif pdf_info['layout_type'] == 'text-only':
            # Text-only documents
            pipeline_methods = [
                ('pymupdf4llm', self._try_pymupdf4llm, PYMUPDF4LLM_AVAILABLE),
                ('pdfminer', self._try_pdfminer_layout, True),
            ]
        else:
            # Default: try all methods in confidence order
            pipeline_methods = [
                ('camelot', self._try_camelot, CAMELOT_AVAILABLE),
                ('docling', self._try_docling, DOCLING_AVAILABLE),
                ('pdfplumber', self._try_pdfplumber_tables, True),
                ('pymupdf', self._try_pymupdf, PYMUPDF_AVAILABLE),
                ('unstructured', self._try_unstructured, UNSTRUCTURED_AVAILABLE),
                ('img2table', self._try_img2table, IMG2TABLE_AVAILABLE),
                ('pymupdf4llm', self._try_pymupdf4llm, PYMUPDF4LLM_AVAILABLE),
                ('pdfminer', self._try_pdfminer_layout, True),
            ]

        best_method = None
        best_products = []

        for method_name, method_func, is_available in pipeline_methods:
            if not is_available:
                continue

            products = method_func(reader, page_num)
            # Filter out false positives (spec data mistaken for products)
            products = filter_valid_products(products)
            all_results.append((method_name, products))

            if len(products) >= MIN_PRODUCTS_THRESHOLD:
                # Calculate average confidence for this result
                avg_confidence = self._calculate_avg_confidence(products)

                # Accept if we found products with good confidence (>= 0.85)
                if avg_confidence >= 0.85:
                    best_method = method_name
                    best_products = products
                    break

                # Keep track of best so far even if not good enough to stop
                if len(products) > len(best_products):
                    best_method = method_name
                    best_products = products

        # If we have a clear winner, use it
        if best_products and best_method:
            self.pipeline_stats[best_method] += 1
            return best_products

        # No single method was good enough - try merging all results
        if all_results:
            all_product_lists = [products for _, products in all_results if products]
            if all_product_lists:
                merged = self._merge_extractions(*all_product_lists)
                # Filter merged results too
                merged = filter_valid_products(merged)
                if merged:
                    self.pipeline_stats['merged'] += 1
                    return merged

        # Last resort: regex fallback on raw text
        page_content = reader.get_page(page_num)
        fallback_products = extract_products_from_text_fallback(page_content, self.pdf_path.name)
        # Filter fallback results
        fallback_products = filter_valid_products(fallback_products)
        if fallback_products:
            self.pipeline_stats['regex_fallback'] += 1
            return fallback_products

        # Nothing found
        self.empty_pages.append(page_num)
        return []

    def _calculate_avg_confidence(self, products: list[Product]) -> float:
        """Calculate average confidence across all products and fields."""
        if not products:
            return 0.0

        total_confidence = 0.0
        count = 0

        for product in products:
            for location in product.field_locations.values():
                total_confidence += location.confidence
                count += 1

        return total_confidence / count if count > 0 else 0.0

    def _try_docling(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using Docling (IBM) - AI-powered table detection."""
        if not DOCLING_AVAILABLE:
            return []

        tables = reader.extract_tables_docling(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
            # Update confidence to Docling level
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_DOCLING
            products.extend(extracted)

        return products

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

    def _try_unstructured(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using unstructured.io - document understanding with layout analysis."""
        if not UNSTRUCTURED_AVAILABLE:
            return []

        tables = reader.extract_tables_unstructured(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
            # Update confidence to unstructured level
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_UNSTRUCTURED
            products.extend(extracted)

        return products

    def _try_pymupdf(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using PyMuPDF - fast native table detection."""
        if not PYMUPDF_AVAILABLE:
            return []

        tables = reader.extract_tables_pymupdf(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
            # Update confidence to PyMuPDF level
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_PYMUPDF
            products.extend(extracted)

        return products

    def _try_img2table(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using img2table - borderless table specialist."""
        if not IMG2TABLE_AVAILABLE:
            return []

        tables = reader.extract_tables_img2table(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
            # Update confidence to img2table level
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_IMG2TABLE
            products.extend(extracted)

        return products

    def _try_pymupdf4llm(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using pymupdf4llm - layout-aware markdown text.

        First attempts to parse markdown tables, then falls back to regex
        extraction on the text content.
        """
        if not PYMUPDF4LLM_AVAILABLE:
            return []

        markdown_text = reader.extract_text_pymupdf4llm(page_num)
        if not markdown_text:
            return []

        products = []

        # First try to parse markdown tables
        md_tables = parse_markdown_tables(markdown_text)
        if md_tables:
            for table in md_tables:
                # Convert string table to expected format
                table_rows = [[{'text': cell, 'bbox': None} for cell in row] for row in table]
                extracted = extract_products_from_table(
                    table_rows, page_num, self.pdf_path.name
                )
                # Update confidence to pymupdf4llm level
                for product in extracted:
                    for location in product.field_locations.values():
                        location.confidence = CONFIDENCE_PYMUPDF4LLM
                products.extend(extracted)

        # If no products from tables, try regex extraction
        if not products:
            # Convert markdown to lines for regex extraction
            lines = [line.strip() for line in markdown_text.split('\n') if line.strip()]

            # Create a synthetic PageContent for the fallback extractor
            page_content = PageContent(
                page_number=page_num,
                lines=lines,
                raw_text=markdown_text
            )

            products = extract_products_from_text_fallback(page_content, self.pdf_path.name)

            # Update confidence for pymupdf4llm extraction
            for product in products:
                for field_name in ['item_no', 'product_name', 'description', 'pkg', 'uom']:
                    if field_name not in product.field_locations:
                        product.field_locations[field_name] = FieldLocation(
                            x0=0, y0=0, x1=0, y1=0,
                            page_number=page_num,
                            confidence=CONFIDENCE_PYMUPDF4LLM
                        )
                    else:
                        product.field_locations[field_name].confidence = CONFIDENCE_PYMUPDF4LLM

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
                elif product.field_locations[field_name].confidence < CONFIDENCE_PDFMINER:
                    # Update if existing confidence is lower
                    product.field_locations[field_name].confidence = CONFIDENCE_PDFMINER
                # else: keep existing higher confidence

        return products

    def _merge_extractions(self, *product_lists: list[Product]) -> list[Product]:
        """Merge products from multiple extractors.

        Strategy:
        - Match products by item_no AND page_number (same product on same page)
        - For each field, pick highest confidence value
        - Combine field_locations from best sources
        """
        # Group products by (item_no, page_number) to avoid merging products from different pages
        by_key: dict[tuple[str, int], list[Product]] = defaultdict(list)

        for product_list in product_lists:
            for product in product_list:
                if product.item_no:
                    key = (product.item_no, product.page_number)
                    by_key[key].append(product)

        merged_products = []

        for (item_no, page_num), products in by_key.items():
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
