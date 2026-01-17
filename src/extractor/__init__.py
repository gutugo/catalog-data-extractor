"""Catalog Data Extractor - Semi-automatic product data extraction from PDF catalogs."""

from .data_model import Product, ExtractionSession, PageContent
from .pdf_reader import PDFReader
from .extractor import InteractiveExtractor
from .auto_extractor import AutoExtractor
from .verifier import Verifier
from .exporter import export_to_csv

__version__ = "0.1.0"

__all__ = [
    "Product",
    "ExtractionSession",
    "PageContent",
    "PDFReader",
    "InteractiveExtractor",
    "AutoExtractor",
    "Verifier",
    "export_to_csv",
]
