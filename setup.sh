#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
source .venv/bin/activate
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    pip install -e ".[mlx,dev]"
else
    pip install -e ".[dev]"
fi

# --- Model selection --------------------------------------------------------
# Labels must match src/coworker/models.py.
declare -a LABELS=("Gemma 4 E4B" "Gemma 4 E2B" "Qwopus3.5-9B-Coder")

if [ -t 0 ]; then
    echo
    echo "Select a worker model:"
    echo "  1) Gemma 4 E4B  (default, ~5.1 GB)"
    echo "  2) Gemma 4 E2B  (~3.2 GB)"
    echo "  3) Qwopus3.5-9B-Coder"
    read -r -p "Choice [1]: " choice
    choice="${choice:-1}"
else
    choice=1
fi

case "$choice" in
    1) idx=0 ;;
    2) idx=1 ;;
    3) idx=2 ;;
    *) echo "Invalid choice, using default (Gemma 4 E4B)."; idx=0 ;;
esac

label="${LABELS[$idx]}"

# Ensure hf is available so coworker-model can offer a download.
if ! command -v hf >/dev/null 2>&1; then
    pip install huggingface_hub
fi

# Persist the selection. coworker-model --set owns the download prompt
# (interactive: prompt, default no; non-TTY: print the manual command).
coworker-model --set "$label" || python -m coworker.cli.coworker_model --set "$label"

echo "Done. Activate with: source .venv/bin/activate"
echo "Or install as a tool:  uv tool install --editable ."
