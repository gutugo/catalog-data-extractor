#!/bin/bash
# Start the web verification UI
# Uses port 5001 by default (port 5000 is used by macOS AirPlay Receiver)
cd "$(dirname "$0")"
uv run extractor web-verify --port 5001 "$@"
