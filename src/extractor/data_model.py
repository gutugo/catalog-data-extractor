"""Data models for catalog product extraction."""

from dataclasses import dataclass, field
from typing import Optional
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path


def _generate_id() -> str:
    """Generate a unique product ID."""
    return str(uuid.uuid4())[:16]


@dataclass
class FieldLocation:
    """Represents the location of a field value on a PDF page."""

    x0: float  # Left edge (PDF coordinates)
    y0: float  # Top edge
    x1: float  # Right edge
    y1: float  # Bottom edge
    page_number: int
    confidence: float = 1.0  # 1.0 for table extraction, lower for text fallback

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'x0': self.x0,
            'y0': self.y0,
            'x1': self.x1,
            'y1': self.y1,
            'page_number': self.page_number,
            'confidence': self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FieldLocation":
        """Create from dictionary."""
        return cls(
            x0=data.get('x0', 0),
            y0=data.get('y0', 0),
            x1=data.get('x1', 0),
            y1=data.get('y1', 0),
            page_number=data.get('page_number', 0),
            confidence=data.get('confidence', 1.0),
        )


@dataclass
class Product:
    """Represents an extracted product from a catalog."""

    product_name: str
    description: str = ""
    item_no: str = ""
    pkg: str = ""
    uom: str = ""
    page_number: int = 0
    source_file: str = ""
    id: str = field(default_factory=_generate_id)
    field_locations: dict[str, FieldLocation] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert product to dictionary."""
        result = {
            'product_name': self.product_name,
            'description': self.description,
            'item_no': self.item_no,
            'pkg': self.pkg,
            'uom': self.uom,
            'page_number': self.page_number,
            'source_file': self.source_file,
            'id': self.id,
        }
        if self.field_locations:
            result['field_locations'] = {
                k: v.to_dict() for k, v in self.field_locations.items()
            }
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Product":
        """Create product from dictionary.

        Only uses known fields, ignoring any extra keys in data.
        """
        field_locations = {}
        if 'field_locations' in data and data['field_locations']:
            for field_name, loc_data in data['field_locations'].items():
                field_locations[field_name] = FieldLocation.from_dict(loc_data)

        # Handle ID: only generate new one if truly missing (None), not for empty string
        existing_id = data.get("id")
        product_id = existing_id if existing_id is not None else _generate_id()

        return cls(
            product_name=data.get("product_name", ""),
            description=data.get("description", ""),
            item_no=data.get("item_no", ""),
            pkg=data.get("pkg", ""),
            uom=data.get("uom", ""),
            page_number=data.get("page_number", 0),
            source_file=data.get("source_file", ""),
            id=product_id,
            field_locations=field_locations,
        )


@dataclass
class ExtractionSession:
    """Tracks the state of an extraction session."""

    source_file: str
    total_pages: int
    current_page: int = 1
    products: list[Product] = field(default_factory=list)
    completed: bool = False

    def add_product(self, product: Product) -> None:
        """Add a product to the session."""
        self.products.append(product)

    def to_dict(self) -> dict:
        """Convert session to dictionary."""
        return {
            "source_file": self.source_file,
            "total_pages": self.total_pages,
            "current_page": self.current_page,
            "products": [p.to_dict() for p in self.products],
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionSession":
        """Create session from dictionary.

        Raises:
            KeyError: If required fields 'source_file' or 'total_pages' are missing.
        """
        if "source_file" not in data:
            raise KeyError("Missing required field 'source_file' in session data")
        if "total_pages" not in data:
            raise KeyError("Missing required field 'total_pages' in session data")

        products = [Product.from_dict(p) for p in data.get("products", [])]
        return cls(
            source_file=data["source_file"],
            total_pages=data["total_pages"],
            current_page=data.get("current_page", 1),
            products=products,
            completed=data.get("completed", False),
        )

    def save(self, session_dir: Path) -> Path:
        """Save session to JSON file atomically.

        Writes to a temporary file first, then atomically renames to prevent
        data corruption if the process crashes during write.
        """
        session_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(self.source_file).stem + ".session.json"
        session_path = session_dir / filename

        # Write to temp file in same directory, then atomic rename
        fd, temp_path = tempfile.mkstemp(dir=session_dir, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
            # Atomic rename - os.replace works on POSIX; on Windows it may fail
            # if destination has certain attributes, so we handle that case
            try:
                os.replace(temp_path, session_path)
            except OSError:
                # Windows fallback: delete destination first, then rename
                if session_path.exists():
                    session_path.unlink()
                os.rename(temp_path, session_path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

        return session_path

    @classmethod
    def load(cls, session_path: Path) -> Optional["ExtractionSession"]:
        """Load session from JSON file.

        Returns None if file doesn't exist or is corrupted/invalid.
        """
        if not session_path.exists():
            return None
        try:
            with open(session_path) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            # Log error but return None to allow graceful handling
            print(f"Warning: Failed to load session {session_path}: {e}", file=sys.stderr)
            return None


@dataclass
class PageContent:
    """Represents extracted content from a PDF page."""

    page_number: int
    lines: list[str] = field(default_factory=list)
    raw_text: str = ""

    def get_numbered_lines(self) -> list[tuple[int, str]]:
        """Return lines with their line numbers (1-indexed)."""
        return [(i + 1, line) for i, line in enumerate(self.lines)]
