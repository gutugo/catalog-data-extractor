"""Tests for parsing_utils.py."""

from extractor.parsing_utils import (
    parse_count_uom,
    is_valid_item_no,
    clean_product_name,
    combine_identifiers,
    parse_markdown_tables,
)


class TestParseCountUom:
    def test_basic_ct(self):
        assert parse_count_uom("32 ct.") == ("32", "ct")

    def test_basic_pk(self):
        assert parse_count_uom("100 pk") == ("100", "pk")

    def test_comma_count(self):
        assert parse_count_uom("1,000 ct.") == ("1000", "ct")

    def test_slash_format(self):
        assert parse_count_uom("2,500/RL") == ("2500", "rl")

    def test_each(self):
        assert parse_count_uom("100/EACH") == ("100", "each")

    def test_no_unit(self):
        pkg, uom = parse_count_uom("123")
        assert pkg == "123"
        assert uom == ""

    def test_empty_string(self):
        assert parse_count_uom("") == ("", "")

    def test_no_space(self):
        assert parse_count_uom("16oz") == ("16", "oz")

    def test_whitespace(self):
        assert parse_count_uom("  32 ct.  ") == ("32", "ct")


class TestIsValidItemNo:
    def test_four_digit(self):
        assert is_valid_item_no("1234")

    def test_five_digit(self):
        assert is_valid_item_no("12345")

    def test_alphanumeric(self):
        assert is_valid_item_no("BJ100120")

    def test_hyphenated(self):
        assert is_valid_item_no("TTRS-42")

    def test_empty(self):
        assert not is_valid_item_no("")

    def test_too_short(self):
        assert not is_valid_item_no("12")

    def test_alpha_only(self):
        assert not is_valid_item_no("ABC")

    def test_whitespace(self):
        assert is_valid_item_no("  12345  ")


class TestCleanProductName:
    def test_strips_whitespace(self):
        assert clean_product_name("  Hello  ") == "Hello"

    def test_collapses_spaces(self):
        assert clean_product_name("Hello   World") == "Hello World"

    def test_collapses_newlines(self):
        assert clean_product_name("Hello\n\nWorld") == "Hello World"

    def test_empty(self):
        assert clean_product_name("") == ""

    def test_none(self):
        assert clean_product_name(None) == ""


class TestCombineIdentifiers:
    def test_upc_and_sku(self):
        assert combine_identifiers("UPC123", "SKU456", "") == "UPC123 / SKU456"

    def test_all_three(self):
        assert combine_identifiers("UPC", "SKU", "ITEM") == "UPC / SKU / ITEM"

    def test_dedup(self):
        assert combine_identifiers("ABC", "ABC", "") == "ABC"

    def test_empty_only(self):
        assert combine_identifiers("", "", "") == ""

    def test_single(self):
        assert combine_identifiers("UPC123", "", "") == "UPC123"

    def test_strips_whitespace(self):
        assert combine_identifiers(" UPC ", " SKU ", "") == "UPC / SKU"


class TestParseMarkdownTables:
    def test_basic_table(self):
        text = """
| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |
"""
        tables = parse_markdown_tables(text)
        assert len(tables) == 1
        assert tables[0][0] == ["Header 1", "Header 2"]
        assert tables[0][1] == ["Cell 1", "Cell 2"]

    def test_no_table(self):
        assert parse_markdown_tables("Just plain text") == []

    def test_single_row_ignored(self):
        text = "| Just one row |"
        assert parse_markdown_tables(text) == []

    def test_multiple_tables(self):
        text = """
| A | B |
|---|---|
| 1 | 2 |

Some text

| C | D |
|---|---|
| 3 | 4 |
"""
        tables = parse_markdown_tables(text)
        assert len(tables) == 2

    def test_separator_only_skipped(self):
        text = """
| H1 | H2 |
|---|---|
| D1 | D2 |
"""
        tables = parse_markdown_tables(text)
        assert len(tables) == 1
        # Separator row should not be in the data
        for row in tables[0]:
            assert "---" not in str(row)
