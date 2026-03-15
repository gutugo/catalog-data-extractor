"""Tests for patterns.py."""

from extractor.patterns import (
    ITEM_NO_PATTERN,
    COUNT_UOM_PATTERN,
    OTC_ITEM_CODE_PATTERN,
    OTC_SKU_PATTERN,
    OTC_PRICE_PATTERN,
    CODE_PRICE_PATTERN,
    DUAL_ID_PATTERN,
    PRODUCT_LINE_PATTERN,
    MULTILINE_ITEM_PATTERN,
    ITEM_PREFIX_PATTERN,
    SLASH_UOM_PATTERN,
    PRICE_PATTERN,
    HEADER_PATTERNS,
    COUNT_COLUMN_PATTERN,
)


class TestItemNoPattern:
    def test_four_digit(self):
        assert ITEM_NO_PATTERN.match("1234")

    def test_five_digit(self):
        assert ITEM_NO_PATTERN.match("12345")

    def test_alphanumeric_prefix(self):
        assert ITEM_NO_PATTERN.match("PMS989803150181")
        assert ITEM_NO_PATTERN.match("BJ100120")

    def test_hyphenated(self):
        assert ITEM_NO_PATTERN.match("TTRS-42")
        assert ITEM_NO_PATTERN.match("VR-1234")
        assert ITEM_NO_PATTERN.match("CS-2")

    def test_letter_digits(self):
        assert ITEM_NO_PATTERN.match("TSTAG1")

    def test_rejects_too_short(self):
        assert not ITEM_NO_PATTERN.match("123")

    def test_rejects_plain_words(self):
        assert not ITEM_NO_PATTERN.match("ABC")
        assert not ITEM_NO_PATTERN.match("Widget")

    def test_rejects_single_letter(self):
        assert not ITEM_NO_PATTERN.match("A")


class TestCountUomPattern:
    def test_basic(self):
        m = COUNT_UOM_PATTERN.match("32 ct.")
        assert m
        assert m.group(1) == "32"

    def test_pack(self):
        assert COUNT_UOM_PATTERN.match("100 pk")

    def test_comma_count(self):
        m = COUNT_UOM_PATTERN.match("1,000 ct.")
        assert m
        assert m.group(1) == "1,000"

    def test_no_space(self):
        assert COUNT_UOM_PATTERN.match("16oz")

    def test_rejects_plain_number(self):
        assert not COUNT_UOM_PATTERN.match("42")

    def test_rejects_text(self):
        assert not COUNT_UOM_PATTERN.match("hello")


class TestOtcPatterns:
    def test_item_code_valid(self):
        assert OTC_ITEM_CODE_PATTERN.match("A1")
        assert OTC_ITEM_CODE_PATTERN.match("B12")
        assert OTC_ITEM_CODE_PATTERN.match("C52")
        assert OTC_ITEM_CODE_PATTERN.match("E146")

    def test_item_code_invalid(self):
        assert not OTC_ITEM_CODE_PATTERN.match("AB1")
        assert not OTC_ITEM_CODE_PATTERN.match("A1234")
        assert not OTC_ITEM_CODE_PATTERN.match("1A")

    def test_sku_valid(self):
        assert OTC_SKU_PATTERN.match("44676")
        assert OTC_SKU_PATTERN.match("446761")
        assert OTC_SKU_PATTERN.match("557890")

    def test_sku_invalid(self):
        assert not OTC_SKU_PATTERN.match("1234")
        assert not OTC_SKU_PATTERN.match("1234567")

    def test_price_valid(self):
        assert OTC_PRICE_PATTERN.match("$16")
        assert OTC_PRICE_PATTERN.match("$8")

    def test_price_invalid(self):
        assert not OTC_PRICE_PATTERN.match("$16.99")
        assert not OTC_PRICE_PATTERN.match("16")


class TestCodePricePattern:
    def test_basic(self):
        m = CODE_PRICE_PATTERN.match("PMS989803150181 $42.26 /EACH")
        assert m
        assert m.group(1) == "PMS989803150181"
        assert m.group(3) == "EACH"

    def test_no_slash(self):
        m = CODE_PRICE_PATTERN.match("BJ100120 $15.00 PAIR")
        assert m


class TestDualIdPattern:
    def test_basic(self):
        m = DUAL_ID_PATTERN.match("A1 446761 ACNE CONTROL CLEANSER 8 OZ $16")
        assert m
        assert m.group(1) == "A1"
        assert m.group(2) == "446761"


class TestProductLinePattern:
    def test_basic(self):
        m = PRODUCT_LINE_PATTERN.match("12345 Some Product 32 ct. $9.99")
        assert m
        assert m.group(1) == "12345"


class TestSlashUomPattern:
    def test_basic(self):
        m = SLASH_UOM_PATTERN.match("2500/RL")
        assert m

    def test_with_comma(self):
        m = SLASH_UOM_PATTERN.match("2,500/RL")
        assert m

    def test_each(self):
        m = SLASH_UOM_PATTERN.match("100/EACH")
        assert m


class TestPricePattern:
    def test_basic(self):
        assert PRICE_PATTERN.match("$9.99")
        assert PRICE_PATTERN.match("$100")

    def test_comma(self):
        assert PRICE_PATTERN.match("$1,000.00")

    def test_rejects_no_dollar(self):
        assert not PRICE_PATTERN.match("9.99")


class TestCountColumnPattern:
    def test_with_unit(self):
        assert COUNT_COLUMN_PATTERN.match("32 ct.")

    def test_plain_number(self):
        assert COUNT_COLUMN_PATTERN.match("100")

    def test_slash_unit(self):
        assert COUNT_COLUMN_PATTERN.match("2500/RL")


class TestItemPrefixPattern:
    def test_item_hash(self):
        m = ITEM_PREFIX_PATTERN.search("Item # TTRS-42")
        assert m
        assert m.group(1) == "TTRS-42"

    def test_item_no_space(self):
        m = ITEM_PREFIX_PATTERN.search("Item#BJ100120")
        assert m


class TestHeaderPatterns:
    def test_matches_item(self):
        assert any(p.match("Item #") for p in HEADER_PATTERNS)

    def test_matches_description(self):
        assert any(p.match("Description") for p in HEADER_PATTERNS)

    def test_matches_price(self):
        assert any(p.match("Price") for p in HEADER_PATTERNS)

    def test_no_match_regular_text(self):
        assert not any(p.match("Widget") for p in HEADER_PATTERNS)
