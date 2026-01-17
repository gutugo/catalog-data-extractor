"""Page-by-page verification of extracted data."""

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


class Verifier:
    """Page-by-page verification of extracted products."""

    def __init__(self, pdf_path: Path, session: ExtractionSession):
        self.pdf_path = Path(pdf_path)
        self.session = session
        self.products_by_page: dict[int, list[Product]] = {}

        # Group products by page
        for product in session.products:
            page_num = product.page_number
            if page_num not in self.products_by_page:
                self.products_by_page[page_num] = []
            self.products_by_page[page_num].append(product)

    def display_comparison(self, page: PageContent) -> None:
        """Display page content alongside extracted products."""
        page_num = page.page_number
        products = self.products_by_page.get(page_num, [])

        console.print()
        console.rule(f"[bold blue]Page {page_num}[/bold blue]")
        console.print()

        # Left panel: Page content
        lines_text = Text()
        for line_num, line in page.get_numbered_lines():
            lines_text.append(f"{line_num:3d} | ", style="dim")
            lines_text.append(f"{line}\n")

        page_panel = Panel(
            lines_text,
            title=f"PDF Content ({len(page.lines)} lines)",
            border_style="blue",
            width=60,
        )

        # Right panel: Extracted products
        if products:
            products_table = Table(show_header=True, header_style="bold cyan", box=None)
            products_table.add_column("#", style="dim", width=3)
            products_table.add_column("Item", width=6)
            products_table.add_column("Product Name", width=35)
            products_table.add_column("Desc", width=10)

            for i, product in enumerate(products, 1):
                name = product.product_name
                if len(name) > 35:
                    name = name[:32] + "..."
                products_table.add_row(
                    str(i),
                    product.item_no,
                    name,
                    product.description,
                )

            products_panel = Panel(
                products_table,
                title=f"Extracted ({len(products)} products)",
                border_style="green",
            )
        else:
            products_panel = Panel(
                "[dim]No products extracted from this page[/dim]",
                title="Extracted",
                border_style="yellow",
            )

        # Display side by side
        console.print(page_panel)
        console.print()
        console.print(products_panel)

    def edit_product(self, product: Product, index: int) -> Product:
        """Edit a single product."""
        console.print(f"\n[cyan]Editing product #{index}[/cyan]")

        product.product_name = Prompt.ask("Product name", default=product.product_name)
        product.description = Prompt.ask("Description", default=product.description)
        product.item_no = Prompt.ask("Item number", default=product.item_no)
        product.pkg = Prompt.ask("Package qty", default=product.pkg)
        product.uom = Prompt.ask("UOM", default=product.uom)

        return product

    def add_product(self, page_number: int) -> Optional[Product]:
        """Add a new product manually."""
        console.print("\n[cyan]Adding new product[/cyan]")

        product_name = Prompt.ask("Product name")
        if not product_name:
            return None

        return Product(
            product_name=product_name,
            description=Prompt.ask("Description", default=""),
            item_no=Prompt.ask("Item number", default=""),
            pkg=Prompt.ask("Package qty", default=""),
            uom=Prompt.ask("UOM", default=""),
            page_number=page_number,
            source_file=self.session.source_file,
        )

    def delete_product(self, page_num: int, index: int) -> bool:
        """Delete a product by index (1-based)."""
        products = self.products_by_page.get(page_num, [])
        if 1 <= index <= len(products):
            product = products[index - 1]
            products.remove(product)
            self.session.products.remove(product)
            return True
        return False

    def run(self, session_dir: Path, start_page: int = 1) -> ExtractionSession:
        """Run interactive verification."""
        console.print(Panel(
            f"[bold]Verifying:[/bold] {self.pdf_path.name}\n"
            f"[dim]Products: {len(self.session.products)} | Pages: {self.session.total_pages}[/dim]",
            title="Verification Mode",
            border_style="blue",
        ))

        console.print("\n[dim]Commands: [n]ext, [p]rev, [g]oto, [e]dit #, [a]dd, [d]elete #, [s]ave, [q]uit[/dim]\n")

        with PDFReader(self.pdf_path) as reader:
            page_num = start_page

            while True:
                if page_num < 1:
                    page_num = 1
                if page_num > reader.total_pages:
                    page_num = reader.total_pages

                page = reader.get_page(page_num)
                self.display_comparison(page)

                console.print()
                action = Prompt.ask(
                    f"[Page {page_num}/{reader.total_pages}]",
                    default="n"
                ).lower().strip()

                if action == 'n' or action == '':
                    page_num += 1
                    if page_num > reader.total_pages:
                        if Confirm.ask("Reached last page. Save and exit?", default=True):
                            self.session.save(session_dir)
                            console.print("[green]Session saved![/green]")
                            break
                        page_num = reader.total_pages

                elif action == 'p':
                    page_num -= 1

                elif action.startswith('g'):
                    try:
                        target = int(action[1:].strip() or Prompt.ask("Go to page"))
                        if 1 <= target <= reader.total_pages:
                            page_num = target
                        else:
                            console.print(f"[red]Invalid page (1-{reader.total_pages})[/red]")
                    except ValueError:
                        console.print("[red]Invalid page number[/red]")

                elif action.startswith('e'):
                    try:
                        idx = int(action[1:].strip() or Prompt.ask("Edit product #"))
                        products = self.products_by_page.get(page_num, [])
                        if 1 <= idx <= len(products):
                            self.edit_product(products[idx - 1], idx)
                        else:
                            console.print(f"[red]Invalid product # (1-{len(products)})[/red]")
                    except ValueError:
                        console.print("[red]Invalid product number[/red]")

                elif action == 'a':
                    product = self.add_product(page_num)
                    if product:
                        self.session.add_product(product)
                        if page_num not in self.products_by_page:
                            self.products_by_page[page_num] = []
                        self.products_by_page[page_num].append(product)
                        console.print("[green]Product added![/green]")

                elif action.startswith('d'):
                    try:
                        idx = int(action[1:].strip() or Prompt.ask("Delete product #"))
                        if self.delete_product(page_num, idx):
                            console.print("[yellow]Product deleted[/yellow]")
                        else:
                            console.print("[red]Invalid product #[/red]")
                    except ValueError:
                        console.print("[red]Invalid product number[/red]")

                elif action == 's':
                    self.session.save(session_dir)
                    console.print("[green]Session saved![/green]")

                elif action == 'q':
                    if Confirm.ask("Save before quitting?", default=True):
                        self.session.save(session_dir)
                        console.print("[green]Session saved![/green]")
                    break

        return self.session
