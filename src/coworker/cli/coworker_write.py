"""Generate a new file from a spec and write it safely."""

import argparse
import sys
from pathlib import Path

from coworker import backend as _backend
from coworker import safety as _safety

_SYSTEM_PROMPT = (
    "You are an expert code generator. "
    "Generate only the file content, no explanations, no markdown fences."
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="coworker-write",
        description="Generate a new file from a spec and write it safely.",
    )
    parser.add_argument(
        "--spec",
        required=True,
        metavar="TEXT_OR_FILE",
        help="Generation spec: inline text or a path to a file containing it.",
    )
    parser.add_argument(
        "--target",
        required=True,
        metavar="PATH",
        help="Path to write the generated file to.",
    )
    parser.add_argument(
        "--style-ref",
        metavar="PATH",
        help="Existing file to use as style reference.",
    )
    parser.add_argument(
        "--backend",
        choices=["ollama", "mlx"],
        help="Backend to use (default: ollama or $COWORKER_BACKEND).",
    )
    parser.add_argument("--model", help="Model name passed to the backend.")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local backend endpoints.",
    )
    parser.add_argument(
        "--allow-outside-cwd",
        action="store_true",
        help="Allow paths outside the current working directory.",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Allow symlinks when resolving paths.",
    )
    parser.add_argument(
        "--include-secrets",
        action="store_true",
        help="Skip secret-content scanning for style-ref.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip file-size limit enforcement.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated content to stdout; do not write to disk.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        metavar="INT",
        help="Maximum tokens for generation (default: 4096).",
    )

    write_mode = parser.add_mutually_exclusive_group()
    write_mode.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite target if it already exists.",
    )
    write_mode.add_argument(
        "--backup",
        action="store_true",
        help="Back up target before overwriting.",
    )
    write_mode.add_argument(
        "--new",
        action="store_true",
        help="Write to target.new and print a unified diff.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    cwd = Path.cwd()

    # -- Resolve spec text -------------------------------------------------------
    spec_candidate = Path(args.spec)
    if spec_candidate.is_file():
        spec_text = spec_candidate.read_text(encoding="utf-8")
    else:
        spec_text = args.spec

    # -- Validate target ---------------------------------------------------------
    try:
        (target_path,) = _safety.resolve_paths(
            [args.target],
            cwd,
            allow_outside_cwd=args.allow_outside_cwd,
            follow_symlinks=args.follow_symlinks,
        )
    except _safety.SafetyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    if _safety.is_secret_filename(target_path):
        print(
            f"error: refusing to write secret-like filename: {target_path}",
            file=sys.stderr,
        )
        sys.exit(2)

    target_exists = target_path.exists()
    if target_exists and not args.dry_run and not (args.overwrite or args.backup or args.new):
        print(
            f"error: {target_path} already exists; pass --overwrite, --backup, or --new",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Style reference ---------------------------------------------------------
    style_content: str | None = None
    style_filename: str | None = None
    if args.style_ref:
        try:
            (style_path,) = _safety.resolve_paths(
                [args.style_ref],
                cwd,
                allow_outside_cwd=args.allow_outside_cwd,
                follow_symlinks=args.follow_symlinks,
            )
        except _safety.SafetyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(exc.exit_code)

        if not args.include_secrets:
            if _safety.is_secret_filename(style_path):
                print(
                    f"error: style-ref is a secret-like filename: {style_path}",
                    file=sys.stderr,
                )
                sys.exit(2)
            if _safety.is_binary(style_path):
                print(
                    f"error: style-ref appears to be a binary file: {style_path}",
                    file=sys.stderr,
                )
                sys.exit(2)
            hits = _safety.scan_secrets(style_path)
            if hits:
                names = ", ".join(name for name, _ in hits)
                print(
                    f"error: style-ref contains secrets ({names}): {style_path}",
                    file=sys.stderr,
                )
                sys.exit(2)

        try:
            _safety.check_sizes([style_path], force=args.force)
        except _safety.SafetyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(exc.exit_code)

        style_content = style_path.read_text(encoding="utf-8")
        style_filename = style_path.name

    # -- Dry run (pre-backend) ---------------------------------------------------
    if args.dry_run:
        print(f"DRY RUN — would generate: {target_path}", file=sys.stderr)
        print(f"Spec: {spec_text[:120]}{'...' if len(spec_text) > 120 else ''}", file=sys.stderr)
        if style_filename:
            print(f"Style reference: {style_filename}", file=sys.stderr)
        sys.exit(0)

    # -- Build messages ----------------------------------------------------------
    user_messages: list[str] = []
    if style_content is not None:
        user_messages.append(
            f"=== Style reference: {style_filename} ===\n{style_content}"
        )
    user_messages.append(f"Generate the following:\n{spec_text}")

    # -- Call backend ------------------------------------------------------------
    # input_bytes = bytes of user content sent to the model (spec + style-ref)
    usage_context = {
        "command": "coworker-write",
        "num_files": 1 if style_content is not None else 0,
        "input_bytes": len(spec_text.encode("utf-8")) + (
            len(style_content.encode("utf-8")) if style_content is not None else 0
        ),
    }

    try:
        content = _backend.run_worker(
            system=_SYSTEM_PROMPT,
            user_messages=user_messages,
            max_tokens=args.max_tokens,
            backend=args.backend,
            model=args.model,
            allow_remote=args.allow_remote,
            usage_context=usage_context,
        )
    except _backend.BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    # -- Determine write mode ----------------------------------------------------
    if args.overwrite:
        mode = "overwrite"
    elif args.backup:
        mode = "backup"
    elif args.new:
        mode = "new"
    else:
        mode = "overwrite"

    # -- Write -------------------------------------------------------------------
    try:
        final_path = _safety.atomic_write(target_path, content, mode)
    except _safety.SafetyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    print(f"Written: {final_path}")


if __name__ == "__main__":
    main()
