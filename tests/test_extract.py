"""Tests for coworker_extract CLI."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path


def run_extract(*args: str, stdin_text: str | None = None, input_file: Path | None = None):
    cmd = [sys.executable, "-m", "coworker.cli.coworker_extract"]
    if input_file is not None:
        cmd += ["--input", str(input_file)]
    cmd += list(args)
    result = subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    return result


def write_jsonl(path: Path, lines: list) -> None:
    path.write_text("\n".join(json.dumps(obj) for obj in lines) + "\n", encoding="utf-8")


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_happy_path_human_and_assistant(tmp_path):
    f = tmp_path / "transcript.jsonl"
    write_jsonl(f, [
        {"role": "human", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ])

    result = run_extract(input_file=f)

    assert result.returncode == 0
    assert "[human] hello" in result.stdout
    assert "[assistant] hi there" in result.stdout


def test_malformed_line_skipped(tmp_path):
    f = tmp_path / "transcript.jsonl"
    f.write_text(
        json.dumps({"role": "human", "content": "valid"}) + "\n"
        + "not valid json\n",
        encoding="utf-8",
    )

    result = run_extract(input_file=f)

    assert result.returncode == 0
    assert "[human] valid" in result.stdout
    assert "Warning" in result.stderr


def test_empty_file(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")

    result_text = run_extract(input_file=f)
    assert result_text.returncode == 0
    assert result_text.stdout.strip() == ""

    result_json = run_extract("--format", "json", input_file=f)
    assert result_json.returncode == 0
    assert json.loads(result_json.stdout) == []


def test_role_filter_human_only(tmp_path):
    f = tmp_path / "transcript.jsonl"
    write_jsonl(f, [
        {"role": "human", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ])

    result = run_extract("--role", "human", input_file=f)

    assert result.returncode == 0
    assert "[human] question" in result.stdout
    assert "assistant" not in result.stdout


def test_format_json(tmp_path):
    f = tmp_path / "transcript.jsonl"
    write_jsonl(f, [
        {"role": "human", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ])

    result = run_extract("--format", "json", input_file=f)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0] == {"role": "human", "content": "hello"}
    assert data[1] == {"role": "assistant", "content": "world"}


def test_stdin_input():
    payload = (
        json.dumps({"role": "human", "content": "from stdin"}) + "\n"
        + json.dumps({"role": "assistant", "content": "got it"}) + "\n"
    )

    result = run_extract("--input", "-", stdin_text=payload)

    assert result.returncode == 0
    assert "[human] from stdin" in result.stdout
    assert "[assistant] got it" in result.stdout


def test_nested_message_format(tmp_path):
    """Handles the line["message"]["role"] + line["message"]["content"] format."""
    f = tmp_path / "transcript.jsonl"
    write_jsonl(f, [
        {"type": "human", "message": {"role": "human", "content": "nested hello"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "nested reply"}},
    ])

    result = run_extract(input_file=f)

    assert result.returncode == 0
    assert "[human] nested hello" in result.stdout
    assert "[assistant] nested reply" in result.stdout


def test_content_blocks(tmp_path):
    """Handles content as a list of text blocks."""
    f = tmp_path / "transcript.jsonl"
    write_jsonl(f, [
        {"role": "assistant", "content": [{"type": "text", "text": "block content"}]},
    ])

    result = run_extract(input_file=f)

    assert result.returncode == 0
    assert "block content" in result.stdout


def test_missing_input_file():
    result = run_extract("--input", "/nonexistent/path/file.jsonl")
    assert result.returncode == 1
    assert "Error" in result.stderr
