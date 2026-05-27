"""List available worker models and select the active one.

Selection is persisted to the XDG config file and consumed by the llamacpp
backend. Optionally downloads the GGUF for the selected model.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from coworker.config import read_config, write_config
from coworker.models import DEFAULT_MODEL, REGISTRY, get_model_by_label


def _active_model_id() -> str:
    """Return the currently configured model_id, falling back to DEFAULT_MODEL."""
    return read_config().get("model") or DEFAULT_MODEL.model_id


def _list_models() -> None:
    active = _active_model_id()
    found_active = False
    for entry in REGISTRY:
        if not found_active and entry.model_id == active:
            marker = "*"
            found_active = True
        else:
            marker = " "
        print(f"{marker} {entry.label}\t{entry.model_id}")


def _download_command(entry) -> str:  # entry: ModelEntry
    local_dir = str(Path.home() / "models")
    return f"huggingface-cli download {entry.hf_repo} {entry.gguf_filename} --local-dir {local_dir}"


def _set_model(label: str) -> int:
    entry = get_model_by_label(label)
    if entry is None:
        known = ", ".join(e.label for e in REGISTRY)
        print(f"error: unknown model label {label!r}. Known: {known}", file=sys.stderr)
        return 1

    if entry.local_path.exists():
        write_config({"model": entry.model_id, "backend": "llamacpp"})
        print(f"Active model set to: {entry.label}")
        return 0

    # GGUF not present locally
    print(f"Model file not found: {entry.gguf_filename}")
    print("Note: GGUF files are several GB in size.")

    cmd = _download_command(entry)

    if not sys.stdin.isatty():
        print(f"To download, run:\n  {cmd}")
    else:
        answer = input("Download now? [y/N]: ").strip().lower()
        if answer == "y":
            subprocess.run(
                [
                    "huggingface-cli",
                    "download",
                    entry.hf_repo,
                    entry.gguf_filename,
                    "--local-dir",
                    str(Path.home() / "models"),
                ],
                check=True,
            )
        else:
            print(f"To download, run:\n  {cmd}")

    write_config({"model": entry.model_id, "backend": "llamacpp"})
    print(f"Active model set to: {entry.label}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="coworker-model",
        description="List available worker models and select the active one.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        action="store_true",
        help="List models; mark the active one with *.",
    )
    group.add_argument(
        "--set",
        metavar="LABEL",
        help="Set the active model by label (case-insensitive).",
    )
    args = parser.parse_args(argv)

    if args.list:
        _list_models()
    else:
        sys.exit(_set_model(args.set))


if __name__ == "__main__":
    main()
