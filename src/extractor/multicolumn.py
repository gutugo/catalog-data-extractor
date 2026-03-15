"""Multi-column layout detection and parsing for OTC-style catalogs."""

from __future__ import annotations

import re

from .data_model import Product, FieldLocation
from .patterns import (
    CONFIDENCE_MULTICOLUMN,
    OTC_ITEM_CODE_PATTERN,
    OTC_PRICE_PATTERN,
    OTC_SKU_PATTERN,
)
from .parsing_utils import clean_product_name, combine_identifiers, parse_count_uom


def detect_column_gaps(words: list[dict], page_width: float) -> list[float]:
    """Find vertical gaps in word coverage that indicate column boundaries.

    Builds a histogram of word x-coverage and looks for low-density regions
    in the middle 25-75% of the page. Uses a density-based approach: a "gap"
    is a region where coverage drops to <=1 (allows for stray page numbers)
    for at least 10pt, flanked by high-density columns on both sides.

    Args:
        words: List of word dicts with x0, x1 keys
        page_width: Width of the page

    Returns:
        List of gap midpoints (x-coordinates of column boundaries)
    """
    if not words or page_width <= 0:
        return []

    # Build a coverage histogram with 1pt resolution
    bins = int(page_width) + 1
    histogram = [0] * bins

    for w in words:
        x_start = max(0, int(w['x0']))
        x_end = min(bins - 1, int(w['x1']))
        for x in range(x_start, x_end + 1):
            histogram[x] += 1

    # Look for low-density regions in the middle 25-75% of the page
    left_bound = int(page_width * 0.25)
    right_bound = int(page_width * 0.75)

    # A gap is a region where density drops to <=1 (stray words allowed)
    LOW_DENSITY_THRESHOLD = 1
    MIN_GAP_WIDTH = 10

    gaps = []
    in_gap = False
    gap_start = 0

    for x in range(left_bound, right_bound + 1):
        if histogram[x] <= LOW_DENSITY_THRESHOLD:
            if not in_gap:
                in_gap = True
                gap_start = x
        else:
            if in_gap:
                gap_width = x - gap_start
                if gap_width >= MIN_GAP_WIDTH:
                    gaps.append(gap_start + gap_width / 2)
                in_gap = False

    # Check if we ended inside a gap
    if in_gap:
        gap_width = right_bound - gap_start
        if gap_width >= MIN_GAP_WIDTH:
            gaps.append(gap_start + gap_width / 2)

    return gaps


def split_words_into_columns(words: list[dict], boundary: float) -> tuple[list[dict], list[dict]]:
    """Split words into left and right columns based on a boundary x-coordinate.

    Assigns each word to left or right based on which side its center falls.

    Args:
        words: List of word dicts with x0, x1 keys
        boundary: X-coordinate of the column boundary

    Returns:
        (left_words, right_words) tuple
    """
    left = []
    right = []
    for w in words:
        center = (w['x0'] + w['x1']) / 2
        if center < boundary:
            left.append(w)
        else:
            right.append(w)
    return left, right


def reconstruct_lines_from_words(words: list[dict], y_tolerance: float = 3.0) -> list[dict]:
    """Group words into lines by y-position proximity.

    Words within y_tolerance of each other vertically are grouped into the same line.
    Lines are sorted top-to-bottom, words within a line sorted left-to-right.

    Args:
        words: List of word dicts with text, x0, top, x1, bottom keys
        y_tolerance: Maximum vertical distance to consider words on the same line

    Returns:
        List of line dicts with keys: text, words, x0, top
    """
    if not words:
        return []

    # Sort words by vertical position first
    sorted_words = sorted(words, key=lambda w: (w['top'], w['x0']))

    lines = []
    current_line_words = [sorted_words[0]]
    current_top = sorted_words[0]['top']

    for w in sorted_words[1:]:
        if abs(w['top'] - current_top) <= y_tolerance:
            current_line_words.append(w)
        else:
            # Finalize current line
            current_line_words.sort(key=lambda w: w['x0'])
            line_text = ' '.join(w['text'] for w in current_line_words)
            lines.append({
                'text': line_text,
                'words': current_line_words,
                'x0': current_line_words[0]['x0'],
                'top': current_top,
            })
            current_line_words = [w]
            current_top = w['top']

    # Don't forget the last line
    if current_line_words:
        current_line_words.sort(key=lambda w: w['x0'])
        line_text = ' '.join(w['text'] for w in current_line_words)
        lines.append({
            'text': line_text,
            'words': current_line_words,
            'x0': current_line_words[0]['x0'],
            'top': current_top,
        })

    return lines


