#!/bin/bash
# Start the catalog web verifier
# Can run without arguments to start in dashboard mode (upload PDFs from browser)
# Or pass a catalog name to open directly: ./start-web.sh my-catalog

cd "$(dirname "$0")"

if [ -n "$1" ]; then
    # Catalog name provided - open that catalog
    uv run extractor web-verify "$1"
else
    # No argument - start in dashboard mode
    uv run extractor web-verify
fi
