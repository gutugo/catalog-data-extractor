"""Tests for data_model.py."""

import json
import pytest
from pathlib import Path
from extractor.data_model import (
    Product, ExtractionSession, FieldLocation, PageContent, _generate_id,
)


class TestFieldLocation:
    def test_to_dict(self):
        loc = FieldLocation(x0=10.0, y0=20.0, x1=100.0, y1=30.0, page_number=1, confidence=0.95)
        d = loc.to_dict()
        assert d == {'x0': 10.0, 'y0': 20.0, 'x1': 100.0, 'y1': 30.0, 'page_number': 1, 'confidence': 0.95}

    def test_from_dict(self):
        data = {'x0': 5.0, 'y0': 10.0, 'x1': 50.0, 'y1': 20.0, 'page_number': 3, 'confidence': 0.8}
        loc = FieldLocation.from_dict(data)
        assert loc.x0 == 5.0
        assert loc.page_number == 3
        assert loc.confidence == 0.8

    def test_from_dict_defaults(self):
        loc = FieldLocation.from_dict({})
        assert loc.x0 == 0
        assert loc.confidence == 1.0

    def test_roundtrip(self):
        original = FieldLocation(x0=1.5, y0=2.5, x1=3.5, y1=4.5, page_number=2, confidence=0.75)
        restored = FieldLocation.from_dict(original.to_dict())
        assert restored.x0 == original.x0
        assert restored.confidence == original.confidence


class TestProduct:
    def test_creation_defaults(self):
        p = Product(product_name="Widget")
        assert p.product_name == "Widget"
        assert p.description == ""
        assert p.item_no == ""
        assert p.pkg == ""
        assert p.uom == ""
        assert p.page_number == 0
        assert p.id  # should have auto-generated ID

    def test_id_generation(self):
        p1 = Product(product_name="A")
        p2 = Product(product_name="B")
        assert p1.id != p2.id
        assert len(p1.id) == 16

    def test_to_dict(self, sample_product):
        d = sample_product.to_dict()
        assert d['product_name'] == "Test Widget"
        assert d['item_no'] == "12345"
        assert d['page_number'] == 1
        assert 'id' in d

    def test_from_dict(self):
        data = {
            'product_name': 'Gadget',
            'item_no': '99999',
            'page_number': 5,
            'id': 'test-id-1234',
        }
        p = Product.from_dict(data)
        assert p.product_name == 'Gadget'
        assert p.item_no == '99999'
        assert p.id == 'test-id-1234'

    def test_from_dict_missing_id(self):
        p = Product.from_dict({'product_name': 'X'})
        assert p.id  # auto-generated
        assert len(p.id) == 16

    def test_from_dict_empty_id(self):
        p = Product.from_dict({'product_name': 'X', 'id': ''})
        assert p.id  # auto-generated, not empty
        assert len(p.id) == 16

    def test_roundtrip(self, sample_product):
        restored = Product.from_dict(sample_product.to_dict())
        assert restored.product_name == sample_product.product_name
        assert restored.item_no == sample_product.item_no
        assert restored.id == sample_product.id

    def test_confidence_no_locations(self):
        p = Product(product_name="X")
        assert p.get_confidence_score() == 100.0

    def test_confidence_with_locations(self):
        p = Product(product_name="X", field_locations={
            'item_no': FieldLocation(0, 0, 1, 1, 1, confidence=0.8),
            'name': FieldLocation(0, 0, 1, 1, 1, confidence=0.6),
        })
        assert p.get_confidence_score() == 70.0  # (0.8+0.6)/2 * 100

    def test_to_dict_with_field_locations(self):
        p = Product(product_name="X", field_locations={
            'item_no': FieldLocation(10, 20, 30, 40, 1, 0.9),
        })
        d = p.to_dict()
        assert 'field_locations' in d
        assert d['field_locations']['item_no']['confidence'] == 0.9

    def test_from_dict_with_field_locations(self):
        data = {
            'product_name': 'X',
            'field_locations': {
                'item_no': {'x0': 10, 'y0': 20, 'x1': 30, 'y1': 40, 'page_number': 1, 'confidence': 0.9}
            }
        }
        p = Product.from_dict(data)
        assert 'item_no' in p.field_locations
        assert p.field_locations['item_no'].confidence == 0.9


class TestExtractionSession:
    def test_creation(self):
        s = ExtractionSession(source_file="test.pdf", total_pages=10)
        assert s.source_file == "test.pdf"
        assert s.total_pages == 10
        assert s.products == []
        assert s.completed is False

    def test_add_product(self, sample_product):
        s = ExtractionSession(source_file="test.pdf", total_pages=5)
        s.add_product(sample_product)
        assert len(s.products) == 1

    def test_to_dict(self, sample_session):
        d = sample_session.to_dict()
        assert d['source_file'] == "test.pdf"
        assert d['total_pages'] == 5
        assert len(d['products']) == 1

    def test_from_dict(self):
        data = {
            'source_file': 'catalog.pdf',
            'total_pages': 20,
            'products': [{'product_name': 'Widget', 'id': 'abc123'}],
        }
        s = ExtractionSession.from_dict(data)
        assert s.source_file == 'catalog.pdf'
        assert len(s.products) == 1

    def test_from_dict_missing_source_file(self):
        with pytest.raises(KeyError, match="source_file"):
            ExtractionSession.from_dict({'total_pages': 5})

    def test_from_dict_missing_total_pages(self):
        with pytest.raises(KeyError, match="total_pages"):
            ExtractionSession.from_dict({'source_file': 'x.pdf'})

    def test_save_and_load(self, sample_session, tmp_session_dir):
        path = sample_session.save(tmp_session_dir)
        assert path.exists()
        loaded = ExtractionSession.load(path)
        assert loaded is not None
        assert loaded.source_file == sample_session.source_file
        assert len(loaded.products) == len(sample_session.products)

    def test_load_nonexistent(self, tmp_path):
        result = ExtractionSession.load(tmp_path / "missing.json")
        assert result is None

    def test_load_corrupted(self, tmp_path):
        bad_file = tmp_path / "bad.session.json"
        bad_file.write_text("not json")
        result = ExtractionSession.load(bad_file)
        assert result is None

    def test_roundtrip(self, sample_session, tmp_session_dir):
        sample_session.save(tmp_session_dir)
        path = tmp_session_dir / "test.session.json"
        loaded = ExtractionSession.load(path)
        assert loaded.total_pages == sample_session.total_pages
        assert loaded.products[0].product_name == sample_session.products[0].product_name


class TestPageContent:
    def test_get_numbered_lines(self):
        pc = PageContent(page_number=1, lines=["Line A", "Line B", "Line C"])
        numbered = pc.get_numbered_lines()
        assert numbered == [(1, "Line A"), (2, "Line B"), (3, "Line C")]

    def test_empty_lines(self):
        pc = PageContent(page_number=1)
        assert pc.get_numbered_lines() == []


class TestGenerateId:
    def test_length(self):
        assert len(_generate_id()) == 16

    def test_unique(self):
        ids = {_generate_id() for _ in range(100)}
        assert len(ids) == 100
