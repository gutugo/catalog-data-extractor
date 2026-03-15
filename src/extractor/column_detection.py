"""Column detection and table-to-product extraction."""

from __future__ import annotations

from collections import defaultdict

from .data_model import Product, FieldLocation
from .patterns import (
    COUNT_COLUMN_PATTERN,
    COUNT_HEADER_PATTERNS,
    COUNT_UOM_PATTERN,
    HEADER_PATTERNS,
    IDENTIFIER_HEADER_PATTERNS,
    ITEM_NO_PATTERN,
    PRICE_PATTERN,
    PRODUCT_NAME_HEADER_PATTERNS,
    SKIP_PATTERNS,
)
from .parsing_utils import (
    clean_product_name,
    combine_identifiers,
    is_valid_item_no,
    parse_count_uom,
)


def _get_cell_text(cell) -> str:
    """Get text from a cell, handling both string and dict formats."""
    if isinstance(cell, dict):
        return (cell.get('text') or '').strip()
    return (cell or '').strip()


def _get_cell_bbox(cell) -> tuple | None:
    """Get bbox from a cell, handling both string and dict formats."""
    if isinstance(cell, dict):
        return cell.get('bbox')
    return None


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
            if ITEM_NO_PATTERN.match(text):
                col_scores[col_idx]['item_no'] += 1.0

            if PRICE_PATTERN.match(text):
                col_scores[col_idx]['price'] += 1.0

            if COUNT_UOM_PATTERN.match(text) or COUNT_COLUMN_PATTERN.match(text):
                col_scores[col_idx]['count'] += 1.0

            if len(text) > 15 and not ITEM_NO_PATTERN.match(text) and not PRICE_PATTERN.match(text):
                col_scores[col_idx]['product_name'] += 0.5

            if len(text) <= 15 and text.isalnum() and any(c.isdigit() for c in text):
                if len(text) >= 10:
                    col_scores[col_idx]['upc'] += 0.8
                elif len(text) >= 4:
                    col_scores[col_idx]['sku'] += 0.5

    # Calculate average column widths
    avg_widths = {}
    for col_idx, widths in col_widths.items():
        avg_widths[col_idx] = sum(widths) / len(widths) if widths else 0

    # Boost product_name score for wide columns
    if avg_widths:
        max_width = max(avg_widths.values())
        for col_idx, width in avg_widths.items():
            if width > max_width * 0.6:
                col_scores[col_idx]['product_name'] += 0.5

    # Assign columns by highest score, avoiding duplicates
    result = dict(header_mapping)
    assigned_cols = set(result.values())

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

        if best_col >= 0 and best_score >= 0.5:
            result[field_name] = best_col
            assigned_cols.add(best_col)

    return result


def find_count_column(table: list[list]) -> int:
    """Find which column contains count data (e.g., '32 ct.', '1 pk').

    Works with both string lists and dict lists (with 'text' and 'bbox' keys).

    Returns the column index, or -1 if no valid count column found.
    """
    if not table:
        return -1

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
            if COUNT_COLUMN_PATTERN.match(cell_text):
                count_matches += 1

        if total_cells > 0 and count_matches >= 1:
            match_rate = count_matches / total_cells
            min_rate = 1.0 if total_cells <= 2 else 0.5
            if match_rate >= min_rate and match_rate > best_match_rate:
                best_match_rate = match_rate
                best_col = col_idx

    return best_col


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

    # Detect column mapping
    if use_robust_detection:
        col_mapping = detect_columns_robust(table)
    else:
        col_mapping = detect_column_mapping(table)

    # Determine which column contains count data (fallback detection)
    count_col = col_mapping.get('count', -1)
    if count_col < 0:
        count_col = find_count_column(table)

    def row_to_strings(row):
        return [_get_cell_text(cell) for cell in row]

    # Determine identifier columns
    id_cols = {}
    if 'upc' in col_mapping:
        id_cols['upc'] = col_mapping['upc']
    if 'sku' in col_mapping:
        id_cols['sku'] = col_mapping['sku']
    if 'item_no' in col_mapping:
        id_cols['item_no'] = col_mapping['item_no']

    name_col = col_mapping.get('product_name', -1)
    use_positional = len(id_cols) == 0

    for row in table:
        row_strings = row_to_strings(row)

        if is_header_row(row_strings) or should_skip_row(row_strings):
            continue

        if len(row) < 2:
            continue

        item_no = ''
        sku = ''
        upc = ''
        field_locations = {}

        if use_positional:
            item_no = _get_cell_text(row[0])
            if not is_valid_item_no(item_no):
                continue
            name_col = 1

            item_bbox = _get_cell_bbox(row[0])
            if item_bbox:
                field_locations['item_no'] = FieldLocation(
                    x0=item_bbox[0], y0=item_bbox[1],
                    x1=item_bbox[2], y1=item_bbox[3],
                    page_number=page_number,
                    confidence=1.0
                )
        else:
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

            if name_col < 0:
                used_cols = set(id_cols.values())
                if count_col >= 0:
                    used_cols.add(count_col)
                for idx in range(len(row)):
                    if idx not in used_cols:
                        cell_text = _get_cell_text(row[idx])
                        if cell_text and not cell_text.startswith('$'):
                            name_col = idx
                            break

        product_name = ''
        if 0 <= name_col < len(row):
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

        count_str = ''
        count_bbox = None
        if 0 <= count_col < len(row):
            count_str = _get_cell_text(row[count_col])
            count_bbox = _get_cell_bbox(row[count_col])

        pkg, uom = parse_count_uom(count_str)

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

        combined_item_no = combine_identifiers(upc, sku, item_no)

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
