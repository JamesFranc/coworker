"""Shared pytest fixtures for coworker-tools tests.

The autouse fixture here sets COWORKER_USAGE_LOG=off for the entire test
suite, preventing any test run from writing to the real
~/.local/state/coworker/usage.jsonl file.  Individual tests that want to
assert logging behaviour opt back in by monkeypatching
COWORKER_USAGE_LOG to a tmp_path value.
"""

import pytest


@pytest.fixture(autouse=True)
def disable_usage_log(monkeypatch):
    """Disable usage logging for every test unless explicitly overridden."""
    monkeypatch.setenv("COWORKER_USAGE_LOG", "off")
