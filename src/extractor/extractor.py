"""Semi-automatic extraction workflow for catalog data."""

from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text

from .data_model import Product, ExtractionSession, PageContent
from .pdf_reader import PDFReader

console = Console()


class InteractiveExtractor:
    """Handles the semi-automatic extraction workflow."""

    def __init__(self, pdf_path: Path, session_dir: Path):
        self.pdf_path = Path(pdf_path)
        self.session_dir = session_dir
        self.session: Optional[ExtractionSession] = None

    def _get_session_path(self) -> Path:
        """Get the session file path for this PDF."""
        return self.session_dir / (self.pdf_path.stem + ".session.json")

    def load_or_create_session(self, total_pages: int) -> ExtractionSession:
        """Load existing session or create a new one."""
        session_path = self._get_session_path()
        session = ExtractionSession.load(session_path)

        if session:
            console.print(
                f"[green]Resuming session:[/green] {session.current_page}/{session.total_pages} pages, "
                f"{len(session.products)} products extracted"
            )
            return session

        return ExtractionSession(
            source_file=self.pdf_path.name,
            total_pages=total_pages,
            current_page=1,
        )

    def save_session(self) -> None:
        """Save current session to disk."""
        if self.session:
            self.session.save(self.session_dir)

    def display_page(self, page: PageContent) -> None:
        """Display page content with line numbers."""
        console.print()
        console.rule(f"[bold blue]Page {page.page_number}[/bold blue]")
        console.print()

        # Create numbered lines display
        lines_text = Text()
        for line_num, line in page.get_numbered_lines():
            lines_text.append(f"{line_num:4d} | ", style="dim")
            lines_text.append(f"{line}\n")

        console.print(Panel(lines_text, title="Page Content", border_style="blue"))

    def prompt_line_selection(self, page: PageContent) -> list[str]:
        """Prompt user to select lines containing product data."""
        console.print()
        console.print("[yellow]Select lines containing product data.[/yellow]")
        console.print("Enter line numbers separated by commas (e.g., 1,2,3) or ranges (e.g., 1-5)")
        console.print("Press Enter with no input to skip this page")
        console.print()

        selection = Prompt.ask("Lines", default="")

        if not selection.strip():
            return []

        # Parse line selection
        selected_lines = []
        for part in selection.split(","):
            part = part.strip()
            if "-" in part:
                try:
                    start, end = part.split("-")
                    for i in range(int(start), int(end) + 1):
                        if 1 <= i <= len(page.lines):
                            selected_lines.append(page.lines[i - 1])
                except ValueError:
                    console.print(f"[red]Invalid range: {part}[/red]")
            else:
                try:
                    line_num = int(part)
                    if 1 <= line_num <= len(page.lines):
                        selected_lines.append(page.lines[line_num - 1])
                except ValueError:
                    console.print(f"[red]Invalid line number: {part}[/red]")

        return selected_lines

    def prompt_product_fields(
        self, selected_lines: list[str], page_number: int
    ) -> Optional[Product]:
        """Prompt user to map selected lines to product fields."""
        console.print()
        console.print("[cyan]Selected lines:[/cyan]")
        for i, line in enumerate(selected_lines, 1):
            console.print(f"  {i}. {line}")
        console.print()
        console.print("[yellow]Enter product details (press Enter to use suggested value):[/yellow]")
        console.print()

        product_name = Prompt.ask("Product name", default=selected_lines[0] if selected_lines else "")
        if not product_name:
            console.print("[red]Product name is required. Skipping this product.[/red]")
            return None

        description = Prompt.ask("Description (size/quantity)", default="")
        item_no = Prompt.ask("Item number (SKU)", default="")
        pkg = Prompt.ask("Package quantity", default="")
        uom = Prompt.ask("Unit of measure", default="")

        product = Product(
            product_name=product_name,
            description=description,
            item_no=item_no,
            pkg=pkg,
            uom=uom,
            page_number=page_number,
            source_file=self.pdf_path.name,
        )

        # Display product for confirmation
        self.display_product(product)

        if Confirm.ask("Save this product?", default=True):
            return product
        return None

    def display_product(self, product: Product) -> None:
        """Display a product in a formatted table."""
        table = Table(title="Product Preview", show_header=False, border_style="green")
        table.add_column("Field", style="cyan")
        table.add_column("Value")

        table.add_row("Product Name", product.product_name)
        table.add_row("Description", product.description)
        table.add_row("Item No", product.item_no)
        table.add_row("Package Qty", product.pkg)
        table.add_row("UOM", product.uom)
        table.add_row("Page", str(product.page_number))

        console.print(table)

    def extract_from_page(self, page: PageContent) -> list[Product]:
        """Extract products from a single page interactively."""
        products = []

        while True:
            self.display_page(page)

            selected_lines = self.prompt_line_selection(page)
            if not selected_lines:
                break

            product = self.prompt_product_fields(selected_lines, page.page_number)
            if product:
                products.append(product)
                console.print("[green]Product saved![/green]")

            if not Confirm.ask("Extract another product from this page?", default=False):
                break

        return products

    def run(self) -> ExtractionSession:
        """Run the interactive extraction workflow."""
        console.print(Panel(
            f"[bold]Processing:[/bold] {self.pdf_path.name}",
            title="Catalog Data Extractor",
            border_style="blue",
        ))

        with PDFReader(self.pdf_path) as reader:
            self.session = self.load_or_create_session(reader.total_pages)

            console.print(f"\n[cyan]Total pages:[/cyan] {reader.total_pages}")
            console.print("[dim]Commands: 'q' to quit and save, 's' to skip page, 'g N' to go to page N[/dim]\n")

            page_num = self.session.current_page

            while page_num <= reader.total_pages:
                page = reader.get_page(page_num)

                # Check for navigation commands
                self.display_page(page)
                console.print()

                action = Prompt.ask(
                    f"[Page {page_num}/{reader.total_pages}] Action",
                    choices=["extract", "skip", "goto", "quit"],
                    default="extract",
                )

                if action == "quit":
                    self.session.current_page = page_num
                    self.save_session()
                    console.print("[yellow]Session saved. Use 'resume' to continue later.[/yellow]")
                    break

                elif action == "skip":
                    page_num += 1
                    continue

                elif action == "goto":
                    try:
                        target = int(Prompt.ask("Go to page"))
                        if 1 <= target <= reader.total_pages:
                            page_num = target
                        else:
                            console.print(f"[red]Invalid page number. Must be 1-{reader.total_pages}[/red]")
                    except ValueError:
                        console.print("[red]Invalid page number[/red]")
                    continue

                elif action == "extract":
                    # Hide the page display we just showed and re-run extraction
                    products = self.extract_from_page(page)
                    for product in products:
                        self.session.add_product(product)

                    # Auto-save after each page
                    self.save_session()
                    page_num += 1

            # Mark as completed if we reached the end
            if page_num > reader.total_pages:
                self.session.completed = True
                self.save_session()
                console.print("[green]Extraction completed![/green]")

        return self.session
