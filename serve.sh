#!/usr/bin/env bash
set -euo pipefail

DOC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${DOC_ROOT}/.venv/bin/python"
SPHINX_BIN="${DOC_ROOT}/.venv/bin/sphinx-build"

if [[ ! -x "${SPHINX_BIN}" ]]; then
  echo "缺少 .venv。先执行：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

"${SPHINX_BIN}" -M html "${DOC_ROOT}/source" "${DOC_ROOT}/build"
exec "${PYTHON_BIN}" -m http.server 8000 --directory "${DOC_ROOT}/build/html"

