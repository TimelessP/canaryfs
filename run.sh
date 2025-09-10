#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p canary

if ! command -v canaryfs >/dev/null 2>&1; then
  echo "canaryfs not found in PATH. Did you run prepare.sh and activate .venv?" >&2
  echo "Try: source .venv/bin/activate" >&2
  exit 1
fi

canaryfs --mount ./canary --ask -v
