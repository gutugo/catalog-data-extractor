"""Web-based verification UI for catalog data extraction."""

from __future__ import annotations

import atexit
import io
import os
import secrets
import sys
import traceback
import threading
from pathlib import Path

import fitz  # PyMuPDF
from flask import Flask, render_template, jsonify, request, send_file
from werkzeug.utils import secure_filename

from .data_model import Product, ExtractionSession, FieldLocation
from .exporter import export_to_csv

# Maximum length for product text fields to prevent resource exhaustion
MAX_FIELD_LENGTH = 10000
MAX_PRODUCT_NAME_LENGTH = 1000

# Flask app - static_folder=None since we don't have static files
app = Flask(__name__, template_folder='templates', static_folder=None)

# Configuration
CATALOGS_DIR = Path.cwd() / "catalogs"
SESSIONS_DIR = Path.cwd() / "processed" / "sessions"
EXTRACTIONS_DIR = Path.cwd() / "processed" / "extractions"

# Global state (set when launching)
_state: dict = {
    'pdf_path': None,
    'session': None,
    'session_dir': None,
    'pdf_doc': None,
    'dashboard_mode': False,  # True when no catalog is loaded initially
    'product_index': None,  # Cached dict of product_id -> index for O(1) lookup
}

# Lock for thread-safe access to _state
_state_lock = threading.Lock()

# Background extraction jobs: {catalog_name: {'status': str, 'progress': dict, 'error': str}}
_extraction_jobs: dict = {}
_extraction_lock = threading.Lock()  # Thread lock for _extraction_jobs access

# Track if cleanup has been registered
_cleanup_registered = False

# CSRF token for protecting state-changing endpoints
_csrf_token: str = ""


def _generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    global _csrf_token
    _csrf_token = secrets.token_urlsafe(32)
    return _csrf_token


def _verify_csrf_token(token: str | None) -> bool:
    """Verify CSRF token matches."""
    if not _csrf_token:
        return False  # CSRF not initialized - reject for security
    return token == _csrf_token


def _sanitize_product_field(value: str | None, max_length: int = MAX_FIELD_LENGTH) -> str:
    """Sanitize and truncate product field values."""
    if value is None:
        return ''
    # Convert to string and strip whitespace
    value = str(value).strip()
    # Truncate if too long
    if len(value) > max_length:
        value = value[:max_length]
    return value


def cleanup_pdf():
    """Close the PDF document on exit."""
    with _state_lock:
        if _state['pdf_doc']:
            try:
                _state['pdf_doc'].close()
            except Exception as e:
                print(f"Warning: Error closing PDF during cleanup: {e}", file=sys.stderr)
            _state['pdf_doc'] = None


def _cleanup_completed_jobs():
    """Remove completed/errored jobs older than 5 minutes to prevent memory leaks."""
    import time
    current_time = time.time()
    with _extraction_lock:
        to_remove = []
        for name, job in _extraction_jobs.items():
            if job['status'] != 'extracting':
                # Remove completed/errored jobs after 5 minutes
                job_time = job.get('completed_at', current_time)
                if current_time - job_time > 300:  # 5 minutes
                    to_remove.append(name)
        for name in to_remove:
            del _extraction_jobs[name]


