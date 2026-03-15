"""Shared test fixtures."""

import pytest
from extractor.data_model import Product, ExtractionSession, FieldLocation


@pytest.fixture
def sample_product():
    return Product(
        product_name="Test Widget",
        description="A test product",
        item_no="12345",
        pkg="10",
        uom="ct",
        page_number=1,
        source_file="test.pdf",
    )


@pytest.fixture
def sample_session(sample_product):
    session = ExtractionSession(
        source_file="test.pdf",
        total_pages=5,
        current_page=1,
    )
    session.add_product(sample_product)
    return session


@pytest.fixture
def tmp_session_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def tmp_extractions_dir(tmp_path):
    d = tmp_path / "extractions"
    d.mkdir()
    return d
