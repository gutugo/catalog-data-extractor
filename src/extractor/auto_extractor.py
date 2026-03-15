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

# Re-export from submodules for backward compatibility
from .patterns import (  # noqa: F401
    CONFIDENCE_DOCLING,
    CONFIDENCE_CAMELOT,
    CONFIDENCE_UNSTRUCTURED,
    CONFIDENCE_PDFPLUMBER,
    CONFIDENCE_PYMUPDF,
    CONFIDENCE_IMG2TABLE,
    CONFIDENCE_PYMUPDF4LLM,
    CONFIDENCE_PDFMINER,
    CONFIDENCE_MULTICOLUMN,
    CONFIDENCE_REGEX,
    ITEM_NO_PATTERN,
    UOM_UNITS,
    COUNT_UOM_PATTERN,
    COUNT_COLUMN_PATTERN,
    PRODUCT_LINE_PATTERN,
    DUAL_ID_PATTERN,
    MULTILINE_ITEM_PATTERN,
    CODE_PRICE_PATTERN,
    ITEM_PREFIX_PATTERN,
    SLASH_UOM_PATTERN,
    HEADER_PATTERNS,
    FALSE_POSITIVE_PATTERNS,
    IDENTIFIER_HEADER_PATTERNS,
    PRODUCT_NAME_HEADER_PATTERNS,
    COUNT_HEADER_PATTERNS,
    SKIP_PATTERNS,
    OTC_ITEM_CODE_PATTERN,
    OTC_SKU_PATTERN,
    OTC_PRICE_PATTERN,
    PRICE_PATTERN,
    NUMERIC_ONLY_PATTERN,
)
from .parsing_utils import (  # noqa: F401
    parse_count_uom,
    is_valid_item_no,
    clean_product_name,
    combine_identifiers,
    parse_markdown_tables,
)
from .product_validation import (  # noqa: F401
    is_false_positive_item_no,
    validate_product,
    filter_valid_products,
)
from .multicolumn import (  # noqa: F401
    detect_column_gaps,
    split_words_into_columns,
    reconstruct_lines_from_words,
    detect_multicolumn_layout,
    parse_multicolumn_products,
)
from .column_detection import (  # noqa: F401
    is_header_row,
    should_skip_row,
    detect_column_mapping,
    detect_columns_robust,
    find_count_column,
    extract_products_from_table,
    _get_cell_text,
    _get_cell_bbox,
)

