"""Tests for product_validation.py."""

from extractor.product_validation import (
    is_false_positive_item_no,
    validate_product,
    filter_valid_products,
)
from extractor.data_model import Product


class TestIsFalsePositiveItemNo:
    def test_measurement_kg(self):
        assert is_false_positive_item_no("75kg")

    def test_measurement_cm(self):
        assert is_false_positive_item_no("200cm")

    def test_measurement_mm(self):
        assert is_false_positive_item_no("10mm")

    def test_dimension(self):
        assert is_false_positive_item_no("200x85cm")

    def test_voltage(self):
        assert is_false_positive_item_no("12V")
        assert is_false_positive_item_no("220V")

    def test_frequency(self):
        assert is_false_positive_item_no("50Hz")

    def test_time(self):
        assert is_false_positive_item_no("10Minutes")

    def test_ip_rating(self):
        assert is_false_positive_item_no("IPX4")

    def test_standard(self):
        assert is_false_positive_item_no("BS 7177")

    def test_pure_alpha(self):
        assert is_false_positive_item_no("Nylon")
        assert is_false_positive_item_no("Black")

    def test_multi_word(self):
        assert is_false_positive_item_no("Analog Pump System")

    def test_percentage(self):
        assert is_false_positive_item_no("50%")

    def test_temperature(self):
        assert is_false_positive_item_no("37°C")

    def test_range(self):
        assert is_false_positive_item_no("10-20")

    def test_valid_item_no_passes(self):
        assert not is_false_positive_item_no("12345")
        assert not is_false_positive_item_no("BJ100120")
        assert not is_false_positive_item_no("TTRS-42")

    def test_empty(self):
        assert not is_false_positive_item_no("")

    def test_combined_identifier(self):
        # Combined identifiers with " / " should pass
        assert not is_false_positive_item_no("A1 / 446761")

    def test_newline(self):
        assert is_false_positive_item_no("some\ntext")

    def test_spec_label(self):
        assert is_false_positive_item_no("Weight:")

    def test_yes_no(self):
        assert is_false_positive_item_no("Yes")
        assert is_false_positive_item_no("N/A")

    def test_class_rating(self):
        assert is_false_positive_item_no("Class1")

    def test_lowercase_words(self):
        assert is_false_positive_item_no("High density")


class TestValidateProduct:
    def test_valid_product(self):
        p = Product(product_name="Widget Pro", item_no="12345")
        assert validate_product(p)

    def test_false_positive_item_no(self):
        p = Product(product_name="Some Item", item_no="75kg")
        assert not validate_product(p)

    def test_spec_label_name(self):
        p = Product(product_name="Weight:", item_no="12345")
        assert not validate_product(p)

    def test_short_name(self):
        p = Product(product_name="AB", item_no="12345")
        assert not validate_product(p)

    def test_no_item_no(self):
        # Empty item_no returns False from is_false_positive_item_no (early return),
        # so the product passes validation
        p = Product(product_name="Valid Name", item_no="")
        assert validate_product(p)

    def test_combined_id(self):
        p = Product(product_name="ACNE CLEANSER", item_no="A1 / 446761")
        assert validate_product(p)


class TestFilterValidProducts:
    def test_filters_invalid(self):
        products = [
            Product(product_name="Valid Widget", item_no="12345"),
            Product(product_name="Bad", item_no="75kg"),
            Product(product_name="Also Valid", item_no="BJ100120"),
        ]
        result = filter_valid_products(products)
        assert len(result) == 2
        assert result[0].item_no == "12345"
        assert result[1].item_no == "BJ100120"

    def test_empty_list(self):
        assert filter_valid_products([]) == []

    def test_all_invalid(self):
        products = [
            Product(product_name="AB", item_no="75kg"),
        ]
        assert filter_valid_products(products) == []
