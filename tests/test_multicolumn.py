"""Tests for multicolumn.py."""

from extractor.multicolumn import (
    detect_column_gaps,
    split_words_into_columns,
    reconstruct_lines_from_words,
    detect_multicolumn_layout,
    parse_multicolumn_products,
)


def _make_word(text, x0, x1, top, bottom=None):
    """Helper to create word dicts."""
    if bottom is None:
        bottom = top + 12
    return {'text': text, 'x0': x0, 'x1': x1, 'top': top, 'bottom': bottom}


class TestDetectColumnGaps:
    def test_two_column_layout(self):
        words = [
            _make_word("Hello", 50, 100, 100),
            _make_word("World", 50, 100, 120),
            _make_word("Foo", 350, 400, 100),
            _make_word("Bar", 350, 400, 120),
        ]
        gaps = detect_column_gaps(words, 600)
        assert len(gaps) >= 1
        # Gap should be between 100 and 350 (middle of page)
        assert any(100 < g < 350 for g in gaps)

    def test_single_column(self):
        words = [
            _make_word("Hello", 50, 200, 100),
            _make_word("World", 50, 200, 120),
        ]
        gaps = detect_column_gaps(words, 600)
        # No gap in the middle since text is all on one side
        assert len(gaps) == 0 or all(g < 150 or g > 250 for g in gaps)

    def test_empty_words(self):
        assert detect_column_gaps([], 600) == []

    def test_zero_width(self):
        assert detect_column_gaps([_make_word("A", 10, 20, 10)], 0) == []

    def test_negative_width(self):
        assert detect_column_gaps([_make_word("A", 10, 20, 10)], -100) == []


class TestSplitWordsIntoColumns:
    def test_basic_split(self):
        words = [
            _make_word("Left", 50, 100, 100),
            _make_word("Right", 350, 400, 100),
        ]
        left, right = split_words_into_columns(words, 200)
        assert len(left) == 1
        assert len(right) == 1
        assert left[0]['text'] == "Left"
        assert right[0]['text'] == "Right"

    def test_all_left(self):
        words = [_make_word("A", 10, 50, 100)]
        left, right = split_words_into_columns(words, 200)
        assert len(left) == 1
        assert len(right) == 0

    def test_all_right(self):
        words = [_make_word("A", 300, 350, 100)]
        left, right = split_words_into_columns(words, 200)
        assert len(left) == 0
        assert len(right) == 1

    def test_empty(self):
        left, right = split_words_into_columns([], 200)
        assert left == []
        assert right == []


class TestReconstructLinesFromWords:
    def test_same_line(self):
        words = [
            _make_word("Hello", 10, 50, 100),
            _make_word("World", 60, 100, 101),  # within tolerance
        ]
        lines = reconstruct_lines_from_words(words)
        assert len(lines) == 1
        assert lines[0]['text'] == "Hello World"

    def test_different_lines(self):
        words = [
            _make_word("Line1", 10, 50, 100),
            _make_word("Line2", 10, 50, 120),
        ]
        lines = reconstruct_lines_from_words(words)
        assert len(lines) == 2

    def test_empty(self):
        assert reconstruct_lines_from_words([]) == []

    def test_single_word(self):
        words = [_make_word("Solo", 10, 50, 100)]
        lines = reconstruct_lines_from_words(words)
        assert len(lines) == 1
        assert lines[0]['text'] == "Solo"

    def test_left_to_right_order(self):
        words = [
            _make_word("B", 60, 80, 100),
            _make_word("A", 10, 30, 100),
        ]
        lines = reconstruct_lines_from_words(words)
        assert lines[0]['text'] == "A B"


class TestDetectMulticolumnLayout:
    def test_two_column_otc(self):
        words = [
            # Left column with OTC codes
            _make_word("A1", 50, 65, 100),
            _make_word("CLEANSER", 70, 150, 100),
            _make_word("$16", 200, 220, 100),
            # Right column with OTC codes
            _make_word("B2", 350, 365, 100),
            _make_word("PAIN", 370, 420, 100),
            _make_word("$8", 450, 465, 100),
        ]
        boundary = detect_multicolumn_layout(words, 600)
        assert boundary is not None
        assert 200 < boundary < 380

    def test_single_column_returns_none(self):
        words = [
            _make_word("A1", 50, 65, 100),
            _make_word("Text", 70, 150, 100),
        ]
        assert detect_multicolumn_layout(words, 600) is None

    def test_no_otc_codes_returns_none(self):
        words = [
            _make_word("Hello", 50, 100, 100),
            _make_word("World", 350, 400, 100),
        ]
        assert detect_multicolumn_layout(words, 600) is None

    def test_empty_words(self):
        assert detect_multicolumn_layout([], 600) is None


class TestParseMulticolumnProducts:
    def _make_lines(self, line_data):
        """Helper: list of (text, words_list) -> line dicts."""
        result = []
        for text, words in line_data:
            result.append({
                'text': text,
                'words': words,
                'x0': words[0]['x0'] if words else 0,
                'top': words[0]['top'] if words else 0,
            })
        return result

    def test_basic_product(self):
        lines = self._make_lines([
            ("A1 CLEANSER $16", [
                _make_word("A1", 50, 65, 100),
                _make_word("CLEANSER", 70, 150, 100),
                _make_word("$16", 200, 220, 100),
            ]),
        ])
        products = parse_multicolumn_products(lines, 1, "test.pdf")
        assert len(products) == 1
        assert products[0].item_no == "A1"
        assert "CLEANSER" in products[0].product_name

    def test_product_with_sku_line(self):
        lines = self._make_lines([
            ("A1 CLEANSER $16", [
                _make_word("A1", 50, 65, 100),
                _make_word("CLEANSER", 70, 150, 100),
                _make_word("$16", 200, 220, 100),
            ]),
            ("446761 8 OZ", [
                _make_word("446761", 50, 90, 115),
                _make_word("8", 170, 178, 115),
                _make_word("OZ", 180, 195, 115),
            ]),
        ])
        products = parse_multicolumn_products(lines, 1, "test.pdf")
        assert len(products) == 1
        assert "446761" in products[0].item_no
        assert products[0].pkg == "8"
        assert products[0].uom == "oz"

    def test_skips_non_product_lines(self):
        lines = self._make_lines([
            ("SECTION HEADER", [
                _make_word("SECTION", 50, 100, 100),
                _make_word("HEADER", 110, 160, 100),
            ]),
        ])
        products = parse_multicolumn_products(lines, 1, "test.pdf")
        assert len(products) == 0

    def test_multiple_products(self):
        lines = self._make_lines([
            ("A1 CLEANSER $16", [
                _make_word("A1", 50, 65, 100),
                _make_word("CLEANSER", 70, 150, 100),
                _make_word("$16", 200, 220, 100),
            ]),
            ("B2 SOAP $8", [
                _make_word("B2", 50, 65, 130),
                _make_word("SOAP", 70, 110, 130),
                _make_word("$8", 200, 215, 130),
            ]),
        ])
        products = parse_multicolumn_products(lines, 1, "test.pdf")
        assert len(products) == 2

    def test_empty_lines(self):
        assert parse_multicolumn_products([], 1, "test.pdf") == []
