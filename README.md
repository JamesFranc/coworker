# coworker-tools — local-only AI coworker for Claude Code

coworker-tools is a set of CLI utilities that offload bulk read-heavy or
boilerplate-generation tasks to a local LLM, keeping secrets and source code
off remote servers. It supports llama.cpp, Ollama, and MLX (Darwin/arm64)
backends with strict safety guards around file reading and writing.

## Install

**Prerequisites:** Python 3.10+, and one of:
- [llama.cpp](https://github.com/ggerganov/llama.cpp) `llama-server` (default)
- [Ollama](https://ollama.com) running locally
- An Apple Silicon Mac with `pip install -e ".[mlx]"`

Recommended (installs the CLIs onto your PATH):

```bash
git clone https://github.com/YOUR_USERNAME/coworker-tools.git
cd coworker-tools
uv tool install --editable .
uv tool update-shell   # ensure ~/.local/bin (or equivalent) is on PATH
```

For contributors who want a local virtualenv with dev/test deps, use the
fallback installers instead:

```bash
bash setup.sh            # creates .venv, installs deps, prompts for a model
source .venv/bin/activate
# or: pipx install --editable .
```

> There is no `curl | bash` installer. Do not use one.

## Quickstart — llama.cpp (default)

The default model is **Gemma 4 E4B**. Use `coworker-model` to list models,
switch the active one, and get the matching download command.

```bash
# Install llama.cpp
brew install llama.cpp

# See available models (active one is marked with *)
coworker-model --list

# Pick a model (writes the choice to config; offers to download the GGUF)
coworker-model --set "Gemma 4 E4B"

# Start llama-server against the GGUF you downloaded
llama-server -m ~/models/<your-model>.gguf

ask-coworker --question "What does this file do?" --paths src/coworker/safety.py
```

> **Behavior change:** the implicit llamacpp default is now Gemma 4 E4B
> (`gemma-4-E4B-it-UD-Q4_K_XL`), not Qwopus. If you relied on the old default,
> run `coworker-model --set Qwopus3.5-9B-Coder`, set `COWORKER_MODEL`, or pass `--model`.

## Quickstart — Ollama

```bash
ollama pull qwen2.5-coder:14b
ask-coworker --backend ollama --question "What does this file do?" --paths src/coworker/safety.py
```

## Quickstart — MLX (Darwin/arm64 only)

```bash
ask-coworker --backend mlx --question "Summarise this module" --paths src/coworker/backend.py
```

## CLI reference

### `ask-coworker`

Send one or more files as context and ask a question about them.

| Flag | Description |
|---|---|
| `--question TEXT` | **(required)** Question to ask the model |
| `--paths PATH [PATH ...]` | Files to include as context |
| `--backend {llamacpp,ollama,mlx}` | Override `COWORKER_BACKEND` |
| `--model NAME` | Override `COWORKER_MODEL` |
| `--max-tokens INT` | Max tokens in response (default: 4096) |
| `--allow-remote` | Allow non-localhost endpoints |
| `--allow-outside-cwd` | Allow paths outside the current working directory |
| `--follow-symlinks` | Follow symlinks when resolving paths |
| `--include-secrets` | Skip secret filename/content checks |
| `--force` | Skip file size limit checks |
| `--dry-run` | Print file list + total bytes; exit without calling the model |

### `coworker-write`

Generate a new file from a spec and write it safely.

| Flag | Description |
|---|---|
| `--spec TEXT_OR_FILE` | **(required)** Inline spec text or path to a spec file |
| `--target PATH` | **(required)** Destination path for the generated file |
| `--style-ref PATH` | Existing file to use as a style reference |
| `--backend {llamacpp,ollama,mlx}` | Backend to use (default: `llamacpp` or `$COWORKER_BACKEND`) |
| `--model NAME` | Model name passed to the backend |
| `--max-tokens INT` | Maximum tokens for generation (default: 4096) |
| `--allow-remote` | Allow non-local backend endpoints |
| `--allow-outside-cwd` | Allow paths outside the current working directory |
| `--follow-symlinks` | Allow symlinks when resolving paths |
| `--include-secrets` | Skip secret-content scanning for `--style-ref` |
| `--force` | Skip file size limit enforcement |
| `--dry-run` | Print what would be generated and exit; do not call the model or write to disk |
| `--overwrite` | Overwrite target if it already exists |
| `--backup` | Back up target before overwriting (saves `.bak.<timestamp>`) |
| `--new` | Write to `target.new` and print a unified diff |

`--overwrite`, `--backup`, and `--new` are mutually exclusive.

### `coworker-extract`

Extract conversation turns from Claude Code JSONL transcript files.

| Flag | Description |
|---|---|
| `--input FILE`, `-i FILE` | Input JSONL file (default: stdin) |
| `--output FILE`, `-o FILE` | Output file (default: stdout) |
| `--format {text,json}`, `-f` | Output format (default: `text`) |
| `--role {human,assistant,all}`, `-r` | Filter by role (default: `all`) |

### `coworker-model`

List available llamacpp worker models and select the active one. The selection
is stored in `$XDG_CONFIG_HOME/coworker/config.toml` (default `~/.config`) and
used by the llamacpp backend when no `--model` flag or `COWORKER_MODEL` env var
is set.

| Flag | Description |
|---|---|
| `--list` | List models (`label` + `model_id`); the active one is marked `*` |
| `--set LABEL` | Set the active model; offers to download the GGUF if missing |

Known labels (case-insensitive): `Gemma 4 E4B` (default), `Gemma 4 E2B`,
`Qwopus3.5-9B-Coder`.

## Environment variables

| Variable | Description |
|---|---|
| `COWORKER_BACKEND` | Backend to use: `llamacpp` (default), `ollama`, or `mlx` |
| `COWORKER_BASE_URL` | Backend base URL (default: `http://localhost:8080/v1` for llamacpp, `http://localhost:11434/v1` for ollama) |
| `COWORKER_MODEL` | Model name (default: `gemma-4-E4B-it-UD-Q4_K_XL` for llamacpp — or your `coworker-model` selection; `qwen2.5-coder:14b` for ollama; `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` for mlx) |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | General error (file not found, IO error, unknown backend) |
| `2` | Safety error (path escape, secret file, size limit, bad write mode) |
| `3` | Backend error (remote endpoint refused, MLX on non-arm64, model call failed) |

## Safety features

- **Local-only by default** — no remote providers in the default configuration.
- **`resolve_endpoint()` enforces localhost-only** — any URL that does not resolve to `127.0.0.1` or `::1` is refused unless `--allow-remote` is passed explicitly.
- **Secret filename block-list** — files matching `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`, `*.pfx`, `*.p12`, `credentials*`, `secrets*`, `*.kdbx` are blocked by default.
- **Secret content regex scan** — files are scanned for AWS access keys, GitHub PATs, Slack tokens, and high-entropy `_KEY`/`_SECRET`/`_TOKEN` assignments before being sent to the model.
- **Symlink + path-traversal protection** — symlinks are refused by default; paths that resolve outside the current working directory are refused unless `--allow-outside-cwd` is set.
- **Binary file skip** — files with a NUL byte in the first 8 KiB are silently skipped.
- **Size limits** — individual files >1 MB and total context >5 MB are refused unless `--force` is passed.
- **Atomic writes with overwrite/backup/new modes** — `coworker-write` writes to a temp file and uses `os.replace`, preventing partial writes; existing files are protected unless `--overwrite`, `--backup`, or `--new` is specified.
- **`.gitignore` + `.coworkerignore` support** — files ignored by either file are skipped automatically.

## MLX note

`pytest -q` passes on Linux and macOS x86_64 without MLX installed — the import
is mocked in the test suite. The real MLX path requires Darwin/arm64 and
`pip install -e ".[mlx]"`.
