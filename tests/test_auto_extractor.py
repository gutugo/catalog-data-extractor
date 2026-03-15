"""Tests for auto_extractor.py."""

from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from collections import defaultdict

from extractor.auto_extractor import (
    AutoExtractor,
    extract_products_from_text_fallback,
)
from extractor.data_model import Product, ExtractionSession, PageContent, FieldLocation


class TestExtractProductsFromTextFallback:
    def test_single_line_pattern(self):
        page = PageContent(
            page_number=1,
            lines=["12345 Widget Pro 32 ct. $9.99"],
            raw_text="12345 Widget Pro 32 ct. $9.99",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        assert len(products) == 1
        assert products[0].item_no == "12345"
        assert "Widget Pro" in products[0].product_name
        assert products[0].pkg == "32"
        assert products[0].uom == "ct"

    def test_dual_id_pattern(self):
        page = PageContent(
            page_number=1,
            lines=["A1 446761 ACNE CONTROL CLEANSER 8 OZ $16"],
            raw_text="A1 446761 ACNE CONTROL CLEANSER 8 OZ $16",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        assert len(products) == 1
        assert "A1" in products[0].item_no
        assert "446761" in products[0].item_no

    def test_code_price_pattern(self):
        page = PageContent(
            page_number=1,
            lines=["Surgical Tape", "PMS989803150181 $42.26 /EACH"],
            raw_text="Surgical Tape\nPMS989803150181 $42.26 /EACH",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        assert len(products) == 1
        assert products[0].item_no == "PMS989803150181"
        assert products[0].uom == "each"
        assert "Surgical Tape" in products[0].product_name

    def test_item_prefix_pattern(self):
        page = PageContent(
            page_number=1,
            lines=["Item # TTRS-42"],
            raw_text="Item # TTRS-42",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        assert len(products) == 1
        assert products[0].item_no == "TTRS-42"

    def test_skip_patterns(self):
        page = PageContent(
            page_number=1,
            lines=["See Page 5 for details", "12345 Widget 32 ct. $9.99"],
            raw_text="See Page 5 for details\n12345 Widget 32 ct. $9.99",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        assert len(products) == 1

    def test_section_header_skipped(self):
        page = PageContent(
            page_number=1,
            lines=["CLEANING SUPPLIES", "12345 Mop 1 ct. $5.99"],
            raw_text="CLEANING SUPPLIES\n12345 Mop 1 ct. $5.99",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        # Section header should not become product name prefix
        assert len(products) == 1
        assert "CLEANING SUPPLIES" not in products[0].product_name

    def test_empty_page(self):
        page = PageContent(page_number=1, lines=[], raw_text="")
        assert extract_products_from_text_fallback(page, "test.pdf") == []

    def test_multiline_item(self):
        page = PageContent(
            page_number=1,
            lines=["Premium Widget", "12345 32 ct. $9.99"],
            raw_text="Premium Widget\n12345 32 ct. $9.99",
        )
        products = extract_products_from_text_fallback(page, "test.pdf")
        assert len(products) == 1
        assert products[0].item_no == "12345"
        assert "Premium Widget" in products[0].product_name


class TestAutoExtractorInit:
    def test_basic_init(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        assert ext.pdf_path == Path("test.pdf")
        assert ext.session_dir == tmp_path
        assert ext.empty_pages == []
        assert ext._multicolumn_detected is None


class TestCalculateAvgConfidence:
    def test_empty(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        assert ext._calculate_avg_confidence([]) == 0.0

    def test_with_locations(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        products = [
            Product(product_name="A", field_locations={
                'item_no': FieldLocation(0, 0, 1, 1, 1, confidence=0.9),
            }),
        ]
        assert ext._calculate_avg_confidence(products) == 0.9

    def test_no_locations(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        products = [Product(product_name="A")]
        assert ext._calculate_avg_confidence(products) == 0.0

    def test_multiple_fields(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        products = [
            Product(product_name="A", field_locations={
                'a': FieldLocation(0, 0, 1, 1, 1, confidence=0.8),
                'b': FieldLocation(0, 0, 1, 1, 1, confidence=1.0),
            }),
        ]
        assert ext._calculate_avg_confidence(products) == 0.9


class TestMergeProductVariants:
    def test_picks_longest_name(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        products = [
            Product(product_name="Widget", item_no="123", page_number=1),
            Product(product_name="Widget Pro Max", item_no="123", page_number=1),
        ]
        merged = ext._merge_product_variants(products)
        assert merged.product_name == "Widget Pro Max"

    def test_empty_list(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        assert ext._merge_product_variants([]) is None

    def test_single_product(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        p = Product(product_name="Widget", item_no="123")
        # _merge_product_variants uses first product as base
        merged = ext._merge_product_variants([p])
        assert merged.product_name == "Widget"

    def test_highest_confidence_fields(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        products = [
            Product(product_name="A", item_no="123", pkg="10", page_number=1, field_locations={
                'pkg': FieldLocation(0, 0, 1, 1, 1, confidence=0.5),
            }),
            Product(product_name="A", item_no="123", pkg="20", page_number=1, field_locations={
                'pkg': FieldLocation(0, 0, 1, 1, 1, confidence=0.9),
            }),
        ]
        merged = ext._merge_product_variants(products)
        assert merged.pkg == "20"


class TestMergeExtractions:
    def test_single_list(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        products = [Product(product_name="A", item_no="123", page_number=1)]
        merged = ext._merge_extractions(products)
        assert len(merged) == 1

    def test_merges_same_product(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        list1 = [Product(product_name="Widget", item_no="123", page_number=1)]
        list2 = [Product(product_name="Widget Pro Max", item_no="123", page_number=1)]
        merged = ext._merge_extractions(list1, list2)
        assert len(merged) == 1
        assert merged[0].product_name == "Widget Pro Max"

    def test_different_pages_not_merged(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        list1 = [Product(product_name="A", item_no="123", page_number=1)]
        list2 = [Product(product_name="A", item_no="123", page_number=2)]
        merged = ext._merge_extractions(list1, list2)
        assert len(merged) == 2
