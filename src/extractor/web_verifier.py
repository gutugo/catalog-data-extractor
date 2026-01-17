"""Web-based verification UI for catalog data extraction."""

import atexit
import io
from pathlib import Path

import fitz  # PyMuPDF
from flask import Flask, render_template, jsonify, request, send_file

from .data_model import Product, ExtractionSession

# Flask app - static_folder=None since we don't have static files
app = Flask(__name__, template_folder='templates', static_folder=None)

# Global state (set when launching)
_state: dict = {
    'pdf_path': None,
    'session': None,
    'session_dir': None,
    'pdf_doc': None,
}


def cleanup_pdf():
    """Close the PDF document on exit."""
    if _state['pdf_doc']:
        _state['pdf_doc'].close()
        _state['pdf_doc'] = None


def init_app(pdf_path: Path, session: ExtractionSession, session_dir: Path) -> Flask:
    """Initialize the Flask app with PDF and session data."""
    _state['pdf_path'] = pdf_path
    _state['session'] = session
    _state['session_dir'] = session_dir
    _state['pdf_doc'] = fitz.open(pdf_path)

    # Register cleanup on exit
    atexit.register(cleanup_pdf)

    return app


@app.route('/')
def index():
    """Main verification page."""
    session = _state['session']
    return render_template(
        'verify.html',
        catalog_name=session.source_file,
        total_pages=session.total_pages,
        total_products=len(session.products),
    )


@app.route('/api/page/<int:page_num>')
def get_page(page_num: int):
    """Get page data including image and extracted products."""
    session = _state['session']

    if page_num < 1 or page_num > session.total_pages:
        return jsonify({'error': 'Invalid page number'}), 400

    # Get products for this page (use stable product IDs, not list indices)
    products = [
        {
            'id': p.id,
            'product_name': p.product_name,
            'description': p.description,
            'item_no': p.item_no,
            'pkg': p.pkg,
            'uom': p.uom,
        }
        for p in session.products
        if p.page_number == page_num
    ]

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

    if page_num < 1 or page_num > session.total_pages:
        return jsonify({'error': 'Invalid page number'}), 400

    # Render page to image (0-indexed in PyMuPDF)
    page = pdf_doc[page_num - 1]

    # Get zoom level from query params (default 2x for good quality)
    try:
        zoom = float(request.args.get('zoom', 2.0))
        zoom = max(0.5, min(zoom, 5.0))  # Clamp between 0.5x and 5x
    except (ValueError, TypeError):
        zoom = 2.0
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

    return jsonify({'success': True, 'product': product.to_dict()})


@app.route('/api/product', methods=['POST'])
def add_product():
    """Add a new product."""
    session = _state['session']
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

    session.save(session_dir)

    return jsonify({'success': True, 'products_count': len(session.products)})


@app.route('/api/stats')
def get_stats():
    """Get session statistics including total product count."""
    session = _state['session']

    return jsonify({
        'total_products': len(session.products),
        'total_pages': session.total_pages,
        'source_file': session.source_file,
    })


@app.route('/api/extract-text', methods=['POST'])
def extract_text_from_region():
    """Extract text from a selected region on a PDF page."""
    pdf_doc = _state['pdf_doc']
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


def run_server(pdf_path: Path, session: ExtractionSession, session_dir: Path,
               host: str = '127.0.0.1', port: int = 5000, debug: bool = False):
    """Run the Flask development server."""
    init_app(pdf_path, session, session_dir)

    print(f"\n  Web Verifier running at: http://{host}:{port}")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=host, port=port, debug=debug)
