"""Tests for the coworker-model CLI."""

import coworker.cli.coworker_model as cli
import coworker.config as _config
from coworker.models import DEFAULT_MODEL, get_model_by_label


def test_list_marks_default_when_no_config(monkeypatch, tmp_path, capsys):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    cli.main(["--list"])
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 3
    # DEFAULT_MODEL should be marked active
    active = [ln for ln in lines if ln.startswith("*")]
    assert len(active) == 1
    assert DEFAULT_MODEL.label in active[0]


def test_list_marks_configured_model(monkeypatch, tmp_path, capsys):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    qwopus = get_model_by_label("Qwopus3.5-9B-Coder")
    assert qwopus is not None
    _config.write_config({"model": qwopus.model_id, "backend": "llamacpp"})
    cli.main(["--list"])
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    active = [ln for ln in lines if ln.startswith("*")]
    assert len(active) == 1
    assert qwopus.label in active[0]


def test_set_writes_model_id_to_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    # non-TTY: download prompt is skipped and exit(0) is called
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with __import__("pytest").raises(SystemExit) as exc:
        cli.main(["--set", "Qwopus3.5-9B-Coder"])
    assert exc.value.code == 0

    result = _config.read_config()
    assert result["model"] == "Qwopus3.5-9B-Coder-MTP-GGUF.Q5_K_M"
    assert result["backend"] == "llamacpp"


def test_set_case_insensitive(monkeypatch, tmp_path):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with __import__("pytest").raises(SystemExit) as exc:
        cli.main(["--set", "qwopus3.5-9b-coder"])
    assert exc.value.code == 0

    result = _config.read_config()
    assert result["model"] == "Qwopus3.5-9B-Coder-MTP-GGUF.Q5_K_M"


def test_set_unknown_label_exits_nonzero(monkeypatch, tmp_path):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)

    with __import__("pytest").raises(SystemExit) as exc:
        cli.main(["--set", "bogus-does-not-exist"])
    assert exc.value.code == 1


def test_set_non_tty_prints_download_command(monkeypatch, tmp_path, capsys):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with __import__("pytest").raises(SystemExit) as exc:
        cli.main(["--set", "Qwopus3.5-9B-Coder"])
    assert exc.value.code == 0

    qwopus = get_model_by_label("Qwopus3.5-9B-Coder")
    assert qwopus is not None
    # If the GGUF is absent (expected in CI), the download command is printed.
    if not qwopus.local_path.exists():
        out = capsys.readouterr().out
        assert "huggingface-cli download" in out
