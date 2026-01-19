"""CSV export functionality for extracted catalog data."""

from pathlib import Path
from typing import Optional

import pandas as pd
from rich.console import Console
from rich.table import Table

from .data_model import ExtractionSession

console = Console()

# CSV column order
CSV_COLUMNS = [
    "product_name",
    "description",
    "item_no",
    "pkg",
    "uom",
    "page_number",
    "source_file",
]


def export_to_csv(
    session: ExtractionSession,
    output_dir: Path,
    filename: Optional[str] = None,
) -> Path:
    """Export extraction session to CSV file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        filename = Path(session.source_file).stem + ".csv"

    output_path = output_dir / filename

    # Convert products to DataFrame with proper column handling
    data = []
    for product in session.products:
        product_dict = product.to_dict()
        # Ensure all CSV columns are present with empty string defaults
        row = {col: product_dict.get(col, '') for col in CSV_COLUMNS}
        data.append(row)

    df = pd.DataFrame(data, columns=CSV_COLUMNS)

    # Replace any NaN values with empty strings for cleaner CSV output
    df = df.fillna('')

    # Export to CSV
    df.to_csv(output_path, index=False)

    console.print(f"[green]Exported {len(session.products)} products to:[/green] {output_path}")

    return output_path


def display_extraction_summary(session: ExtractionSession) -> None:
    """Display a summary of the extraction session."""
    console.print()
    console.rule("[bold]Extraction Summary[/bold]")

    table = Table(show_header=False, border_style="blue")
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("Source File", session.source_file)
    table.add_row("Total Pages", str(session.total_pages))
    table.add_row("Current Page", str(session.current_page))
    table.add_row("Products Extracted", str(len(session.products)))
    table.add_row("Status", "Completed" if session.completed else "In Progress")

    console.print(table)

    if session.products:
        console.print()
        console.print("[cyan]Extracted Products:[/cyan]")

        products_table = Table(border_style="dim")
        products_table.add_column("#", style="dim")
        products_table.add_column("Product Name")
        products_table.add_column("Item No")
        products_table.add_column("Page")

        for i, product in enumerate(session.products, 1):
            products_table.add_row(
                str(i),
                product.product_name[:50] + "..." if len(product.product_name) > 50 else product.product_name,
                product.item_no,
                str(product.page_number),
            )

        console.print(products_table)


def list_sessions(session_dir: Path) -> list[ExtractionSession]:
    """List all extraction sessions in the session directory."""
    session_dir = Path(session_dir)
    sessions = []

    if not session_dir.exists():
        return sessions

    for session_file in session_dir.glob("*.session.json"):
        session = ExtractionSession.load(session_file)
        if session:
            sessions.append(session)

    return sessions


def display_status(session_dir: Path, extractions_dir: Path) -> None:
    """Display status of all extractions."""
    sessions = list_sessions(session_dir)

    if not sessions:
        console.print("[yellow]No extraction sessions found.[/yellow]")
        return

    console.print()
    console.rule("[bold]Extraction Status[/bold]")

    table = Table(border_style="blue")
    table.add_column("Catalog")
    table.add_column("Progress", justify="right")
    table.add_column("Products", justify="right")
    table.add_column("Status")
    table.add_column("CSV Exported")

    for session in sessions:
        progress = f"{session.current_page}/{session.total_pages}"
        status = "[green]Completed[/green]" if session.completed else "[yellow]In Progress[/yellow]"

        csv_path = extractions_dir / (Path(session.source_file).stem + ".csv")
        csv_status = "[green]Yes[/green]" if csv_path.exists() else "[dim]No[/dim]"

        table.add_row(
            session.source_file,
            progress,
            str(len(session.products)),
            status,
            csv_status,
        )

    console.print(table)
