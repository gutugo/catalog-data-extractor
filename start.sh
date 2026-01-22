#!/bin/bash
cd "$(dirname "$0")"
uv run extractor web-verify "$@"
