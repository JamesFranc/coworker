"""Tests for coworker.usage_log and token-usage recording in run_worker."""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coworker.usage_log import append_record, resolve_log_path


# ---------------------------------------------------------------------------
# resolve_log_path
# ---------------------------------------------------------------------------


def test_resolve_log_path_default(monkeypatch, tmp_path):
    """Default path uses XDG_STATE_HOME when set."""
    monkeypatch.delenv("COWORKER_USAGE_LOG", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = resolve_log_path()
    assert path == tmp_path / "state" / "coworker" / "usage.jsonl"


def test_resolve_log_path_fallback_home(monkeypatch, tmp_path):
    """When XDG_STATE_HOME is unset, falls back to ~/.local/state."""
    monkeypatch.delenv("COWORKER_USAGE_LOG", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path = resolve_log_path()
    assert path is not None
    assert path.name == "usage.jsonl"
    assert "coworker" in path.parts


def test_resolve_log_path_custom(monkeypatch, tmp_path):
    """COWORKER_USAGE_LOG with a real path is returned as-is."""
    target = tmp_path / "my_usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(target))
    assert resolve_log_path() == target


@pytest.mark.parametrize("disable_val", ["", "off", "0", "false", "OFF", "False"])
def test_resolve_log_path_disabled(monkeypatch, disable_val):
    """Sentinel values cause resolve_log_path to return None."""
    monkeypatch.setenv("COWORKER_USAGE_LOG", disable_val)
    assert resolve_log_path() is None


# ---------------------------------------------------------------------------
# append_record
# ---------------------------------------------------------------------------


def test_append_record_writes_jsonl(monkeypatch, tmp_path):
    """append_record writes a valid JSONL line."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))

    record = {"ts": "2026-01-01T00:00:00+00:00", "command": "test", "x": 42}
    append_record(record)

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == record


def test_append_record_appends_multiple(monkeypatch, tmp_path):
    """Multiple calls each add one line."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))

    append_record({"n": 1})
    append_record({"n": 2})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"n": 1}
    assert json.loads(lines[1]) == {"n": 2}


def test_append_record_creates_parents(monkeypatch, tmp_path):
    """append_record creates missing parent directories."""
    log_path = tmp_path / "a" / "b" / "c" / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))
    append_record({"x": 1})
    assert log_path.exists()


def test_append_record_disabled_writes_nothing(monkeypatch, tmp_path):
    """When logging is disabled, no file is created."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", "off")
    append_record({"x": 1})
    assert not log_path.exists()


def test_append_record_unwritable_path_does_not_raise(monkeypatch):
    """append_record swallows write errors and never raises."""
    monkeypatch.setenv("COWORKER_USAGE_LOG", "/nonexistent-dir/usage.jsonl")
    # Must not raise:
    append_record({"x": 1})


def test_append_record_unwritable_prints_to_stderr(monkeypatch, capsys):
    """append_record prints exactly one warning line to stderr on failure."""
    monkeypatch.setenv("COWORKER_USAGE_LOG", "/nonexistent-dir/usage.jsonl")
    append_record({"x": 1})
    captured = capsys.readouterr()
    assert "[coworker] usage log skipped:" in captured.err
    # Only one line of warning
    assert captured.err.count("[coworker] usage log skipped:") == 1


# ---------------------------------------------------------------------------
# run_worker — record shape with mocked openai client
# ---------------------------------------------------------------------------


def _make_fake_openai(content: str, prompt_tokens: int, completion_tokens: int, total_tokens: int):
    """Build a fake openai module whose client returns the given values."""
    fake_usage = MagicMock()
    fake_usage.prompt_tokens = prompt_tokens
    fake_usage.completion_tokens = completion_tokens
    fake_usage.total_tokens = total_tokens

    fake_message = MagicMock()
    fake_message.content = content

    fake_choice = MagicMock()
    fake_choice.message = fake_message

    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = fake_usage

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    return fake_openai


def test_run_worker_record_shape(monkeypatch, tmp_path):
    """run_worker writes exactly one well-formed JSONL record with correct fields."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))
    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", lambda *a, **kw: [
        (None, None, None, None, ("127.0.0.1", 0))
    ])

    fake_openai = _make_fake_openai("hello", prompt_tokens=10, completion_tokens=5, total_tokens=15)

    with patch.dict(sys.modules, {"openai": fake_openai}):
        from coworker.backend import run_worker
        result = run_worker(
            system="You are helpful.",
            user_messages=["Say hi"],
            backend="llamacpp",
            base_url="http://localhost:8080/v1",
            model="test-model",
            usage_context={"command": "ask-coworker", "num_files": 2, "input_bytes": 512},
        )

    assert result == "hello"

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])

    # All required fields present
    required_fields = {
        "ts", "command", "backend", "model", "num_files", "input_bytes",
        "prompt_tokens", "completion_tokens", "total_tokens", "max_tokens", "token_source",
    }
    assert required_fields.issubset(rec.keys())

    # Token counts match what the fake usage object returned
    assert rec["prompt_tokens"] == 10
    assert rec["completion_tokens"] == 5
    assert rec["total_tokens"] == 15
    assert rec["token_source"] == "api"

    # Context fields
    assert rec["command"] == "ask-coworker"
    assert rec["num_files"] == 2
    assert rec["input_bytes"] == 512
    assert rec["backend"] == "llamacpp"
    assert rec["model"] == "test-model"
    assert rec["max_tokens"] == 4096  # default

    # ts is a valid ISO8601 UTC timestamp
    ts = datetime.fromisoformat(rec["ts"])
    assert ts.tzinfo is not None


