#!/usr/bin/env bash
# mine-corrections.sh — mine recurring user corrections from Claude Code
# session transcripts and turn them into candidate CLAUDE.md rules.
#
# This is a LOCAL-ONLY workflow. It reads your Claude Code transcripts from
# ~/.claude/projects, extracts your (human) turns with `coworker-extract`,
# keeps the ones that look like corrections, and asks the local coworker
# model (`ask-coworker`) to cluster the recurring ones into a short list of
# CLAUDE.md rules. Nothing leaves your machine: ask-coworker is localhost-only
# by default.
#
# Usage:
#   examples/mine-corrections.sh [N]            # mine last N sessions (default 50)
#   N=50 examples/mine-corrections.sh           # same, via env
#   examples/mine-corrections.sh 50 --no-model  # skip the model; just dump candidates
#
# Env overrides:
#   CLAUDE_PROJECTS_DIR   transcript dir (default: $HOME/.claude/projects)
#   COWORKER_*            forwarded to ask-coworker (backend, model, base url)
#
# Output:
#   mined-corrections.txt   the raw correction-like turns that were collected
#   mined-rules.md          the proposed CLAUDE.md rules (model output)
#
# Review mined-rules.md by hand, then paste the keepers into CLAUDE.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

N="${1:-${N:-50}}"
NO_MODEL=0
for arg in "$@"; do
  case "$arg" in
    --no-model) NO_MODEL=1 ;;
  esac
done

PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"
CANDIDATES="mined-corrections.txt"
RULES="mined-rules.md"

if ! command -v coworker-extract >/dev/null 2>&1; then
  echo "error: coworker-extract not on PATH. Install with: uv tool install --editable ." >&2
  exit 1
fi

if [[ ! -d "$PROJECTS_DIR" ]]; then
  echo "error: no transcript dir at $PROJECTS_DIR" >&2
  echo "       set CLAUDE_PROJECTS_DIR if your sessions live elsewhere." >&2
  exit 1
fi

# Correction-signal patterns: phrasing people use when steering Claude after a
# wrong turn. Case-insensitive, word-ish boundaries kept loose on purpose.
CORRECTION_RE='(\bno,|\bnope\b|\bdon.?t\b|\bdo not\b|\bstop\b|\bactually\b|\binstead\b|\bnot what\b|\bthat.?s wrong\b|\bincorrect\b|\brevert\b|\bundo\b|\bwhy did you\b|\bi (told|said|asked)\b|\byou (keep|always|again|still)\b|\bagain\b|\bnever\b|\balways\b|\bshould (be|have)\b|\bnot\b.*\binstead\b|\buse\b.*\bnot\b|\bplease (don.?t|stop)\b)'

echo "=== collecting last $N session transcripts from $PROJECTS_DIR ===" >&2
mapfile -t FILES < <(
  find "$PROJECTS_DIR" -type f -name '*.jsonl' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | head -n "$N" | cut -d' ' -f2-
)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "error: no .jsonl transcripts found under $PROJECTS_DIR" >&2
  exit 1
fi
echo "found ${#FILES[@]} transcript(s)" >&2

: > "$CANDIDATES"
for f in "${FILES[@]}"; do
  # Extract human turns, then keep only correction-looking lines.
  coworker-extract --input "$f" --role human --format text 2>/dev/null \
    | grep -iE "$CORRECTION_RE" >> "$CANDIDATES" || true
done

COUNT="$(wc -l < "$CANDIDATES" | tr -d ' ')"
echo "collected $COUNT candidate correction line(s) -> $CANDIDATES" >&2

if [[ "$COUNT" -eq 0 ]]; then
  echo "no correction-like turns found; nothing to mine." >&2
  exit 0
fi

if [[ "$NO_MODEL" -eq 1 ]]; then
  echo "--no-model set: skipping clustering. Review $CANDIDATES by hand." >&2
  exit 0
fi

echo "=== clustering recurring corrections with the local coworker model ===" >&2
PROMPT='These lines are corrections a user repeatedly gave an AI coding assistant
across many sessions. Identify the RECURRING themes (a theme must appear more
than once). For each recurring theme, output one concise, imperative CLAUDE.md
rule the assistant should follow to avoid the correction next time. Group under
short headings. Ignore one-off corrections. Output GitHub-flavored Markdown only,
no preamble.'

if ask-coworker \
    --question "$PROMPT" \
    --paths "$CANDIDATES" \
    --allow-outside-cwd \
    --max-tokens 2048 \
    > "$RULES" 2>/tmp/mine-corrections.err; then
  echo "proposed rules written to $RULES" >&2
  echo >&2
  echo "Next: review $RULES and paste the keepers into CLAUDE.md." >&2
else
  echo "ask-coworker failed (is a local backend running?):" >&2
  cat /tmp/mine-corrections.err >&2
  echo >&2
  echo "Candidates are still in $CANDIDATES; re-run with --no-model to skip the model." >&2
  exit 3
fi
