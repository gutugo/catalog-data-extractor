"""Regex patterns and constants for catalog data extraction."""

import re

# Confidence scores for different extraction methods
CONFIDENCE_DOCLING = 0.98
CONFIDENCE_CAMELOT = 1.0
CONFIDENCE_UNSTRUCTURED = 0.92
CONFIDENCE_PDFPLUMBER = 0.95
CONFIDENCE_PYMUPDF = 0.93
CONFIDENCE_IMG2TABLE = 0.90
CONFIDENCE_PYMUPDF4LLM = 0.85
CONFIDENCE_PDFMINER = 0.8
CONFIDENCE_MULTICOLUMN = 0.95
CONFIDENCE_REGEX = 0.5

# Patterns for identifying valid item numbers
ITEM_NO_PATTERN = re.compile(
    r'^('
    r'[A-Z]{0,4}\d{4,}[-\dA-Z]*'
    r'|[A-Z]{1,6}-(?=[\dA-Z-]*\d)[A-Z\d][\dA-Z-]*'
    r'|[A-Z]{2,6}\d+[A-Z\d]*'
    r'|\d{4,5}'
    r')$',
    re.IGNORECASE
)

# UOM_UNITS is the single source of truth for unit patterns
UOM_UNITS = r'ct|pk|pack|bx|oz|gm|ml|lb|qt|pt|bag|roll|pr|dz|set|btl|tube|jar|can|box|ea|sheets?|pair|kit|rl|cs|each|case|carton|drum|gal|pail|tub'

COUNT_UOM_PATTERN = re.compile(
    rf'^([\d,]+)\s*({UOM_UNITS})\.?$',
    re.IGNORECASE
)

COUNT_COLUMN_PATTERN = re.compile(
    rf'^[\d,]+\s*[/]?\s*({UOM_UNITS})?\.?$',
    re.IGNORECASE
)

PRODUCT_LINE_PATTERN = re.compile(
    rf'^(\d{{4,5}})\s+(.+?)\s+(\d+\s*(?:{UOM_UNITS})\.?)\s+\$(\d+\.?\d*)$',
    re.IGNORECASE
)

DUAL_ID_PATTERN = re.compile(
    rf'^([A-Z]\d{{1,3}})\s+(\d{{5,6}})\s+(.+?)\s+(\d+\s*(?:{UOM_UNITS})\.?)\s+\$(\d+\.?\d*)$',
    re.IGNORECASE
)

MULTILINE_ITEM_PATTERN = re.compile(
    rf'^(\d{{4,5}})\s+(\d+\s*(?:{UOM_UNITS})\.?)\s+\$(\d+\.?\d*)$',
    re.IGNORECASE
)

CODE_PRICE_PATTERN = re.compile(
    r'^([A-Z]{2,4}(?=[\dA-Z-]*\d)[\dA-Z-]+)\s+\$([\d,]+\.?\d*)\s*/?(EACH|PAIR|RL|BX|CS|PK|EA|CT)\b',
    re.IGNORECASE
)

ITEM_PREFIX_PATTERN = re.compile(
    r'Item\s*#?\s*:?\s*([A-Z]{0,4}(?=[\dA-Z-]*\d)[\dA-Z][\dA-Z-]*)',
    re.IGNORECASE
)

SLASH_UOM_PATTERN = re.compile(
    rf'^([\d,]+)\s*/\s*({UOM_UNITS})$',
    re.IGNORECASE
)

HEADER_PATTERNS = [
    re.compile(r'^Item\s*#?$', re.IGNORECASE),
    re.compile(r'^Description$', re.IGNORECASE),
    re.compile(r'^Count$', re.IGNORECASE),
    re.compile(r'^Price$', re.IGNORECASE),
    re.compile(r'^SKU\s*#?$', re.IGNORECASE),
    re.compile(r'^UPC', re.IGNORECASE),
    re.compile(r'^Product\s*(Name|Code)?$', re.IGNORECASE),
]

FALSE_POSITIVE_PATTERNS = [
    re.compile(r'^\d+\.?\d*\s*(kg|g|lb|oz|cm|mm|m|inches?|in|ft|feet)\.?$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*x\s*\d+', re.IGNORECASE),
    re.compile(r'^\d+\s*/\s*\d+\s*(mm|cm|m|kg|g).*$', re.IGNORECASE),
    re.compile(r'^\d+\s*(mm|cm|m)\s*diameter$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*(minutes?|mins?|hours?|hrs?|seconds?|secs?|days?)\.?$', re.IGNORECASE),
    re.compile(r'^[\d,\s]+\s*(mins?|hours?|secs?)$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*%$'),
    re.compile(r'^\d+\.?\d*\s*°?[CF]$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*(V|A|W|Hz|kW|mA|VA)$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*(bar|psi|kPa|MPa|Pa)$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*(L|ml|gal|liters?|litres?)$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*(rpm|m/s|km/h|mph)$', re.IGNORECASE),
    re.compile(r'^\d+\.?\d*\s*[-~]\s*\d+\.?\d*$'),
    re.compile(r'^IP[X\d]\d?$', re.IGNORECASE),
    re.compile(r'^Class\s*[1-9IVX]+$', re.IGNORECASE),
    re.compile(r'^(BS|EN|ISO|IEC|ANSI|UL|CE|CSA)\s*\d+', re.IGNORECASE),
    re.compile(r'^[A-Za-z\s]+:$'),
    re.compile(r'^(Yes|No|N/?A|None|Standard|Optional|Included|Available)$', re.IGNORECASE),
    re.compile(r'^\d+\s*(cm|mm|m)\s+\w+', re.IGNORECASE),
    re.compile(r'^[A-Za-z]+\s+[A-Za-z]*\s*\d+\s+[A-Za-z]+', re.IGNORECASE),
]

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
        re.compile(r'^NDC$', re.IGNORECASE),
        re.compile(r'^MPN$', re.IGNORECASE),
    ],
}

PRODUCT_NAME_HEADER_PATTERNS = [
    re.compile(r'^Description$', re.IGNORECASE),
    re.compile(r'^Product\s*(Name)?$', re.IGNORECASE),
    re.compile(r'^Item\s*(Name|Description)$', re.IGNORECASE),
    re.compile(r'^Name$', re.IGNORECASE),
]

COUNT_HEADER_PATTERNS = [
    re.compile(r'^Count$', re.IGNORECASE),
    re.compile(r'^Qty\.?$', re.IGNORECASE),
    re.compile(r'^Quantity$', re.IGNORECASE),
    re.compile(r'^Pack\s*(Size)?$', re.IGNORECASE),
    re.compile(r'^Size$', re.IGNORECASE),
    re.compile(r'^Unit$', re.IGNORECASE),
]

SKIP_PATTERNS = [
    re.compile(r'See Page', re.IGNORECASE),
    re.compile(r'Please note', re.IGNORECASE),
    re.compile(r'Keep this catalog', re.IGNORECASE),
    re.compile(r'^\*', re.IGNORECASE),
]

# --- Multi-column OTC catalog patterns ---
OTC_ITEM_CODE_PATTERN = re.compile(r'^[A-Z]\d{1,3}$')
OTC_SKU_PATTERN = re.compile(r'^\d{5,6}$')
OTC_PRICE_PATTERN = re.compile(r'^\$\d+$')

# Patterns for robust column detection (content-based)
PRICE_PATTERN = re.compile(r'^\$[\d,]+\.?\d*$')
NUMERIC_ONLY_PATTERN = re.compile(r'^[\d,]+$')
