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

```bash
git clone https://github.com/YOUR_USERNAME/coworker-tools.git
cd coworker-tools
bash setup.sh
source .venv/bin/activate
```

> There is no `curl | bash` installer. Do not use one.

## Quickstart — llama.cpp (default)

```bash
# Start llama-server with any GGUF model
llama-server -m /path/to/your-model.gguf

ask-coworker --question "What does this file do?" --paths src/coworker/safety.py
```

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

## Environment variables

| Variable | Description |
|---|---|
| `COWORKER_BACKEND` | Backend to use: `llamacpp` (default), `ollama`, or `mlx` |
| `COWORKER_BASE_URL` | Backend base URL (default: `http://localhost:8080/v1` for llamacpp, `http://localhost:11434/v1` for ollama) |
| `COWORKER_MODEL` | Model name (default: `Qwopus3.5-9B-Coder-MTP-GGUF.Q5_K_M` for llamacpp, `qwen2.5-coder:14b` for ollama, `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` for mlx) |

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
