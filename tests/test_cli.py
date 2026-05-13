"""Subprocess-based tests for the CLI entry points."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def _run(*args: str, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# ask-coworker --dry-run
# ---------------------------------------------------------------------------


def test_ask_coworker_dry_run_exits_zero():
    result = _run(
        "-m", "coworker.cli.ask_coworker",
        "--question", "Q",
        "--paths", "src/coworker/safety.py",
        "--dry-run",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "DRY RUN" in result.stderr


def test_ask_coworker_cwd_escape_exits_2():
    result = _run(
        "-m", "coworker.cli.ask_coworker",
        "--question", "Q",
        "--paths", "../outside_file",
        "--dry-run",
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# coworker-write --dry-run
# ---------------------------------------------------------------------------


def test_coworker_write_dry_run_exits_zero(tmp_path: Path):
    target = tmp_path / "test_output.py"
    result = _run(
        "-m", "coworker.cli.coworker_write",
        "--spec", "hello",
        "--target", str(target),
        "--allow-outside-cwd",
        "--dry-run",
    )
    assert result.returncode == 0
    assert "DRY RUN" in result.stderr
    assert not target.exists()
