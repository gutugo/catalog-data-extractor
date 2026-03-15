"""Automatic extraction logic for catalog data using GLM-OCR."""

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
from .glm_ocr import GlmOcrClient, GlmOcrError

# Re-export from submodules for backward compatibility
from .patterns import (  # noqa: F401
    CONFIDENCE_GLM_OCR,
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
    parse_html_tables,
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
            upc_code = dual_match.group(1)
            sku_code = dual_match.group(2)
            product_name = dual_match.group(3).strip()
            count_str = dual_match.group(4).strip()

            combined_item_no = combine_identifiers(upc_code, sku_code, '')

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

        # Try multi-line item pattern
        multi_match = MULTILINE_ITEM_PATTERN.match(line)
        if multi_match:
            item_no = multi_match.group(1)
            count_str = multi_match.group(2).strip()
            product_name = ' '.join(pending_description) if pending_description else ''

            pkg, uom = parse_count_uom(count_str)

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

        # Try CODE $PRICE /UNIT pattern
        code_price_match = CODE_PRICE_PATTERN.match(line)
        if code_price_match:
            item_no = code_price_match.group(1)
            uom = code_price_match.group(3).lower()

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

            product_name = ''
            uom = ''

            rest_of_line = line[item_prefix_match.end():].strip()
            if rest_of_line:
                product_name = rest_of_line

            if pending_description:
                if product_name:
                    product_name = ' '.join(pending_description) + ' ' + product_name
                else:
                    product_name = ' '.join(pending_description)
                pending_description = []

            j = i + 1
            while j < len(lines) and j < i + 5:
                next_line = lines[j].strip()
                price_uom_match = re.search(rf'\$[\d.]+\s*/?\s*({UOM_UNITS})\b', next_line, re.IGNORECASE)
                if price_uom_match:
                    uom = price_uom_match.group(1).lower()
                    break
                next_line_parts = next_line.split()
                if ITEM_PREFIX_PATTERN.search(next_line) or is_valid_item_no(next_line_parts[0] if next_line_parts else ''):
                    break
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

        # Check for standalone alphanumeric item codes
        if is_valid_item_no(line):
            item_no = line
            product_name = ''
            uom = ''

            j = i + 1
            while j < len(lines) and j < i + 5:
                next_line = lines[j].strip()
                price_uom_match = re.search(rf'\$[\d.]+\s*/?\s*({UOM_UNITS})\b', next_line, re.IGNORECASE)
                if price_uom_match:
                    uom = price_uom_match.group(1).lower()
                    break
                if is_valid_item_no(next_line):
                    break
                if next_line and not next_line.startswith('$'):
                    if product_name:
                        product_name += ' ' + next_line
                    else:
                        product_name = next_line
                j += 1

            if not product_name and pending_description:
                product_name = ' '.join(pending_description)
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
            is_section_header = (
                (re.match(r'^[A-Z][A-Z\s&,\-]+$', line) and len(line) > 3) or
                re.match(r'^(Page \d+|Section \d+|Category:|Index|Table of Contents)$', line, re.IGNORECASE)
            )
            if not is_section_header:
                pending_description.append(line)

        i += 1

    return products


class AutoExtractor:
    """Handles automatic extraction from catalogs using GLM-OCR.

    Renders each PDF page to an image, sends it to GLM-OCR via Ollama,
    and parses the returned markdown into Product objects.
    """

    def __init__(self, pdf_path: Path, session_dir: Path):
        self.pdf_path = Path(pdf_path)
        self.session_dir = session_dir
        self.empty_pages: list[int] = []
        self.pipeline_stats: dict[str, int] = defaultdict(int)
        self._glm_client = GlmOcrClient()

    def run(self, progress_callback=None, show_console=True) -> ExtractionSession:
        """Run GLM-OCR extraction on all pages.

        Args:
            progress_callback: Optional callback function(page_num, total_pages, products_count)
            show_console: Whether to show console output (default True).
        """
        if show_console:
            console.print(f"[bold blue]Auto-extracting (GLM-OCR):[/bold blue] {self.pdf_path.name}")

            if not self._glm_client.check_availability():
                console.print("[red]Error: GLM-OCR not available. Ensure Ollama is running with glm-ocr model.[/red]")
                console.print(f"[dim]Ollama host: {self._glm_client.host}[/dim]")
                console.print("[dim]Install: ollama pull glm-ocr[/dim]")

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
        """Extract products from a single page using GLM-OCR."""
        # Render page to image
        png_bytes = reader.render_page_to_png(page_num)
        if not png_bytes:
            self.empty_pages.append(page_num)
            return []

        # Send to GLM-OCR
        try:
            markdown_text = self._glm_client.recognize_table(png_bytes)
        except GlmOcrError as e:
            console.print(f"[red]GLM-OCR error on page {page_num}: {e}[/red]")
            self.empty_pages.append(page_num)
            return []

        if not markdown_text:
            self.empty_pages.append(page_num)
            return []

        products = []

        # Try parsing tables (HTML first since GLM-OCR often returns HTML, then markdown)
        md_tables = parse_html_tables(markdown_text) or parse_markdown_tables(markdown_text)
        if md_tables:
            for table in md_tables:
                table_rows = [[{'text': cell, 'bbox': None} for cell in row] for row in table]
                extracted = extract_products_from_table(
                    table_rows, page_num, self.pdf_path.name
                )
                for p in extracted:
                    for loc in p.field_locations.values():
                        loc.confidence = CONFIDENCE_GLM_OCR
                products.extend(extracted)

        # Fallback: regex on raw markdown text
        if not products:
            lines = [line.strip() for line in markdown_text.split('\n') if line.strip()]
            page_content = PageContent(
                page_number=page_num,
                lines=lines,
                raw_text=markdown_text,
            )
            products = extract_products_from_text_fallback(page_content, self.pdf_path.name)

            for p in products:
                for field_name in ['item_no', 'product_name', 'description', 'pkg', 'uom']:
                    if field_name not in p.field_locations:
                        p.field_locations[field_name] = FieldLocation(
                            x0=0, y0=0, x1=0, y1=0,
                            page_number=page_num,
                            confidence=CONFIDENCE_GLM_OCR,
                        )

        products = filter_valid_products(products)

        if products:
            self.pipeline_stats['glm_ocr'] += 1
        else:
            self.empty_pages.append(page_num)

        return products

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