console = Console()


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
        self._multicolumn_detected: bool | None = None  # Cache: None=untested, True/False=result

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
        - Digital + Bordered: Camelot -> pdfplumber -> PyMuPDF -> pdfminer -> regex
        - Digital + Borderless: img2table -> pdfplumber -> Docling -> pymupdf4llm -> regex
        - Scanned: Docling -> unstructured
        - Text-only: pymupdf4llm -> pdfminer -> regex

        Stops early if a method finds products with sufficient confidence.
        Falls back to merging all results if no single method is good enough.
        """
        MIN_PRODUCTS_THRESHOLD = 1  # Minimum products to consider method successful
        all_results: list[tuple[str, list[Product]]] = []

        # Detect multi-column layout by sampling first few content pages, cache result
        if self._multicolumn_detected is None:
            self._multicolumn_detected = False
            # Sample up to 15 pages to find one with multi-column layout
            for sample_page in range(1, min(reader.total_pages + 1, 16)):
                words = reader.extract_words(sample_page)
                if len(words) < 20:
                    continue  # Skip sparse pages (covers, blanks)
                page_width, _ = reader.get_page_dimensions(sample_page)
                if detect_multicolumn_layout(words, page_width) is not None:
                    self._multicolumn_detected = True
                    break

        # If multi-column layout detected, try it first (high confidence, fast)
        if self._multicolumn_detected:
            products = self._try_multicolumn(reader, page_num)
            if products:
                self.pipeline_stats['multicolumn'] += 1
                return products

        # Get PDF classification for smart method selection
        pdf_info = reader.classify_pdf()

        # Select pipeline order based on PDF characteristics
        if pdf_info['is_scanned']:
            pipeline_methods = [
                ('docling', self._try_docling, DOCLING_AVAILABLE),
                ('unstructured', self._try_unstructured, UNSTRUCTURED_AVAILABLE),
            ]
        elif pdf_info['has_borders']:
            pipeline_methods = [
                ('camelot', self._try_camelot, CAMELOT_AVAILABLE),
                ('pdfplumber', self._try_pdfplumber_tables, True),
                ('pymupdf', self._try_pymupdf, PYMUPDF_AVAILABLE),
                ('pdfminer', self._try_pdfminer_layout, True),
            ]
        elif pdf_info['layout_type'] == 'borderless':
            pipeline_methods = [
                ('img2table', self._try_img2table, IMG2TABLE_AVAILABLE),
                ('pdfplumber', self._try_pdfplumber_tables, True),
                ('docling', self._try_docling, DOCLING_AVAILABLE),
                ('pymupdf4llm', self._try_pymupdf4llm, PYMUPDF4LLM_AVAILABLE),
            ]
        elif pdf_info['layout_type'] == 'text-only':
            pipeline_methods = [
                ('pymupdf4llm', self._try_pymupdf4llm, PYMUPDF4LLM_AVAILABLE),
                ('pdfminer', self._try_pdfminer_layout, True),
            ]
        else:
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
                merged = filter_valid_products(merged)
                if merged:
                    self.pipeline_stats['merged'] += 1
                    return merged

        # Last resort: regex fallback on raw text
        page_content = reader.get_page(page_num)
        fallback_products = extract_products_from_text_fallback(page_content, self.pdf_path.name)
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

    def _try_multicolumn(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try multi-column word-level extraction for OTC-style catalogs."""
        words = reader.extract_words(page_num)
        if not words:
            return []

        page_width, _ = reader.get_page_dimensions(page_num)

        boundary = detect_multicolumn_layout(words, page_width)

        if boundary is not None:
            left_words, right_words = split_words_into_columns(words, boundary)
            products = []
            for col_words in (left_words, right_words):
                if not col_words:
                    continue
                col_lines = reconstruct_lines_from_words(col_words)
                col_products = parse_multicolumn_products(
                    col_lines, page_num, self.pdf_path.name
                )
                products.extend(col_products)
            return filter_valid_products(products)

        # No two-column layout on this page, but multicolumn was detected globally.
        has_otc_codes = any(OTC_ITEM_CODE_PATTERN.match(w['text']) for w in words)
        if has_otc_codes:
            all_lines = reconstruct_lines_from_words(words)
            products = parse_multicolumn_products(
                all_lines, page_num, self.pdf_path.name
            )
            return filter_valid_products(products)

        return []

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
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_CAMELOT
            products.extend(extracted)

        return products

    def _try_unstructured(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using unstructured.io."""
        if not UNSTRUCTURED_AVAILABLE:
            return []

        tables = reader.extract_tables_unstructured(page_num)
        products = []

        for table_data in tables:
            extracted = extract_products_from_table(
                table_data['rows'], page_num, self.pdf_path.name
            )
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
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_IMG2TABLE
            products.extend(extracted)

        return products

    def _try_pymupdf4llm(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using pymupdf4llm - layout-aware markdown text."""
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
                table_rows = [[{'text': cell, 'bbox': None} for cell in row] for row in table]
                extracted = extract_products_from_table(
                    table_rows, page_num, self.pdf_path.name
                )
                for product in extracted:
                    for location in product.field_locations.values():
                        location.confidence = CONFIDENCE_PYMUPDF4LLM
                products.extend(extracted)

        # If no products from tables, try regex extraction
        if not products:
            lines = [line.strip() for line in markdown_text.split('\n') if line.strip()]
            page_content = PageContent(
                page_number=page_num,
                lines=lines,
                raw_text=markdown_text
            )
            products = extract_products_from_text_fallback(page_content, self.pdf_path.name)

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
            for product in extracted:
                for field_name, location in product.field_locations.items():
                    location.confidence = CONFIDENCE_PDFPLUMBER
            products.extend(extracted)

        return products

    def _try_pdfminer_layout(self, reader: PDFReader, page_num: int) -> list[Product]:
        """Try extraction using pdfminer.six layout analysis."""
        text_blocks = reader.extract_text_with_layout(page_num)

        all_lines = []
        for block in text_blocks:
            for line_data in block.get('lines', []):
                all_lines.append(line_data['text'])

        page_content = PageContent(
            page_number=page_num,
            lines=all_lines,
            raw_text='\n'.join(all_lines)
        )

        products = extract_products_from_text_fallback(page_content, self.pdf_path.name)

        for product in products:
            for field_name in ['item_no', 'product_name', 'description', 'pkg', 'uom']:
                if field_name not in product.field_locations:
                    product.field_locations[field_name] = FieldLocation(
                        x0=0, y0=0, x1=0, y1=0,
                        page_number=page_num,
                        confidence=CONFIDENCE_PDFMINER
                    )
                elif product.field_locations[field_name].confidence < CONFIDENCE_PDFMINER:
                    product.field_locations[field_name].confidence = CONFIDENCE_PDFMINER

        return products

    def _merge_extractions(self, *product_lists: list[Product]) -> list[Product]:
        """Merge products from multiple extractors."""
        by_key: dict[tuple[str, int], list[Product]] = defaultdict(list)

        for product_list in product_lists:
            for product in product_list:
                if product.item_no:
                    key = (product.item_no, product.page_number)
                    by_key[key].append(product)

        merged_products = []

        for (item_no, page_num), products in by_key.items():
            if len(products) == 1:
                merged_products.append(products[0])
                continue

            merged = self._merge_product_variants(products)
            if merged:
                merged_products.append(merged)

        return merged_products

    def _merge_product_variants(self, products: list[Product]) -> Product | None:
        """Merge multiple extractions of the same product."""
        if not products:
            return None

        base = products[0]

        # For product_name: pick longest non-empty value
        best_name = base.product_name
        for p in products[1:]:
            if p.product_name and len(p.product_name) > len(best_name):
                best_name = p.product_name

        def get_field_confidence(product: Product, field: str) -> float:
            loc = product.field_locations.get(field)
            return loc.confidence if loc else 0.0

        best_desc = base.description
        best_desc_conf = get_field_confidence(base, 'description')
        for p in products[1:]:
            conf = get_field_confidence(p, 'description')
            if conf > best_desc_conf and p.description:
                best_desc = p.description
                best_desc_conf = conf

        best_pkg = base.pkg
        best_pkg_conf = get_field_confidence(base, 'pkg')
        for p in products[1:]:
            conf = get_field_confidence(p, 'pkg')
            if conf > best_pkg_conf and p.pkg:
                best_pkg = p.pkg
                best_pkg_conf = conf

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
