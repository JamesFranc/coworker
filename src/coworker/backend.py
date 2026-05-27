"""
Backend abstraction for coworker-tools.
Selected by COWORKER_BACKEND env var or --backend flag ("ollama" | "llamacpp" | "mlx").
"""

import os
import platform
import socket
import sys


class BackendError(Exception):
    def __init__(self, message: str, exit_code: int = 3):
        self.exit_code = exit_code
        super().__init__(message)


def resolve_endpoint(base_url: str, allow_remote: bool = False) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    hostname = parsed.hostname or ""

    if hostname != "localhost":
        try:
            results = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            if not allow_remote:
                raise BackendError(
                    f"Could not resolve {hostname!r}; refusing non-local endpoint. Pass --allow-remote to override.",
                    exit_code=3,
                )
            results = []

        addresses = {r[4][0] for r in results}
        local = {"127.0.0.1", "::1"}

        if not results or not addresses.issubset(local):
            if not allow_remote:
                raise BackendError(
                    f"Remote endpoint refused: {base_url}. Pass --allow-remote to override.",
                    exit_code=3,
                )

    print(f"[coworker] endpoint: {base_url}", file=sys.stderr)
    return base_url


def _run_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user_messages: list[str],
    max_tokens: int,
) -> str:
    import openai

    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    messages = [{"role": "system", "content": system}] + [
        {"role": "user", "content": m} for m in user_messages
    ]
    response = client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens
    )
    return response.choices[0].message.content


def _resolve_backend_and_model(
    backend: str | None,
    model: str | None,
) -> tuple[str, str]:
    """Apply 4-level precedence for backend and model.

    Precedence (highest → lowest):
    1. Explicit arg passed to run_worker()
    2. COWORKER_BACKEND / COWORKER_MODEL env vars
    3. read_config() from config.py
    4. Built-in defaults: "llamacpp" / DEFAULT_MODEL.model_id
    """
    from coworker.config import read_config
    from coworker.models import DEFAULT_MODEL

    cfg = read_config()

    if backend is None:
        backend = os.environ.get("COWORKER_BACKEND")
    if backend is None:
        backend = cfg.get("backend") or None
    if backend is None:
        backend = "llamacpp"

    if model is None:
        model = os.environ.get("COWORKER_MODEL")
    if model is None:
        model = cfg.get("model") or None
    if model is None:
        model = DEFAULT_MODEL.model_id

    return backend, model


def run_worker(
    system: str,
    user_messages: list[str],
    max_tokens: int = 4096,
    *,
    backend: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    allow_remote: bool = False,
) -> str:
    backend, model = _resolve_backend_and_model(backend, model)

    if backend == "ollama":
        if base_url is None:
            base_url = os.environ.get("COWORKER_BASE_URL", "http://localhost:11434/v1")

        resolve_endpoint(base_url, allow_remote)
        return _run_openai_compat(base_url, "ollama", model, system, user_messages, max_tokens)

    elif backend == "llamacpp":
        if base_url is None:
            base_url = os.environ.get("COWORKER_BASE_URL", "http://localhost:8080/v1")

        resolve_endpoint(base_url, allow_remote)
        return _run_openai_compat(base_url, "none", model, system, user_messages, max_tokens)

    elif backend == "mlx":
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise BackendError("MLX backend requires Darwin/arm64.", exit_code=3)

        from mlx_lm import generate, load

        mlx_model, tokenizer = load(model)
        prompt = "\n".join([system] + user_messages)
        result = generate(mlx_model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
        return result

    else:
        raise BackendError(f"Unknown backend: {backend!r}", exit_code=1)
