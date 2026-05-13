"""Tests for coworker.backend — no real network or model calls."""

import socket
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from coworker.backend import BackendError, resolve_endpoint, run_worker


# ---------------------------------------------------------------------------
# 1. Ollama happy path
# ---------------------------------------------------------------------------


def test_ollama_happy_path(monkeypatch):
    fake_message = MagicMock()
    fake_message.content = "Hello from ollama"

    fake_choice = MagicMock()
    fake_choice.message = fake_message

    fake_response = MagicMock()
    fake_response.choices = [fake_choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", lambda *a, **kw: [
        (None, None, None, None, ("127.0.0.1", 0))
    ])

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = run_worker(
            system="You are helpful.",
            user_messages=["Say hi"],
            backend="ollama",
            base_url="http://localhost:11434/v1",
            model="test-model",
        )

    assert result == "Hello from ollama"
    fake_client.chat.completions.create.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Ollama localhost allowed
# ---------------------------------------------------------------------------


def test_resolve_endpoint_localhost_allowed():
    # localhost is explicitly whitelisted by hostname check — no getaddrinfo needed
    url = "http://localhost:11434/v1"
    result = resolve_endpoint(url)
    assert result == url


# ---------------------------------------------------------------------------
# 3. Remote endpoint refused
# ---------------------------------------------------------------------------


def test_resolve_endpoint_remote_refused(monkeypatch):
    monkeypatch.setattr(
        "coworker.backend.socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    with pytest.raises(BackendError) as exc_info:
        resolve_endpoint("http://example.com/v1", allow_remote=False)

    assert exc_info.value.exit_code == 3
    assert "Remote endpoint refused" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Remote endpoint with allow_remote=True
# ---------------------------------------------------------------------------


def test_resolve_endpoint_remote_allowed(monkeypatch):
    monkeypatch.setattr(
        "coworker.backend.socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    url = "http://example.com/v1"
    result = resolve_endpoint(url, allow_remote=True)
    assert result == url


# ---------------------------------------------------------------------------
# 5. MLX happy path on Darwin/arm64
# ---------------------------------------------------------------------------


def test_mlx_happy_path(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")

    fake_mlx_model = MagicMock()
    fake_tokenizer = MagicMock()

    fake_mlx_lm = types.ModuleType("mlx_lm")
    fake_mlx_lm.load = MagicMock(return_value=(fake_mlx_model, fake_tokenizer))
    fake_mlx_lm.generate = MagicMock(return_value="Generated MLX response")

    with patch.dict(sys.modules, {"mlx_lm": fake_mlx_lm}):
        result = run_worker(
            system="You are helpful.",
            user_messages=["Hello"],
            backend="mlx",
            model="test-mlx-model",
        )

    assert result == "Generated MLX response"
    fake_mlx_lm.load.assert_called_once_with("test-mlx-model")
    fake_mlx_lm.generate.assert_called_once()


# ---------------------------------------------------------------------------
# 6. MLX refused on non-arm64
# ---------------------------------------------------------------------------


def test_mlx_refused_non_arm64(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    with pytest.raises(BackendError) as exc_info:
        run_worker(system="s", user_messages=["m"], backend="mlx")

    assert exc_info.value.exit_code == 3
    assert "Darwin/arm64" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 7. Unknown backend
# ---------------------------------------------------------------------------


def test_unknown_backend():
    with pytest.raises(BackendError) as exc_info:
        run_worker(system="s", user_messages=["m"], backend="bogus")

    assert exc_info.value.exit_code == 1
    assert "bogus" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8. DNS failure fails closed (allow_remote=False)
# ---------------------------------------------------------------------------


def test_resolve_endpoint_dns_failure_fails_closed(monkeypatch):
    def raise_gaierror(*a, **kw):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", raise_gaierror)
    with pytest.raises(BackendError) as exc_info:
        resolve_endpoint("http://unresolvable.invalid/v1", allow_remote=False)

    assert exc_info.value.exit_code == 3
    assert "Could not resolve" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 9. DNS failure with allow_remote=True does NOT raise
# ---------------------------------------------------------------------------


def test_resolve_endpoint_dns_failure_allow_remote(monkeypatch):
    def raise_gaierror(*a, **kw):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr("coworker.backend.socket.getaddrinfo", raise_gaierror)
    url = "http://unresolvable.invalid/v1"
    result = resolve_endpoint(url, allow_remote=True)
    assert result == url
