#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip

if command -v uv >/dev/null 2>&1; then
  uv pip install -e .
else
  pip install -e .
fi

echo "Prepared virtualenv and installed canaryfs. To use: source .venv/bin/activate"
