"""Tests for web_verifier.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from extractor.data_model import Product, ExtractionSession, FieldLocation


@pytest.fixture
def web_client(sample_session, tmp_session_dir):
    """Create Flask test client with mocked state."""
    from extractor.web_verifier import (
        init_app, _state, _state_lock, _generate_csrf_token,
        _extraction_jobs, _extraction_lock,
    )

    # init_app() first (generates CSRF token, registers cleanup)
    app = init_app()
    app.config['TESTING'] = True

    # Then set state AFTER init_app (which resets to dashboard mode)
    with _state_lock:
        _state['pdf_path'] = None
        _state['session'] = sample_session
        _state['session_dir'] = tmp_session_dir
        _state['pdf_doc'] = MagicMock()
        _state['dashboard_mode'] = False
        _state['product_index'] = None

    # Clear extraction jobs
    with _extraction_lock:
        _extraction_jobs.clear()

    # Get the CSRF token after init
    from extractor.web_verifier import _csrf_token as token
    with app.test_client() as client:
        yield client, token


def _headers(csrf_token):
    return {'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json'}


class TestIndexRoute:
    def test_returns_200(self, web_client):
        client, token = web_client
        resp = client.get('/')
        assert resp.status_code == 200

    def test_dashboard_mode(self, tmp_session_dir):
        from extractor.web_verifier import init_app, _state, _state_lock
        app = init_app()
        app.config['TESTING'] = True
        with _state_lock:
            _state['session'] = None
            _state['dashboard_mode'] = True
            _state['pdf_doc'] = None
        with app.test_client() as client:
            resp = client.get('/')
            assert resp.status_code == 200


class TestGetPage:
    def test_valid_page(self, web_client):
        client, token = web_client
        resp = client.get('/api/page/1')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['page_number'] == 1

    def test_invalid_page(self, web_client):
        client, token = web_client
        resp = client.get('/api/page/999')
        assert resp.status_code == 400


class TestGetStats:
    def test_returns_stats(self, web_client):
        client, token = web_client
        resp = client.get('/api/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total_products'] == 1
        assert data['total_pages'] == 5


class TestProductCRUD:
    def test_add_product(self, web_client):
        client, token = web_client
        resp = client.post('/api/product', headers=_headers(token),
                          data=json.dumps({'product_name': 'New Widget', 'item_no': '99999', 'page_number': 1}))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success']
        assert data['product']['product_name'] == 'New Widget'

    def test_add_product_no_csrf(self, web_client):
        client, token = web_client
        resp = client.post('/api/product', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'product_name': 'X'}))
        assert resp.status_code == 403

    def test_update_product(self, web_client, sample_product):
        client, token = web_client
        pid = sample_product.id
        resp = client.put(f'/api/product/{pid}', headers=_headers(token),
                         data=json.dumps({'product_name': 'Updated Widget'}))
        assert resp.status_code == 200
        assert resp.get_json()['success']

    def test_update_nonexistent(self, web_client):
        client, token = web_client
        resp = client.put('/api/product/nonexistent', headers=_headers(token),
                         data=json.dumps({'product_name': 'X'}))
        assert resp.status_code == 404

    def test_delete_product(self, web_client, sample_product):
        client, token = web_client
        pid = sample_product.id
        resp = client.delete(f'/api/product/{pid}', headers=_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()['success']

    def test_delete_nonexistent(self, web_client):
        client, token = web_client
        resp = client.delete('/api/product/nonexistent', headers=_headers(token))
        assert resp.status_code == 404


class TestCsrfProtection:
    def test_save_without_csrf(self, web_client):
        client, token = web_client
        # Send with Content-Type so Flask parses JSON body (empty),
        # but no CSRF token header
        resp = client.post('/api/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({}))
        assert resp.status_code == 403

    def test_save_with_csrf(self, web_client):
        client, token = web_client
        resp = client.post('/api/save', headers=_headers(token))
        assert resp.status_code == 200

    def test_invalid_csrf(self, web_client):
        client, token = web_client
        resp = client.post('/api/save', headers={'X-CSRF-Token': 'wrong', 'Content-Type': 'application/json'})
        assert resp.status_code == 403


class TestExportCsv:
    def test_export(self, web_client):
        client, token = web_client
        resp = client.post('/api/export-csv', headers=_headers(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success']
        assert data['products_count'] == 1


class TestValidateCatalogName:
    def test_valid_name(self):
        from extractor.web_verifier import _validate_catalog_name
        assert _validate_catalog_name("my-catalog") == "my-catalog"

    def test_path_traversal(self):
        from extractor.web_verifier import _validate_catalog_name
        result = _validate_catalog_name("../etc/passwd")
        assert result is None or ".." not in result

    def test_empty(self):
        from extractor.web_verifier import _validate_catalog_name
        assert _validate_catalog_name("") is None

    def test_dot_prefix(self):
        from extractor.web_verifier import _validate_catalog_name
        result = _validate_catalog_name(".hidden")
        assert result is None or not result.startswith('.')


class TestSanitizeProductField:
    def test_truncates(self):
        from extractor.web_verifier import _sanitize_product_field
        result = _sanitize_product_field("x" * 200, max_length=100)
        assert len(result) == 100

    def test_strips(self):
        from extractor.web_verifier import _sanitize_product_field
        assert _sanitize_product_field("  hello  ") == "hello"

    def test_none(self):
        from extractor.web_verifier import _sanitize_product_field
        assert _sanitize_product_field(None) == ""


class TestProductIndex:
    def test_build_and_find(self, web_client, sample_product):
        from extractor.web_verifier import _state, _state_lock, _find_product_by_id
        with _state_lock:
            product, idx = _find_product_by_id(_state['session'], sample_product.id)
        assert product is not None
        assert product.id == sample_product.id
        assert idx == 0

    def test_find_missing(self, web_client):
        from extractor.web_verifier import _state, _state_lock, _find_product_by_id
        with _state_lock:
            product, idx = _find_product_by_id(_state['session'], "nonexistent")
        assert product is None
        assert idx == -1
