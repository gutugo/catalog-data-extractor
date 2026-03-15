"""Tests for auto_extractor.py."""

from unittest.mock import MagicMock, patch
from pathlib import Path

from extractor.auto_extractor import (
    AutoExtractor,
    extract_products_from_text_fallback,
)
from extractor.glm_ocr import GlmOcrClient, GlmOcrError
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
        assert isinstance(ext._glm_client, GlmOcrClient)


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