def detect_multicolumn_layout(words: list[dict], page_width: float) -> float | None:
    """Detect whether the page has a two-column OTC-style layout.

    Checks:
    1. A column gap exists near the center of the page
    2. OTC item codes (A1, B12, etc.) appear at two distinct x-positions

    Args:
        words: List of word dicts
        page_width: Width of the page

    Returns:
        Column boundary x-coordinate if detected, None otherwise
    """
    gaps = detect_column_gaps(words, page_width)
    if not gaps:
        return None

    # Use the gap closest to the center
    center = page_width / 2
    best_gap = min(gaps, key=lambda g: abs(g - center))

    # Verify: look for OTC item codes at two distinct x-positions
    code_x_positions = []
    for w in words:
        if OTC_ITEM_CODE_PATTERN.match(w['text']):
            code_x_positions.append(w['x0'])

    if len(code_x_positions) < 2:
        return None

    # Check that codes appear on both sides of the gap
    left_codes = [x for x in code_x_positions if x < best_gap]
    right_codes = [x for x in code_x_positions if x >= best_gap]

    if left_codes and right_codes:
        return best_gap

    return None


def parse_multicolumn_products(lines: list[dict], page_number: int,
                                source_file: str) -> list[Product]:
    """Parse products from reconstructed lines within a single column.

    Each product follows this pattern:
      Line 1: [Code: A1] [opt. description words] [$Price]
      Line 2: [Description continuation]  (optional)
      Line 3: [6-digit UPC/SKU] [opt. desc] [Size Unit]  (optional)

    Section headers (ALL CAPS or Title Case, no code/price) are skipped.

    Args:
        lines: Reconstructed line dicts (from reconstruct_lines_from_words)
        page_number: Page number for product metadata
        source_file: Source PDF filename

    Returns:
        List of Product objects
    """
    products = []
    i = 0

    while i < len(lines):
        line = lines[i]
        line_words = line['words']

        if not line_words:
            i += 1
            continue

        first_word = line_words[0]['text']

        # Check if this line starts a product (first word is OTC item code)
        if not OTC_ITEM_CODE_PATTERN.match(first_word):
            i += 1
            continue

        item_code = first_word

        # Extract price from rightmost words if present
        price_found = False
        desc_words = []

        for w in line_words[1:]:
            if OTC_PRICE_PATTERN.match(w['text']):
                price_found = True
                continue
            if w['text'] == '$':
                price_found = True
                continue
            if price_found and re.match(r'^\d+$', w['text']):
                continue
            if w['text'] == '00' and price_found:
                continue
            desc_words.append(w['text'])

        description_parts = desc_words
        upc_sku = ''
        pkg = ''
        uom = ''

        # Look ahead for continuation lines
        j = i + 1
        while j < len(lines):
            next_line = lines[j]
            next_words = next_line['words']

            if not next_words:
                j += 1
                continue

            next_first = next_words[0]['text']

            # If next line starts with an OTC item code, it's a new product
            if OTC_ITEM_CODE_PATTERN.match(next_first):
                break

            # If next line starts with a 5-6 digit SKU, it's the UPC/size line
            if OTC_SKU_PATTERN.match(next_first):
                upc_sku = next_first

                remaining = [w['text'] for w in next_words[1:]]

                if len(remaining) >= 2:
                    potential_count = remaining[-2]
                    potential_unit = remaining[-1]
                    combined = f"{potential_count} {potential_unit}"
                    parsed_pkg, parsed_uom = parse_count_uom(combined)
                    if parsed_uom:
                        pkg = parsed_pkg
                        uom = parsed_uom
                        description_parts.extend(remaining[:-2])
                    else:
                        parsed_pkg, parsed_uom = parse_count_uom(remaining[-1])
                        if parsed_uom:
                            pkg = parsed_pkg
                            uom = parsed_uom
                            description_parts.extend(remaining[:-1])
                        else:
                            description_parts.extend(remaining)
                elif len(remaining) == 1:
                    parsed_pkg, parsed_uom = parse_count_uom(remaining[0])
                    if parsed_uom:
                        pkg = parsed_pkg
                        uom = parsed_uom
                    else:
                        description_parts.extend(remaining)

                j += 1
                break
            else:
                line_text = next_line['text']
                is_header = (
                    re.match(r'^[A-Z][A-Z\s&,\-/]+$', line_text) and
                    len(line_text) > 3 and
                    not OTC_PRICE_PATTERN.search(line_text)
                )
                if is_header:
                    j += 1
                    break

                for w in next_words:
                    if not OTC_PRICE_PATTERN.match(w['text']) and w['text'] != '$' and w['text'] != '00':
                        description_parts.append(w['text'])
                j += 1

        combined_item_no = combine_identifiers(item_code, upc_sku, '')

        product_name = clean_product_name(' '.join(description_parts))

        products.append(Product(
            product_name=product_name,
            description='',
            item_no=combined_item_no,
            pkg=pkg,
            uom=uom,
            page_number=page_number,
            source_file=source_file,
            field_locations={
                'item_no': FieldLocation(
                    x0=0, y0=0, x1=0, y1=0,
                    page_number=page_number,
                    confidence=CONFIDENCE_MULTICOLUMN
                ),
            },
        ))

        i = j

    return products
