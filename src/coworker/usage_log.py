"""Token-usage logging for coworker-tools.

Writes one JSONL record per real model invocation to
$XDG_STATE_HOME/coworker/usage.jsonl (default) or a path set via
COWORKER_USAGE_LOG.  Set COWORKER_USAGE_LOG to "", "off", "0", or "false"
to disable logging entirely.

Design invariants
-----------------
- resolve_log_path() reads os.environ at call time (not at import time) so
  tests can monkeypatch COWORKER_USAGE_LOG without patching module-level state.
- append_record() never raises and never alters stdout / exit code.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def resolve_log_path() -> Path | None:
    """Return the resolved log path, or None to disable logging.

    Env var COWORKER_USAGE_LOG controls the path:
      - Not set: use XDG_STATE_HOME/coworker/usage.jsonl (fallback ~/.local/state)
      - Set to "", "off", "0", or "false": disable → return None
      - Set to any other string: use that path
    """
    env_val = os.environ.get("COWORKER_USAGE_LOG")

    if env_val is not None:
        if env_val.strip().lower() in ("", "off", "0", "false"):
            return None
        return Path(env_val)

    state_home = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(state_home) / "coworker" / "usage.jsonl"


def append_record(record: dict) -> None:
    """Append *record* as a single JSONL line to the usage log.

    Silently no-ops if logging is disabled (resolve_log_path() returns None).
    Any failure is printed as a single stderr line and then swallowed —
    this function never raises.
    """
    try:
        path = resolve_log_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"[coworker] usage log skipped: {exc}", file=sys.stderr)
