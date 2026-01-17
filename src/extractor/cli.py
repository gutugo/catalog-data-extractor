"""CLI interface for catalog data extractor."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from .data_model import ExtractionSession
from .extractor import InteractiveExtractor
from .auto_extractor import AutoExtractor
from .verifier import Verifier
from .exporter import export_to_csv, display_extraction_summary, display_status
# web_verifier imported lazily in web_verify command to avoid Flask dependency for other commands

app = typer.Typer(
    name="extractor",
    help="Semi-automatic extraction of product data from PDF catalogs.",
    add_completion=False,
)

console = Console()

# Default directories
BASE_DIR = Path.cwd()
CATALOGS_DIR = BASE_DIR / "catalogs"
PROCESSED_DIR = BASE_DIR / "processed"
SESSIONS_DIR = PROCESSED_DIR / "sessions"
EXTRACTIONS_DIR = PROCESSED_DIR / "extractions"


def ensure_directories() -> None:
    """Ensure required directories exist."""
    PROCESSED_DIR.mkdir(exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)
    EXTRACTIONS_DIR.mkdir(exist_ok=True)


@app.command()
def process(
    pdf_path: Path = typer.Argument(
        ...,
        help="Path to the PDF catalog to process",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Process a PDF catalog interactively."""
    ensure_directories()

    if not pdf_path.suffix.lower() == ".pdf":
        console.print(f"[red]Error:[/red] {pdf_path} is not a PDF file")
        raise typer.Exit(1)

    extractor = InteractiveExtractor(pdf_path, SESSIONS_DIR)
    session = extractor.run()

    display_extraction_summary(session)

    if session.products:
        export_to_csv(session, EXTRACTIONS_DIR)


