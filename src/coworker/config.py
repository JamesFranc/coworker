"""XDG config read/write for coworker-tools.

Stores the active model and backend in
``$XDG_CONFIG_HOME/coworker/config.toml`` (defaulting to ``~/.config``).
Uses stdlib ``tomllib`` (Python 3.11+) with a fallback to the ``tomli``
package for Python 3.10.  Writing uses hand-rolled TOML so no additional
runtime dependency is required.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _tomllib
except ImportError:  # Python 3.10
    try:
        import tomli as _tomllib  # type: ignore[no-redef]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "tomllib is not available (Python < 3.11) and tomli is not installed. "
            "Install it with: pip install tomli"
        ) from exc

CONFIG_PATH: Path = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "coworker"
    / "config.toml"
)


def read_config() -> dict:
    """Return the parsed config dict, or ``{}`` if the file does not exist."""
    try:
        with open(CONFIG_PATH, "rb") as fh:
            return _tomllib.load(fh)
    except FileNotFoundError:
        return {}


def write_config(data: dict) -> None:
    """Write *data* as minimal TOML, creating the config directory if needed."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key in ("backend", "model"):
        if key in data:
            value = str(data[key]).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{value}"\n')
    CONFIG_PATH.write_text("".join(lines), encoding="utf-8")
