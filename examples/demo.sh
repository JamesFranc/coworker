#!/usr/bin/env bash
# demo.sh — exercises ask-coworker and coworker-write with --dry-run
# No model needed: --dry-run exits before calling the backend.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== ask-coworker dry run ==="
ask-coworker \
  --question "What safety guards does this module implement?" \
  --paths src/coworker/safety.py \
  --dry-run

echo ""
echo "=== coworker-write dry run ==="
coworker-write \
  --spec "Write a Python function that returns the square of a number." \
  --target /tmp/coworker_demo_output.py \
  --allow-outside-cwd \
  --dry-run

echo ""
echo "=== coworker-extract (empty input) ==="
echo "" | coworker-extract --input - --format json

echo ""
echo "Demo complete."
