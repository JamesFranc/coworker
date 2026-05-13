#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
source .venv/bin/activate
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    pip install -e ".[mlx,dev]"
else
    pip install -e ".[dev]"
fi
echo "Done. Activate with: source .venv/bin/activate"
