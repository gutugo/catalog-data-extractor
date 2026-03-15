"""Tests for exporter.py."""

import csv
from pathlib import Path
from io import StringIO

from extractor.exporter import export_to_csv, list_sessions, CSV_COLUMNS
from extractor.data_model import Product, ExtractionSession


class TestExportToCsv:
    def test_creates_file(self, sample_session, tmp_extractions_dir):
        path = export_to_csv(sample_session, tmp_extractions_dir)
        assert path.exists()
        assert path.suffix == ".csv"

    def test_correct_columns(self, sample_session, tmp_extractions_dir):
        path = export_to_csv(sample_session, tmp_extractions_dir)
        with open(path) as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == CSV_COLUMNS

    def test_correct_row_count(self, sample_session, tmp_extractions_dir):
        path = export_to_csv(sample_session, tmp_extractions_dir)
        with open(path) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            rows = list(reader)
        assert len(rows) == 1  # one product

    def test_custom_filename(self, sample_session, tmp_extractions_dir):
        path = export_to_csv(sample_session, tmp_extractions_dir, "custom.csv")
        assert path.name == "custom.csv"

    def test_empty_session(self, tmp_extractions_dir):
        session = ExtractionSession(source_file="empty.pdf", total_pages=1)
        path = export_to_csv(session, tmp_extractions_dir)
        with open(path) as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
        assert header == CSV_COLUMNS
        assert len(rows) == 0

    def test_multiple_products(self, tmp_extractions_dir):
        session = ExtractionSession(source_file="multi.pdf", total_pages=3)
        for i in range(5):
            session.add_product(Product(
                product_name=f"Product {i}",
                item_no=str(10000 + i),
                page_number=1,
            ))
        path = export_to_csv(session, tmp_extractions_dir)
        with open(path) as f:
            reader = csv.reader(f)
            next(reader)
            rows = list(reader)
        assert len(rows) == 5


class TestListSessions:
    def test_no_sessions(self, tmp_path):
        assert list_sessions(tmp_path) == []

    def test_finds_sessions(self, sample_session, tmp_session_dir):
        sample_session.save(tmp_session_dir)
        sessions = list_sessions(tmp_session_dir)
        assert len(sessions) == 1
        assert sessions[0].source_file == "test.pdf"

    def test_nonexistent_dir(self, tmp_path):
        assert list_sessions(tmp_path / "nonexistent") == []
