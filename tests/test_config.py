"""Tests for coworker.config — XDG config round-trip."""

from pathlib import Path

import coworker.config as config


def test_missing_file_returns_empty(monkeypatch, tmp_path):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    assert config.read_config() == {}


def test_round_trip(monkeypatch, tmp_path):
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    config.write_config({"model": "some-model-id", "backend": "llamacpp"})
    result = config.read_config()
    assert result["model"] == "some-model-id"
    assert result["backend"] == "llamacpp"
    assert cfg_path.exists()
    assert cfg_path.parent.name == "coworker"


def test_write_creates_parent_dirs(monkeypatch, tmp_path):
    cfg_path = tmp_path / "a" / "b" / "c" / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    config.write_config({"backend": "llamacpp"})
    assert cfg_path.exists()


def test_write_only_known_keys(monkeypatch, tmp_path):
    """write_config only serialises 'backend' and 'model'; extra keys are ignored."""
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    config.write_config({"model": "m", "backend": "llamacpp", "extra": "ignored"})
    result = config.read_config()
    assert "extra" not in result
    assert result["model"] == "m"


def test_config_path_constant_type():
    assert isinstance(config.CONFIG_PATH, Path)
