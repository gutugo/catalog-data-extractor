"""Product validation and false-positive filtering."""

from __future__ import annotations

import re

from .patterns import FALSE_POSITIVE_PATTERNS


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
    if len(cleaned) > 15 and re.match(r'^[A-Za-z]+\d+[A-Za-z]+', cleaned):
        return True

    # Additional heuristic: if it contains newlines, likely a spec cell
    if '\n' in value:
        return True

    # Real SKUs/item numbers almost always contain at least one digit
    if not any(c.isdigit() for c in cleaned):
        return True

    # Real item numbers rarely contain spaces
    value_stripped = value.strip()
    if ' ' in value_stripped and ' / ' not in value_stripped:
        words = value_stripped.split()
        if len(words) >= 3:
            return True
        if any(w.islower() or (w[0].isupper() and w[1:].islower()) for w in words if len(w) > 1):
            return True

    return False


def validate_product(product) -> bool:
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


def filter_valid_products(products: list) -> list:
    """Filter out false positive products from extraction results.

    Args:
        products: List of extracted products

    Returns:
        Filtered list with false positives removed
    """
    return [p for p in products if validate_product(p)]
