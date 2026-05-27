"""Tests for llamacpp model resolution precedence:
   --model flag > COWORKER_MODEL env > config file > registry default.

Config stores model_id strings directly; no label-to-id translation in the backend.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

import coworker.config as _config
from coworker.backend import run_worker
from coworker.models import DEFAULT_MODEL, get_model_by_label


@pytest.fixture
def captured_model(monkeypatch, tmp_path):
    """Run the llamacpp branch with mocked openai/socket and capture the
    model string that the backend resolved to."""
    cfg_path = tmp_path / "coworker" / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("COWORKER_MODEL", raising=False)
    monkeypatch.delenv("COWORKER_BACKEND", raising=False)
    monkeypatch.setattr(
        "coworker.backend.socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 0))],
    )

    fake_message = MagicMock()
    fake_message.content = "ok"
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    def call(**kwargs):
        with patch.dict(sys.modules, {"openai": fake_openai}):
            run_worker(system="s", user_messages=["m"], backend="llamacpp", **kwargs)
        return fake_client.chat.completions.create.call_args.kwargs["model"]

    return call


def test_default_when_nothing_set(captured_model):
    """No env, no config → falls back to DEFAULT_MODEL.model_id."""
    assert captured_model() == DEFAULT_MODEL.model_id


def test_config_overrides_default(captured_model):
    """Config model_id wins over registry default."""
    qwopus = get_model_by_label("Qwopus3.5-9B-Coder")
    assert qwopus is not None
    _config.write_config({"model": qwopus.model_id, "backend": "llamacpp"})
    assert captured_model() == qwopus.model_id


def test_env_overrides_config(captured_model, monkeypatch):
    """COWORKER_MODEL env var beats config file."""
    _config.write_config({"model": "config-model-id"})
    monkeypatch.setenv("COWORKER_MODEL", "env-model")
    assert captured_model() == "env-model"


def test_flag_overrides_env(captured_model, monkeypatch):
    """Explicit model= arg beats env var."""
    monkeypatch.setenv("COWORKER_MODEL", "env-model")
    assert captured_model(model="flag-model") == "flag-model"


def test_empty_config_uses_default_model(captured_model):
    """Config file with no model key → falls back to DEFAULT_MODEL.model_id."""
    _config.write_config({"backend": "llamacpp"})  # no model key
    assert captured_model() == DEFAULT_MODEL.model_id
