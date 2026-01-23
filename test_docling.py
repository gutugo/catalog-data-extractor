#!/usr/bin/env python3
"""Test script for Docling-only extraction."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from extractor.pdf_reader import PDFReader, DOCLING_AVAILABLE
from extractor.auto_extractor import extract_products_from_table, CONFIDENCE_DOCLING

def test_docling_extraction(pdf_path: str, page_num: int = 1):
    """Test Docling extraction on a specific page."""

    if not DOCLING_AVAILABLE:
        print("ERROR: Docling is not available!")
        return

    print(f"Testing Docling extraction on: {pdf_path}")
    print(f"Page: {page_num}")
    print("-" * 50)

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        return

    with PDFReader(pdf_path) as reader:
        print(f"Total pages: {reader.total_pages}")

        if page_num > reader.total_pages:
            print(f"ERROR: Page {page_num} exceeds total pages")
            return

        print(f"\nExtracting tables with Docling from page {page_num}...")
        tables = reader.extract_tables_docling(page_num)

        if not tables:
            print("No tables found by Docling on this page.")
            return

        print(f"Found {len(tables)} table(s)")

        for i, table in enumerate(tables):
            print(f"\n=== Table {i+1} ===")
            print(f"BBox: {table.get('bbox')}")
            print(f"Rows: {len(table.get('rows', []))}")

            # Print first few rows
            rows = table.get('rows', [])
            for j, row in enumerate(rows[:5]):  # Show first 5 rows
                row_text = [cell.get('text', '') for cell in row]
                print(f"  Row {j}: {row_text}")

            if len(rows) > 5:
                print(f"  ... and {len(rows) - 5} more rows")

            # Try to extract products
            print(f"\nExtracting products from table {i+1}...")
            products = extract_products_from_table(rows, page_num, pdf_path.name)

            if products:
                print(f"Found {len(products)} product(s):")
                for p in products[:5]:  # Show first 5 products
                    print(f"  - {p.item_no}: {p.product_name[:50]}..." if len(p.product_name) > 50 else f"  - {p.item_no}: {p.product_name}")
                if len(products) > 5:
                    print(f"  ... and {len(products) - 5} more products")
            else:
                print("No products extracted from this table.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_docling.py <pdf_path> [page_num]")
        print("\nExample: python test_docling.py catalogs/my-catalog.pdf 1")
        sys.exit(1)

    pdf_path = sys.argv[1]
    page_num = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    test_docling_extraction(pdf_path, page_num)