def test_run_worker_null_usage_when_api_returns_none(monkeypatch, tmp_path):
    """When response.usage is None, token fields are null and token_source is 'unavailable'."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))
    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", lambda *a, **kw: [
        (None, None, None, None, ("127.0.0.1", 0))
    ])

    fake_message = MagicMock()
    fake_message.content = "hi"
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = None
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict(sys.modules, {"openai": fake_openai}):
        from coworker.backend import run_worker
        result = run_worker(
            system="s",
            user_messages=["m"],
            backend="llamacpp",
            base_url="http://localhost:8080/v1",
            model="m",
            usage_context={"command": "test"},
        )

    assert result == "hi"
    rec = json.loads(log_path.read_text().strip())
    assert rec["prompt_tokens"] is None
    assert rec["completion_tokens"] is None
    assert rec["total_tokens"] is None
    assert rec["token_source"] == "unavailable"


def test_run_worker_no_context_writes_nothing(monkeypatch, tmp_path):
    """run_worker without usage_context writes NO record (preserves old behaviour)."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))
    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", lambda *a, **kw: [
        (None, None, None, None, ("127.0.0.1", 0))
    ])

    fake_openai = _make_fake_openai("hi", 1, 1, 2)

    with patch.dict(sys.modules, {"openai": fake_openai}):
        from coworker.backend import run_worker
        run_worker(
            system="s",
            user_messages=["m"],
            backend="llamacpp",
            base_url="http://localhost:8080/v1",
            model="m",
        )

    assert not log_path.exists()


