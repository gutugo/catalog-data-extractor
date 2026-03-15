"""Tests for cli.py."""

from pathlib import Path
from unittest.mock import patch, MagicMock

from extractor.cli import _validate_source_file_path, _is_port_available, _open_browser_delayed


class TestValidateSourceFilePath:
    def test_valid_pdf(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.touch()
        result = _validate_source_file_path("test.pdf", tmp_path)
        assert result is not None
        assert result.name == "test.pdf"

    def test_path_traversal(self, tmp_path):
        result = _validate_source_file_path("../../etc/passwd", tmp_path)
        assert result is None

    def test_non_pdf(self, tmp_path):
        result = _validate_source_file_path("test.txt", tmp_path)
        assert result is None

    def test_empty(self, tmp_path):
        result = _validate_source_file_path("", tmp_path)
        assert result is None

    def test_strips_directory(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.touch()
        result = _validate_source_file_path("subdir/test.pdf", tmp_path)
        assert result is not None
        assert result.name == "test.pdf"


class TestIsPortAvailable:
    def test_available_port(self):
        # Use a high port that's likely available
        assert _is_port_available("127.0.0.1", 59123) is True

    def test_unavailable_port(self):
        import socket
        # Bind a port, then check it's unavailable
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            assert _is_port_available("127.0.0.1", port) is False


class TestOpenBrowserDelayed:
    @patch("extractor.cli.webbrowser.open")
    def test_opens_browser(self, mock_open):
        import time
        _open_browser_delayed("127.0.0.1", 5000, delay=0.1)
        time.sleep(0.3)
        mock_open.assert_called_once_with("http://127.0.0.1:5000")


class TestCliCommands:
    def test_status_no_sessions(self, tmp_path):
        from typer.testing import CliRunner
        from extractor.cli import app
        runner = CliRunner()
        with patch("extractor.cli.SESSIONS_DIR", tmp_path / "sessions"), \
             patch("extractor.cli.EXTRACTIONS_DIR", tmp_path / "extractions"), \
             patch("extractor.cli.PROCESSED_DIR", tmp_path):
            (tmp_path / "sessions").mkdir()
            (tmp_path / "extractions").mkdir()
            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0

    def test_export_missing_session(self, tmp_path):
        from typer.testing import CliRunner
        from extractor.cli import app
        runner = CliRunner()
        with patch("extractor.cli.SESSIONS_DIR", tmp_path), \
             patch("extractor.cli.EXTRACTIONS_DIR", tmp_path), \
             patch("extractor.cli.PROCESSED_DIR", tmp_path):
            result = runner.invoke(app, ["export", "nonexistent"])
            assert result.exit_code == 1
