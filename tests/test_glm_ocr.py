"""Tests for glm_ocr.py and GLM-OCR integration."""

import base64
import json
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

from extractor.glm_ocr import GlmOcrClient, GlmOcrError
from extractor.auto_extractor import AutoExtractor
from extractor.pdf_reader import PDFReader
from extractor.data_model import Product, ExtractionSession, PageContent, FieldLocation


# ---------------------------------------------------------------------------
# GlmOcrClient unit tests
# ---------------------------------------------------------------------------

class TestGlmOcrClientInit:
    def test_default_host(self):
        with patch.dict("os.environ", {}, clear=True):
            client = GlmOcrClient()
        assert client.host == "http://localhost:11434"

    def test_host_from_env(self):
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://mini01:11434"}):
            client = GlmOcrClient()
        assert client.host == "http://mini01:11434"

    def test_host_from_arg(self):
        client = GlmOcrClient(host="http://custom:9999")
        assert client.host == "http://custom:9999"

    def test_arg_overrides_env(self):
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://env:1111"}):
            client = GlmOcrClient(host="http://arg:2222")
        assert client.host == "http://arg:2222"

    def test_strips_trailing_slash(self):
        client = GlmOcrClient(host="http://host:1234/")
        assert client.host == "http://host:1234"

    def test_default_model(self):
        client = GlmOcrClient()
        assert client.model == "glm-ocr"

    def test_custom_model(self):
        client = GlmOcrClient(model="glm-ocr:latest")
        assert client.model == "glm-ocr:latest"


class TestGlmOcrClientCheckAvailability:
    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_available(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "models": [{"name": "glm-ocr:latest"}, {"name": "llama3:8b"}]
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient()
        assert client.check_availability() is True

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_not_available(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "models": [{"name": "llama3:8b"}]
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient()
        assert client.check_availability() is False

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")
        client = GlmOcrClient()
        assert client.check_availability() is False


class TestGlmOcrClientRecognize:
    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_recognize_table_success(self, mock_urlopen):
        markdown = "| Item | Name |\n|---|---|\n| 12345 | Widget |"
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "message": {"content": markdown}
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient()
        result = client.recognize_table(b"fake-png-bytes")
        assert result == markdown

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_recognize_text_success(self, mock_urlopen):
        text = "Some recognized text"
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "message": {"content": text}
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient()
        result = client.recognize_text(b"fake-png-bytes")
        assert result == text

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_sends_correct_payload(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"message": {"content": "ok"}}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient(model="glm-ocr")
        client.recognize_table(b"\x89PNG")

        # Check the request
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data)

        assert body["model"] == "glm-ocr"
        assert body["stream"] is False
        assert len(body["messages"]) == 1
        assert body["messages"][0]["content"] == "Table Recognition:"
        assert body["messages"][0]["images"][0] == base64.b64encode(b"\x89PNG").decode("ascii")

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = GlmOcrClient()
        with pytest.raises(GlmOcrError, match="Connection failed"):
            client.recognize_table(b"png")

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_malformed_response(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b"not json"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient()
        with pytest.raises(GlmOcrError, match="Malformed response"):
            client.recognize_table(b"png")

    @patch("extractor.glm_ocr.urllib.request.urlopen")
    def test_unexpected_structure(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"wrong": "structure"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        client = GlmOcrClient()
        with pytest.raises(GlmOcrError, match="Unexpected response"):
            client.recognize_table(b"png")


# ---------------------------------------------------------------------------
# AutoExtractor GLM-OCR integration tests
# ---------------------------------------------------------------------------

class TestAutoExtractorGlmOcr:
    def test_init_creates_client(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        assert isinstance(ext._glm_client, GlmOcrClient)

    @patch.object(GlmOcrClient, "recognize_table")
    def test_extract_page_with_table(self, mock_recognize, tmp_path):
        mock_recognize.return_value = (
            "| Item # | Description | Count |\n"
            "|---|---|---|\n"
            "| 12345 | Widget Pro | 32 ct. |"
        )

        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        mock_reader = MagicMock()
        mock_reader.render_page_to_png.return_value = b"fake-png"
        products = ext._extract_page(mock_reader, 1)

        assert len(products) >= 1
        assert any("12345" in p.item_no for p in products)

    @patch.object(GlmOcrClient, "recognize_table")
    def test_extract_page_glm_error(self, mock_recognize, tmp_path):
        mock_recognize.side_effect = GlmOcrError("timeout")

        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        mock_reader = MagicMock()
        mock_reader.render_page_to_png.return_value = b"fake-png"
        products = ext._extract_page(mock_reader, 1)

        assert products == []
        assert 1 in ext.empty_pages

    def test_extract_page_empty_render(self, tmp_path):
        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        mock_reader = MagicMock()
        mock_reader.render_page_to_png.return_value = b""
        products = ext._extract_page(mock_reader, 1)

        assert products == []
        assert 1 in ext.empty_pages

    @patch.object(GlmOcrClient, "recognize_table")
    def test_extract_page_text_fallback(self, mock_recognize, tmp_path):
        # Return text that doesn't parse as a markdown table but matches regex
        mock_recognize.return_value = "12345 Widget Pro 32 ct. $9.99"

        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        mock_reader = MagicMock()
        mock_reader.render_page_to_png.return_value = b"fake-png"
        products = ext._extract_page(mock_reader, 1)

        assert len(products) >= 1

    @patch.object(GlmOcrClient, "recognize_table")
    def test_extract_page_empty_response(self, mock_recognize, tmp_path):
        mock_recognize.return_value = ""

        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        mock_reader = MagicMock()
        mock_reader.render_page_to_png.return_value = b"fake-png"
        products = ext._extract_page(mock_reader, 1)

        assert products == []
        assert 1 in ext.empty_pages

    @patch.object(GlmOcrClient, "recognize_table")
    def test_pipeline_stats_tracked(self, mock_recognize, tmp_path):
        mock_recognize.return_value = (
            "| Item # | Description |\n"
            "|---|---|\n"
            "| 12345 | Widget |"
        )

        ext = AutoExtractor(Path("test.pdf"), tmp_path)
        mock_reader = MagicMock()
        mock_reader.render_page_to_png.return_value = b"fake-png"
        ext._extract_page(mock_reader, 1)

        assert ext.pipeline_stats.get('glm_ocr', 0) >= 1


# ---------------------------------------------------------------------------
# render_page_to_png tests
# ---------------------------------------------------------------------------

class TestRenderPageToPng:
    def test_renders_png_bytes(self):
        """Test with a real in-memory PDF created via pymupdf."""
        import pymupdf

        # Create a tiny 1-page PDF in memory
        doc = pymupdf.open()
        page = doc.new_page(width=100, height=100)
        page.insert_text((10, 50), "Hello")
        pdf_bytes = doc.tobytes()
        doc.close()

        # Write to temp file and test
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            f.flush()
            pdf_path = Path(f.name)

        try:
            with PDFReader(pdf_path) as reader:
                png = reader.render_page_to_png(1)
                assert len(png) > 0
                # Check PNG magic bytes
                assert png[:4] == b"\x89PNG"
        finally:
            pdf_path.unlink()

    def test_invalid_page_raises(self):
        import pymupdf
        import tempfile

        doc = pymupdf.open()
        doc.new_page(width=100, height=100)
        pdf_bytes = doc.tobytes()
        doc.close()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            f.flush()
            pdf_path = Path(f.name)

        try:
            with PDFReader(pdf_path) as reader:
                with pytest.raises(ValueError):
                    reader.render_page_to_png(99)
        finally:
            pdf_path.unlink()