def test_run_worker_mlx_fallback_on_bad_tokenizer(monkeypatch, tmp_path):
    """MLX path: if tokenizer.encode raises, token fields are null, token_source is 'mlx_unavailable'."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")

    fake_mlx_model = MagicMock()
    fake_tokenizer = MagicMock()
    # Make encode() raise so the fallback path is triggered
    fake_tokenizer.encode = MagicMock(side_effect=AttributeError("no encode on this tokenizer"))

    fake_mlx_lm = types.ModuleType("mlx_lm")
    fake_mlx_lm.load = MagicMock(return_value=(fake_mlx_model, fake_tokenizer))
    fake_mlx_lm.generate = MagicMock(return_value="mlx output")

    with patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}):
        from coworker.backend import run_worker
        result = run_worker(
            system="s",
            user_messages=["m"],
            backend="mlx",
            model="mlx-model",
            usage_context={"command": "ask-coworker", "num_files": 0, "input_bytes": 10},
        )

    assert result == "mlx output"
    rec = json.loads(log_path.read_text().strip())
    assert rec["prompt_tokens"] is None
    assert rec["completion_tokens"] is None
    assert rec["total_tokens"] is None
    assert rec["token_source"] == "mlx_unavailable"


def test_run_worker_mlx_estimated_tokens(monkeypatch, tmp_path):
    """MLX path with working tokenizer: token_source is 'mlx_estimated' and counts are ints."""
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("COWORKER_USAGE_LOG", str(log_path))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")

    fake_mlx_model = MagicMock()
    fake_tokenizer = MagicMock()
    # Make encode() return a real list so len() works
    fake_tokenizer.encode = MagicMock(side_effect=lambda text: list(range(len(text.split()))))

    fake_mlx_lm = types.ModuleType("mlx_lm")
    fake_mlx_lm.load = MagicMock(return_value=(fake_mlx_model, fake_tokenizer))
    fake_mlx_lm.generate = MagicMock(return_value="output text")

    with patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}):
        from coworker.backend import run_worker
        result = run_worker(
            system="be helpful",
            user_messages=["hello world"],
            backend="mlx",
            model="mlx-model",
            usage_context={"command": "ask-coworker", "num_files": 0, "input_bytes": 20},
        )

    assert result == "output text"
    rec = json.loads(log_path.read_text().strip())
    assert rec["token_source"] == "mlx_estimated"
    assert isinstance(rec["prompt_tokens"], int)
    assert isinstance(rec["completion_tokens"], int)
    assert rec["total_tokens"] == rec["prompt_tokens"] + rec["completion_tokens"]


# ---------------------------------------------------------------------------
# Disable sentinels via COWORKER_USAGE_LOG
# ---------------------------------------------------------------------------


def test_empty_string_disables_logging(monkeypatch, tmp_path):
    """COWORKER_USAGE_LOG="" disables logging — no file written."""
    monkeypatch.setenv("COWORKER_USAGE_LOG", "")
    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", lambda *a, **kw: [
        (None, None, None, None, ("127.0.0.1", 0))
    ])
    fake_openai = _make_fake_openai("hi", 1, 1, 2)
    log_path = tmp_path / "usage.jsonl"

    with patch.dict(sys.modules, {"openai": fake_openai}):
        from coworker.backend import run_worker
        run_worker(
            system="s",
            user_messages=["m"],
            backend="llamacpp",
            base_url="http://localhost:8080/v1",
            model="m",
            usage_context={"command": "test"},
        )

    assert not log_path.exists()


def test_log_failure_swallowed_result_unchanged(monkeypatch, tmp_path):
    """Unwritable log path: run_worker still returns content, no exception raised."""
    monkeypatch.setenv("COWORKER_USAGE_LOG", "/nonexistent-dir/usage.jsonl")
    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", lambda *a, **kw: [
        (None, None, None, None, ("127.0.0.1", 0))
    ])
    fake_openai = _make_fake_openai("answer", 5, 5, 10)

    with patch.dict(sys.modules, {"openai": fake_openai}):
        from coworker.backend import run_worker
        result = run_worker(
            system="s",
            user_messages=["m"],
            backend="llamacpp",
            base_url="http://localhost:8080/v1",
            model="m",
            usage_context={"command": "test"},
        )

    assert result == "answer"


# ---------------------------------------------------------------------------
# coworker-extract local-only record
# ---------------------------------------------------------------------------


def test_extract_writes_local_only_record(monkeypatch, tmp_path):
    """coworker-extract appends a local-only record with null token fields."""
    import subprocess

    log_path = tmp_path / "usage.jsonl"
    input_file = tmp_path / "transcript.jsonl"
    import json as _json

    input_file.write_text(
        _json.dumps({"role": "human", "content": "hello"}) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "coworker.cli.coworker_extract",
         "--input", str(input_file)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "COWORKER_USAGE_LOG": str(log_path)},
    )

    assert result.returncode == 0
    assert log_path.exists()

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    rec = _json.loads(lines[0])

    assert rec["command"] == "coworker-extract"
    assert rec["backend"] is None
    assert rec["model"] is None
    assert rec["prompt_tokens"] is None
    assert rec["completion_tokens"] is None
    assert rec["total_tokens"] is None
    assert rec["token_source"] == "none"
    assert rec["num_files"] == 1
    assert isinstance(rec["input_bytes"], int) and rec["input_bytes"] > 0
    # ts parses as ISO8601
    ts = datetime.fromisoformat(rec["ts"])
    assert ts.tzinfo is not None


def test_extract_stdin_num_files_zero(monkeypatch, tmp_path):
    """coworker-extract from stdin: num_files=0 in the record."""
    import subprocess, os, json as _json

    log_path = tmp_path / "usage.jsonl"
    payload = _json.dumps({"role": "human", "content": "from stdin"}) + "\n"

    result = subprocess.run(
        [sys.executable, "-m", "coworker.cli.coworker_extract", "--input", "-"],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, "COWORKER_USAGE_LOG": str(log_path)},
    )

    assert result.returncode == 0
    rec = _json.loads(log_path.read_text().strip())
    assert rec["num_files"] == 0


# ---------------------------------------------------------------------------
# ask-coworker --dry-run writes no record (subprocess)
# ---------------------------------------------------------------------------


def test_extract_help_writes_no_record(tmp_path):
    """coworker-extract --help exits via argparse before _process runs — no log written."""
    import subprocess, os

    log_path = tmp_path / "usage.jsonl"

    result = subprocess.run(
        [sys.executable, "-m", "coworker.cli.coworker_extract", "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "COWORKER_USAGE_LOG": str(log_path)},
    )

    assert result.returncode == 0
    assert not log_path.exists()


def test_ask_coworker_dry_run_no_log(monkeypatch, tmp_path):
    """ask-coworker --dry-run exits before run_worker → no usage record."""
    import subprocess, os

    log_path = tmp_path / "usage.jsonl"

    result = subprocess.run(
        [
            sys.executable, "-m", "coworker.cli.ask_coworker",
            "--question", "Q",
            "--paths", "src/coworker/safety.py",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
        env={**os.environ, "COWORKER_USAGE_LOG": str(log_path)},
    )

    assert result.returncode == 0
    assert not log_path.exists()


def test_write_dry_run_no_log(tmp_path):
    """coworker-write --dry-run exits before run_worker → no usage record."""
    import subprocess, os

    log_path = tmp_path / "usage.jsonl"
    target = tmp_path / "out.txt"

    result = subprocess.run(
        [
            sys.executable, "-m", "coworker.cli.coworker_write",
            "--spec", "x",
            "--target", str(target),
            "--allow-outside-cwd",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "COWORKER_USAGE_LOG": str(log_path)},
    )

    assert result.returncode == 0
    assert not log_path.exists()
