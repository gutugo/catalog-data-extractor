"""Tests for column_detection.py."""

from extractor.column_detection import (
    _get_cell_text,
    _get_cell_bbox,
    is_header_row,
    should_skip_row,
    detect_column_mapping,
    detect_columns_robust,
    find_count_column,
    extract_products_from_table,
)


class TestGetCellText:
    def test_string(self):
        assert _get_cell_text("hello") == "hello"

    def test_dict(self):
        assert _get_cell_text({'text': 'hello', 'bbox': None}) == "hello"

    def test_none(self):
        assert _get_cell_text(None) == ""

    def test_empty_dict(self):
        assert _get_cell_text({}) == ""

    def test_strips_whitespace(self):
        assert _get_cell_text("  hello  ") == "hello"


class TestGetCellBbox:
    def test_dict_with_bbox(self):
        assert _get_cell_bbox({'text': 'x', 'bbox': (1, 2, 3, 4)}) == (1, 2, 3, 4)

    def test_dict_without_bbox(self):
        assert _get_cell_bbox({'text': 'x'}) is None

    def test_string(self):
        assert _get_cell_bbox("text") is None

    def test_none(self):
        assert _get_cell_bbox(None) is None


class TestIsHeaderRow:
    def test_header_row(self):
        assert is_header_row(["Item #", "Description", "Count", "Price"])

    def test_data_row(self):
        assert not is_header_row(["12345", "Widget", "32 ct.", "$9.99"])

    def test_single_header_in_large_row(self):
        # Only 1 header in 4 cells - not enough
        assert not is_header_row(["Item #", "Widget", "32", "$9.99"])

    def test_two_headers(self):
        assert is_header_row(["Item #", "Description", "32", "$9.99"])

    def test_small_row_majority(self):
        # 2 cells, need majority (2/2) to be headers
        assert is_header_row(["Item #", "Description"])

    def test_empty_row(self):
        assert not is_header_row([])

    def test_all_empty_cells(self):
        assert not is_header_row(["", "", ""])


class TestShouldSkipRow:
    def test_see_page(self):
        assert should_skip_row(["See Page 5 for details"])

    def test_please_note(self):
        assert should_skip_row(["", "Please note: prices subject to change"])

    def test_asterisk(self):
        assert should_skip_row(["*Footnote text"])

    def test_normal_row(self):
        assert not should_skip_row(["12345", "Widget", "32 ct."])


class TestDetectColumnMapping:
    def test_standard_headers(self):
        table = [
            ["Item #", "Description", "Count", "Price"],
            ["12345", "Widget", "32 ct.", "$9.99"],
        ]
        mapping = detect_column_mapping(table)
        assert mapping.get('item_no') == 0
        assert mapping.get('product_name') == 1
        assert mapping.get('count') == 2

    def test_upc_sku_headers(self):
        table = [
            ["UPC", "SKU #", "Description", "Size"],
            ["012345678", "ABC123", "Widget", "Large"],
        ]
        mapping = detect_column_mapping(table)
        assert mapping.get('upc') == 0
        assert mapping.get('sku') == 1
        assert mapping.get('product_name') == 2

    def test_empty_table(self):
        assert detect_column_mapping([]) == {}

    def test_no_headers(self):
        table = [
            ["12345", "Widget", "32 ct.", "$9.99"],
        ]
        mapping = detect_column_mapping(table)
        # No headers detected
        assert len(mapping) == 0


class TestDetectColumnsRobust:
    def test_content_based_detection(self):
        table = [
            ["12345", "Widget Pro X", "32 ct.", "$9.99"],
            ["67890", "Gadget Ultra Plus Max", "16 pk", "$19.99"],
            ["11111", "Another Product Name Here", "8 oz", "$5.99"],
        ]
        mapping = detect_columns_robust(table)
        assert 'item_no' in mapping

    def test_empty_table(self):
        assert detect_columns_robust([]) == {}

    def test_with_header(self):
        table = [
            ["Item #", "Description", "Count", "Price"],
            ["12345", "Widget", "32 ct.", "$9.99"],
        ]
        mapping = detect_columns_robust(table)
        assert mapping.get('item_no') == 0
        assert mapping.get('product_name') == 1


class TestFindCountColumn:
    def test_finds_count(self):
        table = [
            ["12345", "Widget", "32 ct.", "$9.99"],
            ["67890", "Gadget", "16 pk", "$19.99"],
        ]
        assert find_count_column(table) == 2

    def test_no_count(self):
        table = [
            ["12345", "Widget", "$9.99"],
            ["67890", "Gadget", "$19.99"],
        ]
        assert find_count_column(table) == -1

    def test_empty_table(self):
        assert find_count_column([]) == -1


class TestExtractProductsFromTable:
    def test_positional_extraction(self):
        table = [
            [{'text': '12345', 'bbox': (10, 10, 50, 20)},
             {'text': 'Widget', 'bbox': (50, 10, 200, 20)},
             {'text': '32 ct.', 'bbox': (200, 10, 250, 20)}],
        ]
        products = extract_products_from_table(table, 1, "test.pdf")
        assert len(products) == 1
        assert products[0].item_no == "12345"
        assert products[0].product_name == "Widget"

    def test_header_row_skipped(self):
        table = [
            [{'text': 'Item #', 'bbox': None}, {'text': 'Description', 'bbox': None}],
            [{'text': '12345', 'bbox': None}, {'text': 'Widget', 'bbox': None}],
        ]
        products = extract_products_from_table(table, 1, "test.pdf")
        assert len(products) == 1

    def test_empty_table(self):
        assert extract_products_from_table([], 1, "test.pdf") == []

    def test_field_locations_populated(self):
        table = [
            [{'text': '12345', 'bbox': (10, 20, 50, 30)},
             {'text': 'Widget', 'bbox': (50, 20, 200, 30)}],
        ]
        products = extract_products_from_table(table, 1, "test.pdf")
        assert len(products) == 1
        assert 'item_no' in products[0].field_locations

    def test_with_header_mapping(self):
        table = [
            [{'text': 'UPC', 'bbox': None}, {'text': 'SKU #', 'bbox': None},
             {'text': 'Description', 'bbox': None}],
            [{'text': '012345678901', 'bbox': None}, {'text': 'ABC123', 'bbox': None},
             {'text': 'Widget Pro', 'bbox': None}],
        ]
        products = extract_products_from_table(table, 1, "test.pdf")
        assert len(products) == 1
        assert "012345678901" in products[0].item_no

    def test_skip_row_filtered(self):
        table = [
            [{'text': '12345', 'bbox': None}, {'text': 'Widget', 'bbox': None}],
            [{'text': 'See Page 5', 'bbox': None}, {'text': '', 'bbox': None}],
        ]
        products = extract_products_from_table(table, 1, "test.pdf")
        assert len(products) == 1

    def test_string_cells(self):
        table = [
            ["12345", "Widget", "32 ct."],
            ["67890", "Gadget", "16 pk"],
        ]
        products = extract_products_from_table(table, 1, "test.pdf")
        assert len(products) == 2
