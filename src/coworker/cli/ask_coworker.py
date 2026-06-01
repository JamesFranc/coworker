"""Send files + a question to the local worker LLM and print the answer."""

import argparse
import sys
from pathlib import Path

from coworker import backend as _backend
from coworker import safety as _safety


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ask the local worker LLM a question about one or more files."
    )
    parser.add_argument("--question", required=True, help="Question to ask")
    parser.add_argument("--paths", nargs="+", metavar="PATH", default=[], help="Files to include as context")
    parser.add_argument("--backend", choices=["ollama", "mlx"], default=None, help="Override COWORKER_BACKEND")
    parser.add_argument("--model", default=None, help="Override COWORKER_MODEL")
    parser.add_argument("--allow-remote", action="store_true", help="Allow non-localhost endpoints")
    parser.add_argument("--allow-outside-cwd", action="store_true", help="Allow paths outside cwd")
    parser.add_argument("--follow-symlinks", action="store_true", help="Follow symlinks when resolving paths")
    parser.add_argument("--include-secrets", action="store_true", help="Skip secret filename/content checks")
    parser.add_argument("--force", action="store_true", help="Skip size limit checks")
    parser.add_argument("--dry-run", action="store_true", help="Print file list + total bytes, exit without calling worker")
    parser.add_argument("--max-tokens", type=int, default=4096, metavar="INT", help="Max tokens in response (default: 4096)")
    args = parser.parse_args()

    cwd = Path.cwd()

    try:
        resolved = _safety.resolve_paths(args.paths, cwd, args.allow_outside_cwd, args.follow_symlinks)
    except _safety.SafetyError as e:
        print(f"Safety error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except FileNotFoundError as e:
        print(f"File not found: {e}", file=sys.stderr)
        sys.exit(1)

    ignore_spec = _safety.build_ignore_spec(cwd)

    try:
        _safety.check_sizes(resolved, args.force)
    except _safety.SafetyError as e:
        print(f"Safety error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)

    accepted: list[Path] = []
    for path in resolved:
        if _safety.is_ignored(path, cwd, ignore_spec):
            print(f"Skipping ignored file: {path}", file=sys.stderr)
            continue

        if not args.include_secrets:
            if _safety.is_secret_filename(path):
                print(f"Skipping secret-named file: {path}", file=sys.stderr)
                continue
            hits = _safety.scan_secrets(path)
            if hits:
                names = ", ".join(name for name, _ in hits)
                print(f"Skipping file with secret content ({names}): {path}", file=sys.stderr)
                continue

        if _safety.is_binary(path):
            print(f"Skipping binary file: {path}", file=sys.stderr)
            continue

        accepted.append(path)

    if args.dry_run:
        print("DRY RUN — files to send:", file=sys.stderr)
        total = 0
        for path in accepted:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            total += size
            print(f"  {path}  ({size} bytes)", file=sys.stderr)
        print(f"Total: {total} bytes", file=sys.stderr)
        sys.exit(0)

    system = "You are a helpful code assistant. Answer concisely."

    user_messages: list[str] = []
    # input_bytes = bytes of user content sent to the model (files + question)
    input_bytes = 0
    for path in accepted:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"Error reading {path}: {e}", file=sys.stderr)
            sys.exit(1)
        input_bytes += len(content.encode("utf-8", errors="replace"))
        user_messages.append(f"=== {path.name} ===\n{content}")
    input_bytes += len(args.question.encode("utf-8"))
    user_messages.append(args.question)

    usage_context = {
        "command": "ask-coworker",
        "num_files": len(accepted),
        "input_bytes": input_bytes,
    }

    try:
        answer = _backend.run_worker(
            system,
            user_messages,
            args.max_tokens,
            backend=args.backend,
            model=args.model,
            allow_remote=args.allow_remote,
            usage_context=usage_context,
        )
    except _safety.SafetyError as e:
        print(f"Safety error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except _backend.BackendError as e:
        print(f"Backend error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)

    print(answer)


if __name__ == "__main__":
    main()
