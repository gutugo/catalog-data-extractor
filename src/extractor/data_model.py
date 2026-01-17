"""Data models for catalog product extraction."""

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import uuid
from pathlib import Path


def _generate_id() -> str:
    """Generate a unique product ID."""
    return str(uuid.uuid4())[:8]


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

    def to_dict(self) -> dict:
        """Convert product to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Product":
        """Create product from dictionary.

        Only uses known fields, ignoring any extra keys in data.
        """
        return cls(
            product_name=data.get("product_name", ""),
            description=data.get("description", ""),
            item_no=data.get("item_no", ""),
            pkg=data.get("pkg", ""),
            uom=data.get("uom", ""),
            page_number=data.get("page_number", 0),
            source_file=data.get("source_file", ""),
            id=data.get("id") or _generate_id(),
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
        """Save session to JSON file."""
        session_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(self.source_file).stem + ".session.json"
        session_path = session_dir / filename
        with open(session_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
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
            import sys
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
