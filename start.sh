#!/bin/bash
# Start the web verification UI on mini01 server
# Binds to 0.0.0.0:5001 so it's accessible from other machines
cd "$(dirname "$0")"
uv run extractor web-verify "$@"