def list_catalogs() -> list[dict]:
    """List catalogs that exist in /catalogs directory.

    Returns list of dicts with keys: name, status, products_count, pages
    Status can be: 'uploaded', 'extracting', 'ready', 'exported'
    Only includes catalogs with PDF files in the catalogs directory.
    """
    catalogs = []

    # Ensure directories exist
    CATALOGS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up old completed/errored extraction jobs (prevent memory leak)
    _cleanup_completed_jobs()

    # Find all PDFs in catalogs directory (ONLY these will be shown)
    pdf_files = {}
    for pdf in CATALOGS_DIR.glob("*.pdf"):
        pdf_files[pdf.stem] = pdf
    for pdf in CATALOGS_DIR.glob("*.PDF"):
        pdf_files[pdf.stem] = pdf

    # Find all sessions
    sessions = {}
    for session_file in SESSIONS_DIR.glob("*.session.json"):
        # stem gives "foo.session", use removesuffix for cleaner handling
        name = session_file.stem.removesuffix('.session')
        sessions[name] = session_file

    # Find all exports
    exports = set()
    for csv_file in EXTRACTIONS_DIR.glob("*.csv"):
        exports.add(csv_file.stem)

    # Only show catalogs that have PDFs in /catalogs
    for name in sorted(pdf_files.keys()):
        catalog = {
            'name': name,
            'has_pdf': True,
            'products_count': 0,
            'pages': 0,
            'status': 'uploaded',
        }

        # Check if currently extracting (thread-safe access)
        is_extracting = False
        with _extraction_lock:
            if name in _extraction_jobs:
                is_extracting = True
                job = _extraction_jobs[name]
                catalog['status'] = job['status']
                if job.get('progress'):
                    catalog['progress'] = job['progress'].copy()
                if job.get('error'):
                    catalog['error'] = job['error']

        # Check if has session (only if not currently extracting)
        if not is_extracting:
            if name in sessions:
                session = ExtractionSession.load(sessions[name])
                if session:
                    catalog['products_count'] = len(session.products)
                    catalog['pages'] = session.total_pages
                    catalog['status'] = 'exported' if name in exports else 'ready'

        catalogs.append(catalog)

    return catalogs


def init_app(pdf_path: Path = None, session: ExtractionSession = None,
             session_dir: Path = None, dashboard_mode: bool = False) -> Flask:
    """Initialize the Flask app with PDF and session data.

    Can be called with no arguments for dashboard mode, or with
    pdf_path, session, and session_dir for catalog-specific mode.
    """
    _state['dashboard_mode'] = dashboard_mode
    _state['session_dir'] = session_dir or SESSIONS_DIR

    if pdf_path and session:
        _state['pdf_path'] = pdf_path
        _state['session'] = session
        _state['pdf_doc'] = fitz.open(pdf_path)
        _state['dashboard_mode'] = False
    else:
        _state['pdf_path'] = None
        _state['session'] = None
        _state['pdf_doc'] = None
        _state['dashboard_mode'] = True

    # Generate CSRF token for this session
    _generate_csrf_token()

    # Register cleanup on exit (only once)
    global _cleanup_registered
    if not _cleanup_registered:
        atexit.register(cleanup_pdf)
        _cleanup_registered = True

    return app


def _validate_catalog_name(catalog_name: str) -> str | None:
    """Validate and sanitize catalog name to prevent path traversal.

    Returns sanitized name or None if invalid.
    """
    if not catalog_name:
        return None

    # Use secure_filename for robust sanitization
    sanitized = secure_filename(catalog_name)
    if not sanitized:
        return None

    # Remove any extension that might have been added
    sanitized = Path(sanitized).stem

    # Verify it doesn't try to escape (double-check after sanitization)
    if not sanitized or sanitized.startswith('.') or '/' in sanitized or '\\' in sanitized:
        return None

    return sanitized


@app.route('/')
def index():
    """Main verification page."""
    # Check for catalog query param to auto-load
    catalog_name = request.args.get('catalog')
    if catalog_name:
        # Validate catalog_name to prevent path traversal
        catalog_name = _validate_catalog_name(catalog_name)
        if catalog_name:
            # Load session and switch catalog within lock to prevent race conditions
            with _state_lock:
                session_path = SESSIONS_DIR / f"{catalog_name}.session.json"
                if session_path.exists():
                    session = ExtractionSession.load(session_path)
                    if session:
                        # Find PDF
                        pdf_path = CATALOGS_DIR / session.source_file
                        if not pdf_path.exists():
                            pdf_path = Path.cwd() / session.source_file

                        if pdf_path.exists():
                            old_doc = _state['pdf_doc']
                            try:
                                new_doc = fitz.open(pdf_path)
                                # Only update state after successful open
                                _state['pdf_doc'] = new_doc
                                _state['pdf_path'] = pdf_path
                                _state['session'] = session
                                _state['dashboard_mode'] = False
                                _state['product_index'] = None  # New session
                                # Close old doc after state update
                                if old_doc:
                                    try:
                                        old_doc.close()
                                    except Exception as e:
                                        print(f"Warning: Error closing old PDF: {e}", file=sys.stderr)
                            except Exception as e:
                                print(f"Warning: Failed to open PDF {pdf_path}: {e}", file=sys.stderr)

    with _state_lock:
        session = _state['session']
        dashboard_mode = _state['dashboard_mode']

    if dashboard_mode or session is None:
        # Dashboard mode - no catalog loaded
        return render_template(
            'verify.html',
            catalog_name=None,
            total_pages=0,
            total_products=0,
            dashboard_mode=True,
            csrf_token=_csrf_token,
        )

    return render_template(
        'verify.html',
        catalog_name=session.source_file,
        total_pages=session.total_pages,
        total_products=len(session.products),
        dashboard_mode=False,
        csrf_token=_csrf_token,
    )


