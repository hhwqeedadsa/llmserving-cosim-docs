#!/usr/bin/env bash
# Read-only dependency checks; this script never fetches or changes a checkout.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf 'ERROR: Python 3 not found: %s\n' "$PYTHON_BIN" >&2
    exit 2
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/check_deps.py" "$@"