@app.command()
def process_all(
    catalog_dir: Path = typer.Argument(
        ...,
        help="Directory containing PDF catalogs",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """Process all PDF catalogs in a directory."""
    ensure_directories()

    pdf_files = list(catalog_dir.glob("*.pdf")) + list(catalog_dir.glob("*.PDF"))

    if not pdf_files:
        console.print(f"[yellow]No PDF files found in {catalog_dir}[/yellow]")
        raise typer.Exit(0)

    console.print(f"[cyan]Found {len(pdf_files)} PDF files to process[/cyan]")

    for pdf_path in pdf_files:
        console.print(Panel(f"Processing: {pdf_path.name}", border_style="blue"))

        extractor = InteractiveExtractor(pdf_path, SESSIONS_DIR)
        session = extractor.run()

        if session.products:
            export_to_csv(session, EXTRACTIONS_DIR)

        console.print()


@app.command()
def resume(
    catalog_name: str = typer.Argument(
        ...,
        help="Name of the catalog to resume (without extension)",
    ),
) -> None:
    """Resume an incomplete extraction session."""
    ensure_directories()

    # Find the session file
    session_path = SESSIONS_DIR / f"{catalog_name}.session.json"

    if not session_path.exists():
        console.print(f"[red]No session found for:[/red] {catalog_name}")
        console.print(f"[dim]Looking for: {session_path}[/dim]")
        raise typer.Exit(1)

    session = ExtractionSession.load(session_path)
    if not session:
        console.print(f"[red]Failed to load session:[/red] {session_path}")
        raise typer.Exit(1)

    if session.completed:
        console.print(f"[yellow]Session already completed:[/yellow] {catalog_name}")
        display_extraction_summary(session)
        raise typer.Exit(0)

    # Find the original PDF
    pdf_path = CATALOGS_DIR / session.source_file
    if not pdf_path.exists():
        # Try to find it in current directory
        pdf_path = BASE_DIR / session.source_file

    if not pdf_path.exists():
        console.print(f"[red]Cannot find original PDF:[/red] {session.source_file}")
        console.print("[dim]Please ensure the PDF is in the catalogs/ directory[/dim]")
        raise typer.Exit(1)

    extractor = InteractiveExtractor(pdf_path, SESSIONS_DIR)
    session = extractor.run()

    display_extraction_summary(session)

    if session.products:
        export_to_csv(session, EXTRACTIONS_DIR)


@app.command()
def status() -> None:
    """View extraction status for all catalogs."""
    ensure_directories()
    display_status(SESSIONS_DIR, EXTRACTIONS_DIR)


@app.command()
def export(
    catalog_name: str = typer.Argument(
        ...,
        help="Name of the catalog to export (without extension)",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Custom output path for CSV file",
    ),
) -> None:
    """Export extracted data to CSV."""
    ensure_directories()

    session_path = SESSIONS_DIR / f"{catalog_name}.session.json"

    if not session_path.exists():
        console.print(f"[red]No session found for:[/red] {catalog_name}")
        raise typer.Exit(1)

    session = ExtractionSession.load(session_path)
    if not session:
        console.print(f"[red]Failed to load session:[/red] {session_path}")
        raise typer.Exit(1)

    if not session.products:
        console.print("[yellow]No products to export.[/yellow]")
        raise typer.Exit(0)

    if output:
        output_dir = output.parent
        filename = output.name
    else:
        output_dir = EXTRACTIONS_DIR
        filename = None

    export_to_csv(session, output_dir, filename)
    display_extraction_summary(session)


@app.command()
def view(
    pdf_path: Path = typer.Argument(
        ...,
        help="Path to the PDF catalog",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    page: int = typer.Option(
        1,
        "--page", "-p",
        help="Page number to view",
    ),
) -> None:
    """View a specific page of a PDF catalog."""
    from .pdf_reader import PDFReader

    with PDFReader(pdf_path) as reader:
        if page < 1 or page > reader.total_pages:
            console.print(f"[red]Invalid page number.[/red] PDF has {reader.total_pages} pages.")
            raise typer.Exit(1)

        page_content = reader.get_page(page)

        console.print(Panel(
            f"[bold]{pdf_path.name}[/bold] - Page {page}/{reader.total_pages}",
            border_style="blue",
        ))

        for line_num, line in page_content.get_numbered_lines():
            console.print(f"[dim]{line_num:4d}[/dim] | {line}")


@app.command()
def auto(
    pdf_path: Path = typer.Argument(
        ...,
        help="Path to the PDF catalog to auto-extract",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Auto-extract products from a PDF catalog."""
    ensure_directories()

    if not pdf_path.suffix.lower() == ".pdf":
        console.print(f"[red]Error:[/red] {pdf_path} is not a PDF file")
        raise typer.Exit(1)

    extractor = AutoExtractor(pdf_path, SESSIONS_DIR)
    session = extractor.run()

    display_extraction_summary(session)

    if session.products:
        export_to_csv(session, EXTRACTIONS_DIR)

    console.print(f"\n[cyan]Run 'extractor verify {pdf_path.stem}' to review and correct extractions[/cyan]")


@app.command()
def verify(
    catalog_name: str = typer.Argument(
        ...,
        help="Name of the catalog to verify (without extension)",
    ),
    page: int = typer.Option(
        1,
        "--page", "-p",
        help="Starting page number",
    ),
) -> None:
    """Verify and correct extracted data page-by-page."""
    ensure_directories()

    session_path = SESSIONS_DIR / f"{catalog_name}.session.json"

    if not session_path.exists():
        console.print(f"[red]No session found for:[/red] {catalog_name}")
        console.print("[dim]Run 'extractor auto <pdf>' first to extract data[/dim]")
        raise typer.Exit(1)

    session = ExtractionSession.load(session_path)
    if not session:
        console.print(f"[red]Failed to load session:[/red] {session_path}")
        raise typer.Exit(1)

    # Find the original PDF
    pdf_path = CATALOGS_DIR / session.source_file
    if not pdf_path.exists():
        pdf_path = BASE_DIR / session.source_file

    if not pdf_path.exists():
        console.print(f"[red]Cannot find original PDF:[/red] {session.source_file}")
        raise typer.Exit(1)

    verifier = Verifier(pdf_path, session)
    session = verifier.run(SESSIONS_DIR, start_page=page)

    display_extraction_summary(session)

    if session.products:
        export_to_csv(session, EXTRACTIONS_DIR)


@app.command("web-verify")
def web_verify(
    catalog_name: Optional[str] = typer.Argument(
        None,
        help="Name of the catalog to verify (without extension). If not provided, opens in dashboard mode.",
    ),
    port: int = typer.Option(
        5000,
        "--port", "-p",
        help="Port to run the web server on",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host to bind the server to",
    ),
) -> None:
    """Launch web-based verification UI in browser.

    If no catalog name is provided, opens in dashboard mode where you can
    upload PDFs, run extractions, and switch between catalogs.
    """
    ensure_directories()

    # Import web_verifier lazily to avoid Flask dependency for other commands
    try:
        from .web_verifier import run_server as run_web_verifier
    except ImportError as e:
        console.print(f"[red]Error:[/red] Flask is required for web verification: {e}")
        console.print("[dim]Install with: uv add flask pymupdf[/dim]")
        raise typer.Exit(1)

    # Dashboard mode - no catalog specified
    if catalog_name is None:
        console.print(Panel(
            "[bold]Web Verification UI - Dashboard Mode[/bold]\n\n"
            "Upload PDFs, run extractions, and manage catalogs.",
            border_style="blue"
        ))

        # Open browser after a short delay
        import webbrowser
        import threading

        def open_browser():
            webbrowser.open(f"http://{host}:{port}")

        threading.Timer(1.0, open_browser).start()

        # Run the web server in dashboard mode
        run_web_verifier(host=host, port=port, dashboard_mode=True)
        return

    # Catalog-specific mode
    session_path = SESSIONS_DIR / f"{catalog_name}.session.json"

    if not session_path.exists():
        console.print(f"[red]No session found for:[/red] {catalog_name}")
        console.print("[dim]Run 'extractor auto <pdf>' first to extract data[/dim]")
        raise typer.Exit(1)

    session = ExtractionSession.load(session_path)
    if not session:
        console.print(f"[red]Failed to load session:[/red] {session_path}")
        raise typer.Exit(1)

    # Find the original PDF
    pdf_path = CATALOGS_DIR / session.source_file
    if not pdf_path.exists():
        pdf_path = BASE_DIR / session.source_file

    if not pdf_path.exists():
        console.print(f"[red]Cannot find original PDF:[/red] {session.source_file}")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]Web Verification UI[/bold]\n\n"
        f"Catalog: {session.source_file}\n"
        f"Products: {len(session.products)}\n"
        f"Pages: {session.total_pages}",
        border_style="blue"
    ))

    # Open browser after a short delay to allow server to start
    import webbrowser
    import threading

    def open_browser():
        webbrowser.open(f"http://{host}:{port}")

    # Delay browser opening by 1 second to let server start
    threading.Timer(1.0, open_browser).start()

    # Run the web server
    run_web_verifier(pdf_path, session, SESSIONS_DIR, host=host, port=port)


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
