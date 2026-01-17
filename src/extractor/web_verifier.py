"""Web-based verification UI for catalog data extraction."""

import atexit
import io
import threading
from pathlib import Path

import fitz  # PyMuPDF
from flask import Flask, render_template, jsonify, request, send_file
from werkzeug.utils import secure_filename

from .data_model import Product, ExtractionSession, FieldLocation
from .exporter import export_to_csv

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
}

# Background extraction jobs: {catalog_name: {'status': str, 'progress': dict, 'error': str}}
_extraction_jobs: dict = {}
_extraction_lock = threading.Lock()  # Thread lock for _extraction_jobs access

# Track if cleanup has been registered
_cleanup_registered = False


def cleanup_pdf():
    """Close the PDF document on exit."""
    if _state['pdf_doc']:
        _state['pdf_doc'].close()
        _state['pdf_doc'] = None


def list_catalogs() -> list[dict]:
    """List all catalogs with their status.

    Returns list of dicts with keys: name, status, products_count, pages
    Status can be: 'uploaded', 'extracting', 'ready', 'exported'
    """
    catalogs = []

    # Ensure directories exist
    CATALOGS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Find all PDFs in catalogs directory
    pdf_files = set()
    for pdf in CATALOGS_DIR.glob("*.pdf"):
        pdf_files.add(pdf.stem)
    for pdf in CATALOGS_DIR.glob("*.PDF"):
        pdf_files.add(pdf.stem)

    # Find all sessions
    sessions = {}
    for session_file in SESSIONS_DIR.glob("*.session.json"):
        name = session_file.stem.replace('.session', '')
        sessions[name] = session_file

    # Find all exports
    exports = set()
    for csv_file in EXTRACTIONS_DIR.glob("*.csv"):
        exports.add(csv_file.stem)

    # Combine into catalog list
    all_names = pdf_files | set(sessions.keys())

    for name in sorted(all_names):
        catalog = {
            'name': name,
            'has_pdf': name in pdf_files,
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
            # Otherwise just uploaded PDF
            elif name in pdf_files:
                catalog['status'] = 'uploaded'

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

    # Register cleanup on exit (only once)
    global _cleanup_registered
    if not _cleanup_registered:
        atexit.register(cleanup_pdf)
        _cleanup_registered = True

    return app


@app.route('/')
def index():
    """Main verification page."""
    session = _state['session']

    if _state['dashboard_mode'] or session is None:
        # Dashboard mode - no catalog loaded
        return render_template(
            'verify.html',
            catalog_name=None,
            total_pages=0,
            total_products=0,
            dashboard_mode=True,
        )

    return render_template(
        'verify.html',
        catalog_name=session.source_file,
        total_pages=session.total_pages,
        total_products=len(session.products),
        dashboard_mode=False,
    )


# ============================================
# Catalog Management API
# ============================================

@app.route('/api/catalogs')
def get_catalogs():
    """List all catalogs with status."""
    catalogs = list_catalogs()
    return jsonify({
        'catalogs': catalogs,
        'active': _state['session'].source_file if _state['session'] else None,
    })


@app.route('/api/upload', methods=['POST'])
def upload_catalog():
    """Upload a new PDF catalog."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are allowed'}), 400

    # Secure the filename
    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({'error': 'Invalid filename'}), 400

    # Ensure catalogs directory exists
    CATALOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Save the file
    pdf_path = CATALOGS_DIR / filename
    file.save(pdf_path)

    # Get page count
    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()
    except Exception as e:
        pdf_path.unlink()  # Remove invalid PDF
        return jsonify({'error': f'Invalid PDF file: {e}'}), 400

    return jsonify({
        'success': True,
        'name': pdf_path.stem,
        'filename': filename,
        'pages': page_count,
    })


@app.route('/api/extract/<catalog_name>', methods=['POST'])
def start_extraction(catalog_name: str):
    """Start auto-extraction for a catalog."""
    # Check if already extracting (thread-safe)
    with _extraction_lock:
        if catalog_name in _extraction_jobs and _extraction_jobs[catalog_name]['status'] == 'extracting':
            return jsonify({'error': 'Extraction already in progress'}), 400

    # Find the PDF
    pdf_path = CATALOGS_DIR / f"{catalog_name}.pdf"
    if not pdf_path.exists():
        pdf_path = CATALOGS_DIR / f"{catalog_name}.PDF"
    if not pdf_path.exists():
        return jsonify({'error': 'PDF not found'}), 404

    # Initialize job status (thread-safe)
    with _extraction_lock:
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

            with _extraction_lock:
                _extraction_jobs[catalog_name]['status'] = 'completed'
                _extraction_jobs[catalog_name]['progress']['products'] = len(session.products)
        except Exception as e:
            with _extraction_lock:
                _extraction_jobs[catalog_name]['status'] = 'error'
                _extraction_jobs[catalog_name]['error'] = str(e)

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
    # Thread-safe access to _extraction_jobs
    with _extraction_lock:
        if catalog_name not in _extraction_jobs:
            # Check if session exists (already extracted)
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

        job = _extraction_jobs[catalog_name]
        return jsonify({
            'status': job['status'],
            'progress': job['progress'].copy(),
            'error': job.get('error'),
        })


@app.route('/api/switch/<catalog_name>', methods=['POST'])
def switch_catalog(catalog_name: str):
    """Switch to a different catalog."""
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

    # Close current PDF if open
    if _state['pdf_doc']:
        _state['pdf_doc'].close()

    # Load new catalog
    _state['pdf_path'] = pdf_path
    _state['session'] = session
    _state['pdf_doc'] = fitz.open(pdf_path)
    _state['dashboard_mode'] = False

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
    session = _state['session']

    if session is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    if page_num < 1 or page_num > session.total_pages:
        return jsonify({'error': 'Invalid page number'}), 400

    # Get products for this page (use stable product IDs, not list indices)
    products = []
    for p in session.products:
        if p.page_number == page_num:
            product_data = {
                'id': p.id,
                'product_name': p.product_name,
                'description': p.description,
                'item_no': p.item_no,
                'pkg': p.pkg,
                'uom': p.uom,
            }
            # Include field_locations if present
            if p.field_locations:
                product_data['field_locations'] = {
                    k: v.to_dict() for k, v in p.field_locations.items()
                }
            products.append(product_data)

    return jsonify({
        'page_number': page_num,
        'total_pages': session.total_pages,
        'products': products,
    })


@app.route('/api/page/<int:page_num>/image')
def get_page_image(page_num: int):
    """Get PDF page as PNG image."""
    session = _state['session']
    pdf_doc = _state['pdf_doc']

    if session is None or pdf_doc is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    if page_num < 1 or page_num > session.total_pages:
        return jsonify({'error': 'Invalid page number'}), 400

    # Render page to image (0-indexed in PyMuPDF)
    page = pdf_doc[page_num - 1]

    # Get zoom level from query params (default 2x for good quality)
    try:
        zoom = float(request.args.get('zoom', 1.0))
        zoom = max(1.0, min(zoom, 5.0))  # Clamp between 1x and 5x
    except (ValueError, TypeError):
        zoom = 1.0
    mat = fitz.Matrix(zoom, zoom)

    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes('png')

    return send_file(
        io.BytesIO(img_bytes),
        mimetype='image/png',
        download_name=f'page_{page_num}.png'
    )


def _find_product_by_id(session, product_id: str):
    """Find a product by its ID. Returns (product, index) or (None, -1)."""
    for i, p in enumerate(session.products):
        if p.id == product_id:
            return p, i
    return None, -1


@app.route('/api/product/<product_id>', methods=['PUT'])
def update_product(product_id: str):
    """Update a product's data."""
    session = _state['session']

    if session is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    product, _ = _find_product_by_id(session, product_id)
    if product is None:
        return jsonify({'error': 'Product not found'}), 404

    data = request.json
    if data is None:
        return jsonify({'error': 'Invalid JSON body'}), 400

    # Update fields
    if 'product_name' in data:
        product.product_name = data['product_name']
    if 'description' in data:
        product.description = data['description']
    if 'item_no' in data:
        product.item_no = data['item_no']
    if 'pkg' in data:
        product.pkg = data['pkg']
    if 'uom' in data:
        product.uom = data['uom']

    # Update field locations if provided
    if 'field_locations' in data and data['field_locations']:
        for field_name, loc_data in data['field_locations'].items():
            product.field_locations[field_name] = FieldLocation.from_dict(loc_data)

    return jsonify({'success': True, 'product': product.to_dict()})


@app.route('/api/product', methods=['POST'])
def add_product():
    """Add a new product."""
    session = _state['session']

    if session is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    data = request.json

    if data is None:
        return jsonify({'error': 'Invalid JSON body'}), 400

    product = Product(
        product_name=data.get('product_name', ''),
        description=data.get('description', ''),
        item_no=data.get('item_no', ''),
        pkg=data.get('pkg', ''),
        uom=data.get('uom', ''),
        page_number=data.get('page_number', 1),
        source_file=session.source_file,
    )

    session.add_product(product)

    return jsonify({
        'success': True,
        'index': len(session.products) - 1,
        'product': product.to_dict()
    })


@app.route('/api/product/<product_id>', methods=['DELETE'])
def delete_product(product_id: str):
    """Delete a product."""
    session = _state['session']

    if session is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    product, index = _find_product_by_id(session, product_id)
    if product is None:
        return jsonify({'error': 'Product not found'}), 404

    deleted = session.products.pop(index)

    return jsonify({'success': True, 'deleted': deleted.to_dict()})


@app.route('/api/save', methods=['POST'])
def save_session():
    """Save the current session."""
    session = _state['session']
    session_dir = _state['session_dir']

    if session is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    session.save(session_dir)

    return jsonify({'success': True, 'products_count': len(session.products)})


@app.route('/api/export-csv', methods=['POST'])
def export_csv():
    """Export session to CSV file."""
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

    return jsonify({
        'success': True,
        'csv_path': str(csv_path),
        'products_count': len(session.products)
    })


@app.route('/api/stats')
def get_stats():
    """Get session statistics including total product count."""
    session = _state['session']

    if session is None:
        return jsonify({
            'total_products': 0,
            'total_pages': 0,
            'source_file': None,
            'dashboard_mode': True,
        })

    return jsonify({
        'total_products': len(session.products),
        'total_pages': session.total_pages,
        'source_file': session.source_file,
        'dashboard_mode': False,
    })


@app.route('/api/shutdown', methods=['POST'])
def shutdown():
    """Shutdown the server gracefully."""
    session = _state['session']
    session_dir = _state['session_dir']

    # Save session before shutdown if one is loaded
    if session and session_dir:
        session.save(session_dir)

    # Schedule shutdown
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
    else:
        # For newer versions of werkzeug, use os._exit
        import os
        def shutdown_server():
            import time
            time.sleep(0.5)
            os._exit(0)
        threading.Thread(target=shutdown_server).start()

    return jsonify({'success': True, 'message': 'Server shutting down'})


@app.route('/api/extract-text', methods=['POST'])
def extract_text_from_region():
    """Extract text from a selected region on a PDF page."""
    pdf_doc = _state['pdf_doc']

    if pdf_doc is None:
        return jsonify({'error': 'No catalog loaded'}), 400

    data = request.json

    if data is None:
        return jsonify({'error': 'Invalid JSON body'}), 400

    page_num = data.get('page_number', 1)
    # Coordinates are in image space, need to convert to PDF space
    zoom = data.get('zoom', 2.0)
    x0 = data.get('x0', 0) / zoom
    y0 = data.get('y0', 0) / zoom
    x1 = data.get('x1', 0) / zoom
    y1 = data.get('y1', 0) / zoom

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
