#!/usr/bin/env bash
# One-click launcher for Quanti Web Console (Linux/macOS)
set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
exec "$PYTHON_BIN" start_web.py "$@"