# ============================================
# Catalog Management API
# ============================================

@app.route('/api/catalogs')
def get_catalogs():
    """List all catalogs with status."""
    catalogs = list_catalogs()
    # Thread-safe access to _state
    with _state_lock:
        active = _state['session'].source_file if _state['session'] else None
    return jsonify({
        'catalogs': catalogs,
        'active': active,
    })


def _check_csrf() -> tuple[dict, int] | None:
    """Check CSRF token from request. Returns error response tuple if invalid, None if valid."""
    token = request.headers.get('X-CSRF-Token') or (request.json or {}).get('csrf_token')
    if not _verify_csrf_token(token):
        return {'error': 'Invalid CSRF token'}, 403
    return None


@app.route('/api/upload', methods=['POST'])
def upload_catalog():
    """Upload a new PDF catalog."""
    # CSRF check - for multipart forms, check header
    token = request.headers.get('X-CSRF-Token')
    if not _verify_csrf_token(token):
        return jsonify({'error': 'Invalid CSRF token'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '' or file.filename is None:
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are allowed'}), 400

    # Secure the filename - validates and sanitizes
    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({'error': 'Invalid filename'}), 400

    # Additional validation: ensure filename ends with .pdf after sanitization
    if not filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Invalid filename after sanitization'}), 400

    # Ensure no path components remain (double-check secure_filename worked)
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Invalid filename'}), 400

    # Ensure catalogs directory exists
    CATALOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Save the file
    pdf_path = CATALOGS_DIR / filename
    file.save(pdf_path)

    # Validate PDF and get page count
    doc = None
    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)
    except Exception as e:
        pdf_path.unlink()  # Remove invalid PDF
        return jsonify({'error': f'Invalid PDF file: {e}'}), 400
    finally:
        if doc:
            doc.close()

    return jsonify({
        'success': True,
        'name': pdf_path.stem,
        'filename': filename,
        'pages': page_count,
    })


@app.route('/api/extract/<catalog_name>', methods=['POST'])
def start_extraction(catalog_name: str):
    """Start auto-extraction for a catalog.

    Uses smart pipeline extraction that automatically selects the best
    extraction methods based on PDF characteristics.
    """
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    # Validate catalog name to prevent path traversal
    catalog_name = _validate_catalog_name(catalog_name)
    if not catalog_name:
        return jsonify({'error': 'Invalid catalog name'}), 400

    # Find the PDF first (before acquiring lock)
    pdf_path = CATALOGS_DIR / f"{catalog_name}.pdf"
    if not pdf_path.exists():
        pdf_path = CATALOGS_DIR / f"{catalog_name}.PDF"
    if not pdf_path.exists():
        return jsonify({'error': 'PDF not found'}), 404

    # Check and initialize in single lock acquisition (prevents TOCTOU race)
    with _extraction_lock:
        if catalog_name in _extraction_jobs and _extraction_jobs[catalog_name]['status'] == 'extracting':
            return jsonify({'error': 'Extraction already in progress'}), 400

        # Clean up old completed/errored jobs for this catalog
        _extraction_jobs[catalog_name] = {
            'status': 'extracting',
            'progress': {'page': 0, 'total_pages': 0, 'products': 0},
            'error': None,
        }

    def progress_callback(page_num, total_pages, products_count):
        with _extraction_lock:
            _extraction_jobs[catalog_name]['progress'] = {
                'page': page_num,
                'total_pages': total_pages,
                'products': products_count,
            }

    def run_extraction():
        try:
            from .auto_extractor import AutoExtractor

            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            extractor = AutoExtractor(pdf_path, SESSIONS_DIR)
            session = extractor.run(progress_callback=progress_callback, show_console=False)

            import time as _time
            with _extraction_lock:
                _extraction_jobs[catalog_name]['status'] = 'completed'
                _extraction_jobs[catalog_name]['progress']['products'] = len(session.products)
                _extraction_jobs[catalog_name]['completed_at'] = _time.time()
        except Exception as e:
            # Log full stack trace for debugging
            error_msg = f"{e}\n{traceback.format_exc()}"
            print(f"Extraction error for {catalog_name}: {error_msg}", file=sys.stderr)
            import time as _time
            with _extraction_lock:
                _extraction_jobs[catalog_name]['status'] = 'error'
                _extraction_jobs[catalog_name]['error'] = str(e)
                _extraction_jobs[catalog_name]['completed_at'] = _time.time()

    # Start extraction in background thread
    thread = threading.Thread(target=run_extraction, daemon=True)
    thread.start()

    return jsonify({
        'success': True,
        'message': 'Extraction started',
        'catalog': catalog_name,
    })


@app.route('/api/extract/<catalog_name>/status')
def get_extraction_status(catalog_name: str):
    """Get extraction progress for a catalog."""
    # Cleanup old completed jobs periodically
    _cleanup_completed_jobs()

    # Thread-safe access to _extraction_jobs
    with _extraction_lock:
        if catalog_name in _extraction_jobs:
            job = _extraction_jobs[catalog_name]
            progress = job.get('progress')
            return jsonify({
                'status': job['status'],
                'progress': progress.copy() if progress else {},
                'error': job.get('error'),
            })

    # Not in jobs - check if session exists (do this outside lock to avoid blocking)
    session_path = SESSIONS_DIR / f"{catalog_name}.session.json"
    if session_path.exists():
        session = ExtractionSession.load(session_path)
        if session:
            return jsonify({
                'status': 'completed',
                'progress': {
                    'page': session.total_pages,
                    'total_pages': session.total_pages,
                    'products': len(session.products),
                },
            })

    return jsonify({'error': 'No extraction job found'}), 404


@app.route('/api/switch/<catalog_name>', methods=['POST'])
def switch_catalog(catalog_name: str):
    """Switch to a different catalog."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    # Validate catalog name to prevent path traversal
    catalog_name = _validate_catalog_name(catalog_name)
    if not catalog_name:
        return jsonify({'error': 'Invalid catalog name'}), 400

    # Find session
    session_path = SESSIONS_DIR / f"{catalog_name}.session.json"
    if not session_path.exists():
        return jsonify({'error': 'Session not found. Extract the catalog first.'}), 404

    session = ExtractionSession.load(session_path)
    if not session:
        return jsonify({'error': 'Failed to load session'}), 500

    # Find PDF
    pdf_path = CATALOGS_DIR / session.source_file
    if not pdf_path.exists():
        pdf_path = Path.cwd() / session.source_file
    if not pdf_path.exists():
        return jsonify({'error': f'PDF not found: {session.source_file}'}), 404

    # Thread-safe state update with proper resource cleanup
    with _state_lock:
        old_pdf_doc = _state['pdf_doc']

        # Open new PDF first (before closing old one)
        try:
            new_pdf_doc = fitz.open(pdf_path)
        except Exception as e:
            return jsonify({'error': f'Failed to open PDF: {e}'}), 500

        # Close old PDF after successfully opening new one
        if old_pdf_doc:
            try:
                old_pdf_doc.close()
            except Exception as e:
                print(f"Warning: Error closing old PDF: {e}", file=sys.stderr)

        # Update state
        _state['pdf_path'] = pdf_path
        _state['session'] = session
        _state['pdf_doc'] = new_pdf_doc
        _state['dashboard_mode'] = False
        _invalidate_product_index()  # New session, new index

    return jsonify({
        'success': True,
        'catalog_name': session.source_file,
        'total_pages': session.total_pages,
        'total_products': len(session.products),
    })


# ============================================
# Page and Product API
# ============================================

@app.route('/api/page/<int:page_num>')
def get_page(page_num: int):
    """Get page data including image and extracted products."""
    with _state_lock:
        session = _state['session']
        if session is None:
            return jsonify({'error': 'No catalog loaded'}), 400
        total_pages = session.total_pages
        # Copy products list to avoid holding lock during iteration
        session_products = list(session.products)

    if page_num < 1 or page_num > total_pages:
        return jsonify({'error': 'Invalid page number'}), 400

    # Get products for this page (use stable product IDs, not list indices)
    products = []
    for p in session_products:
        if p.page_number == page_num:
            product_data = {
                'id': p.id,
                'product_name': p.product_name,
                'description': p.description,
                'item_no': p.item_no,
                'pkg': p.pkg,
                'uom': p.uom,
                'confidence': p.get_confidence_score(),
            }
            # Include field_locations if present
            if p.field_locations:
                product_data['field_locations'] = {
                    k: v.to_dict() for k, v in p.field_locations.items()
                }
            products.append(product_data)

    return jsonify({
        'page_number': page_num,
        'total_pages': total_pages,
        'products': products,
    })


@app.route('/api/page/<int:page_num>/image')
def get_page_image(page_num: int):
    """Get PDF page as PNG image."""
    with _state_lock:
        session = _state['session']
        pdf_doc = _state['pdf_doc']

        if session is None or pdf_doc is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        total_pages = session.total_pages

        if page_num < 1 or page_num > total_pages:
            return jsonify({'error': 'Invalid page number'}), 400

        # Render page to image (0-indexed in PyMuPDF)
        # Note: We hold the lock during rendering to prevent pdf_doc from being closed
        try:
            page = pdf_doc[page_num - 1]

            # Get zoom level from query params (default 1x, range 1x-5x)
            try:
                zoom = float(request.args.get('zoom', 1.0))
                zoom = max(1.0, min(zoom, 5.0))  # Clamp between 1x and 5x
            except (ValueError, TypeError):
                zoom = 1.0
            mat = fitz.Matrix(zoom, zoom)

            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes('png')
            # Explicitly free pixmap memory to prevent accumulation
            del pix
        except Exception as e:
            print(f"Error rendering page {page_num}: {e}", file=sys.stderr)
            return jsonify({'error': f'Failed to render page: {e}'}), 500

    return send_file(
        io.BytesIO(img_bytes),
        mimetype='image/png',
        download_name=f'page_{page_num}.png'
    )


def _build_product_index(session: ExtractionSession) -> dict[str, int]:
    """Build an index of product_id -> list index for O(1) lookup."""
    return {p.id: i for i, p in enumerate(session.products)}


def _invalidate_product_index():
    """Invalidate the cached product index (call after add/delete).

    IMPORTANT: Must be called while holding _state_lock to ensure thread safety.
    This function does not acquire the lock itself to avoid deadlocks when called
    from code that already holds the lock.
    """
    _state['product_index'] = None


def _find_product_by_id(session: ExtractionSession, product_id: str) -> tuple[Product | None, int]:
    """Find a product by its ID. Returns (product, index) or (None, -1).

    Uses cached index for O(1) lookup instead of O(n) linear search.

    IMPORTANT: Must be called while holding _state_lock to ensure thread safety.
    """
    # Build or use cached index
    if _state['product_index'] is None:
        _state['product_index'] = _build_product_index(session)

    index = _state['product_index'].get(product_id)
    if index is not None and index < len(session.products):
        product = session.products[index]
        # Verify the product ID matches (in case index is stale)
        if product.id == product_id:
            return product, index

    # Index miss or stale - rebuild and try again
    _state['product_index'] = _build_product_index(session)
    index = _state['product_index'].get(product_id)
    # Add bounds check and ID verification after rebuild
    if index is not None and index < len(session.products):
        product = session.products[index]
        if product.id == product_id:
            return product, index

    return None, -1


@app.route('/api/product/<product_id>', methods=['PUT'])
def update_product(product_id: str):
    """Update a product's data."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    data = request.json
    if data is None:
        return jsonify({'error': 'Invalid JSON body'}), 400

    with _state_lock:
        session = _state['session']
        if session is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        product, _ = _find_product_by_id(session, product_id)
        if product is None:
            return jsonify({'error': 'Product not found'}), 404

        # Update fields with sanitization
        if 'product_name' in data:
            product.product_name = _sanitize_product_field(data['product_name'], MAX_PRODUCT_NAME_LENGTH)
        if 'description' in data:
            product.description = _sanitize_product_field(data['description'])
        if 'item_no' in data:
            product.item_no = _sanitize_product_field(data['item_no'], 200)
        if 'pkg' in data:
            product.pkg = _sanitize_product_field(data['pkg'], 50)
        if 'uom' in data:
            product.uom = _sanitize_product_field(data['uom'], 50)

        # Update field locations if provided
        if 'field_locations' in data and data['field_locations']:
            for field_name, loc_data in data['field_locations'].items():
                product.field_locations[field_name] = FieldLocation.from_dict(loc_data)

        result = product.to_dict()

    return jsonify({'success': True, 'product': result})


@app.route('/api/product', methods=['POST'])
def add_product():
    """Add a new product."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    data = request.json
    if data is None:
        return jsonify({'error': 'Invalid JSON body'}), 400

    with _state_lock:
        session = _state['session']
        if session is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        # Sanitize page_number to prevent injection
        page_number = data.get('page_number', 1)
        if not isinstance(page_number, int) or page_number < 1:
            page_number = 1

        product = Product(
            product_name=_sanitize_product_field(data.get('product_name', ''), MAX_PRODUCT_NAME_LENGTH),
            description=_sanitize_product_field(data.get('description', '')),
            item_no=_sanitize_product_field(data.get('item_no', ''), 200),
            pkg=_sanitize_product_field(data.get('pkg', ''), 50),
            uom=_sanitize_product_field(data.get('uom', ''), 50),
            page_number=page_number,
            source_file=session.source_file,
        )

        session.add_product(product)
        _invalidate_product_index()  # Index changed
        result = {
            'success': True,
            'index': len(session.products) - 1,
            'product': product.to_dict()
        }

    return jsonify(result)


@app.route('/api/product/<product_id>', methods=['DELETE'])
def delete_product(product_id: str):
    """Delete a product."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    with _state_lock:
        session = _state['session']
        if session is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        product, index = _find_product_by_id(session, product_id)
        if product is None:
            return jsonify({'error': 'Product not found'}), 404

        deleted = session.products.pop(index)
        _invalidate_product_index()  # Index changed
        result = deleted.to_dict()

    return jsonify({'success': True, 'deleted': result})


@app.route('/api/save', methods=['POST'])
def save_session():
    """Save the current session."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    with _state_lock:
        session = _state['session']
        session_dir = _state['session_dir']

        if session is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        session.save(session_dir)
        products_count = len(session.products)

    return jsonify({'success': True, 'products_count': products_count})


@app.route('/api/export-csv', methods=['POST'])
def export_csv():
    """Export session to CSV file."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    with _state_lock:
        session = _state['session']
        session_dir = _state['session_dir']

        if session is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        # Save session first to ensure latest changes are persisted
        session.save(session_dir)

        # Export to CSV (extractions directory is sibling to sessions)
        extractions_dir = session_dir.parent / 'extractions'
        extractions_dir.mkdir(parents=True, exist_ok=True)

        csv_path = export_to_csv(session, extractions_dir)
        products_count = len(session.products)

    return jsonify({
        'success': True,
        'csv_path': str(csv_path),
        'products_count': products_count
    })


@app.route('/api/stats')
def get_stats():
    """Get session statistics including total product count and confidence data."""
    # Confidence threshold for "low confidence" items (95%)
    LOW_CONFIDENCE_THRESHOLD = 95.0

    with _state_lock:
        session = _state['session']

        if session is None:
            return jsonify({
                'total_products': 0,
                'total_pages': 0,
                'source_file': None,
                'dashboard_mode': True,
                'overall_confidence': 0,
                'low_confidence_count': 0,
                'low_confidence_products': [],
            })

        # Calculate confidence statistics
        total_confidence = 0.0
        low_confidence_products = []

        for p in session.products:
            score = p.get_confidence_score()
            total_confidence += score
            if score < LOW_CONFIDENCE_THRESHOLD:
                low_confidence_products.append({
                    'id': p.id,
                    'page': p.page_number,
                    'score': round(score, 1),
                    'item_no': p.item_no,
                    'product_name': p.product_name[:50] if p.product_name else '',
                })

        overall_confidence = (total_confidence / len(session.products)) if session.products else 100.0

        return jsonify({
            'total_products': len(session.products),
            'total_pages': session.total_pages,
            'source_file': session.source_file,
            'dashboard_mode': False,
            'overall_confidence': round(overall_confidence, 1),
            'low_confidence_count': len(low_confidence_products),
            'low_confidence_products': low_confidence_products,
        })


@app.route('/api/shutdown', methods=['POST'])
def shutdown():
    """Shutdown the server gracefully."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    with _state_lock:
        session = _state['session']
        session_dir = _state['session_dir']

        # Save session before shutdown if one is loaded
        if session and session_dir:
            session.save(session_dir)

    # Schedule shutdown using signal-based approach for safety
    import signal
    import time

    def shutdown_server():
        time.sleep(0.5)
        # Use SIGINT for graceful shutdown instead of os._exit
        # This allows cleanup handlers to run properly
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=shutdown_server, daemon=True).start()

    return jsonify({'success': True, 'message': 'Server shutting down'})


@app.route('/api/extract-text', methods=['POST'])
def extract_text_from_region():
    """Extract text from a selected region on a PDF page."""
    csrf_error = _check_csrf()
    if csrf_error:
        return jsonify(csrf_error[0]), csrf_error[1]

    data = request.json
    if data is None:
        return jsonify({'error': 'Invalid JSON body'}), 400

    page_num = data.get('page_number', 1)
    # Coordinates are in image space, need to convert to PDF space
    zoom = data.get('zoom', 2.0)

    # Validate zoom to prevent division by zero
    if not isinstance(zoom, (int, float)) or zoom <= 0:
        zoom = 2.0

    x0 = data.get('x0', 0) / zoom
    y0 = data.get('y0', 0) / zoom
    x1 = data.get('x1', 0) / zoom
    y1 = data.get('y1', 0) / zoom

    with _state_lock:
        pdf_doc = _state['pdf_doc']

        if pdf_doc is None:
            return jsonify({'error': 'No catalog loaded'}), 400

        if page_num < 1 or page_num > len(pdf_doc):
            return jsonify({'error': 'Invalid page number'}), 400

        page = pdf_doc[page_num - 1]
        rect = fitz.Rect(x0, y0, x1, y1)
        text = page.get_text('text', clip=rect).strip()

    return jsonify({
        'success': True,
        'text': text,
        'rect': {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1}
    })


def run_server(pdf_path: Path = None, session: ExtractionSession = None,
               session_dir: Path = None, host: str = '127.0.0.1', port: int = 5000,
               debug: bool = False, dashboard_mode: bool = False):
    """Run the Flask development server."""
    init_app(pdf_path, session, session_dir, dashboard_mode=dashboard_mode)

    mode = "Dashboard" if dashboard_mode or session is None else "Catalog"
    print(f"\n  Web Verifier ({mode} mode) running at: http://{host}:{port}")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=host, port=port, debug=debug)
