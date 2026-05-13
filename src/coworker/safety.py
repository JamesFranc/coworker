"""Local-only safety guards for coworker-tools.

This module is load-bearing: every file read, content scan, and write performed
by the CLIs must pass through these guards. The aim is defense in depth — each
guard is independently sufficient for its threat, and callers stack them.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pathspec


class SafetyError(Exception):
    """Raised when a safety guard refuses an operation."""

    def __init__(self, message: str, exit_code: int = 2):
        self.exit_code = exit_code
        super().__init__(message)


# -- Path resolution ---------------------------------------------------------


def resolve_paths(
    paths: list[str],
    cwd: Path,
    allow_outside_cwd: bool = False,
    follow_symlinks: bool = False,
) -> list[Path]:
    """Resolve paths to absolute; refuse cwd-escape and symlinks by default."""
    cwd_resolved = Path(cwd).resolve()
    resolved: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = cwd_resolved / p

        if not follow_symlinks and p.is_symlink():
            raise SafetyError(f"refusing symlink: {raw}")

        # Resolve with strict=False so we can also validate paths that don't yet
        # exist (e.g., new-file targets). strict=True would raise FileNotFound.
        abs_path = p.resolve()

        if not allow_outside_cwd:
            try:
                abs_path.relative_to(cwd_resolved)
            except ValueError as exc:
                raise SafetyError(
                    f"path escapes cwd: {raw} -> {abs_path}"
                ) from exc

        resolved.append(abs_path)
    return resolved


# -- Ignore rules ------------------------------------------------------------


def build_ignore_spec(cwd: Path) -> pathspec.PathSpec:
    """Combine .gitignore and .coworkerignore (if present) into one spec."""
    patterns: list[str] = []
    for name in (".gitignore", ".coworkerignore"):
        candidate = Path(cwd) / name
        if candidate.is_file():
            patterns.extend(
                candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def is_ignored(path: Path, cwd: Path, spec: pathspec.PathSpec) -> bool:
    """Return True iff path matches any ignore pattern (relative to cwd)."""
    cwd_resolved = Path(cwd).resolve()
    try:
        rel = Path(path).resolve().relative_to(cwd_resolved)
    except ValueError:
        # Outside cwd — not subject to project-local ignore rules.
        return False
    return spec.match_file(str(rel))


# -- Secret filename block-list ----------------------------------------------

SECRET_FILENAMES = {".env"}
SECRET_PATTERNS = [
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "*.pfx",
    "*.p12",
    "*.kdbx",
]
SECRET_STEM_PATTERNS = ["credentials*", "secrets*"]


def is_secret_filename(path: Path) -> bool:
    """Match the secret filename block-list (case-insensitive)."""
    name = Path(path).name.lower()
    if name in {s.lower() for s in SECRET_FILENAMES}:
        return True
    for pat in SECRET_PATTERNS:
        if fnmatch.fnmatchcase(name, pat.lower()):
            return True
    stem = Path(name).stem
    for pat in SECRET_STEM_PATTERNS:
        if fnmatch.fnmatchcase(name, pat.lower()):
            return True
        if fnmatch.fnmatchcase(stem, pat.lower()):
            return True
    return False


# -- Binary detection --------------------------------------------------------


def is_binary(path: Path) -> bool:
    """Heuristic: NUL byte in first 8 KiB => binary."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
    except OSError:
        return False
    return b"\x00" in chunk


# -- Secret content scan -----------------------------------------------------

SECRET_REGEXES: list[tuple[str, str]] = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub PAT (ghp_)"),
    (r"github_pat_[A-Za-z0-9_]{82}", "GitHub PAT (github_pat_)"),
    (r"xox[abprs]-[A-Za-z0-9\-]+", "Slack token"),
    (
        r'(?:_KEY|_SECRET|_TOKEN)\s*=\s*["\']?[A-Za-z0-9/+]{20,}["\']?',
        "high-entropy key/secret/token assignment",
    ),
]


def scan_secrets(path: Path) -> list[tuple[str, str]]:
    """Return list of (pattern_name, matched_snippet) for any hits."""
    hits: list[tuple[str, str]] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    for pattern, name in SECRET_REGEXES:
        for match in re.finditer(pattern, text):
            hits.append((name, match.group(0)))
    return hits


# -- Size checks -------------------------------------------------------------

MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_TOTAL_BYTES = 5 * 1024 * 1024  # 5 MB


def check_sizes(paths: list[Path], force: bool = False) -> None:
    """Raise SafetyError if any file >1MB or total >5MB (unless force=True)."""
    total = 0
    oversized: list[tuple[Path, int]] = []
    for p in paths:
        try:
            size = Path(p).stat().st_size
        except OSError:
            continue
        total += size
        if size > MAX_FILE_BYTES:
            oversized.append((p, size))

    if force:
        return

    if oversized:
        details = ", ".join(f"{p} ({s} bytes)" for p, s in oversized)
        raise SafetyError(
            f"file exceeds {MAX_FILE_BYTES}-byte limit: {details}"
        )
    if total > MAX_TOTAL_BYTES:
        raise SafetyError(
            f"total size {total} bytes exceeds {MAX_TOTAL_BYTES}-byte limit"
        )


# -- Atomic write ------------------------------------------------------------


def _unified_diff(before: str, after: str, target: Path) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target) + ".new",
        )
    )


def atomic_write(
    target: Path,
    content: str,
    mode: str,
    backup_suffix: str | None = None,
) -> Path:
    """Write content to target with the given mode.

    mode:
      "overwrite" — write to temp in target's dir, os.replace to target
      "backup"    — copy target to target.bak.<ts>, then overwrite
      "new"       — write to target.new, print unified diff to stderr
    """
    target = Path(target)

    if is_secret_filename(target):
        raise SafetyError(f"refusing to write secret-like filename: {target}")

    if mode not in {"overwrite", "backup", "new"}:
        raise SafetyError(f"unknown write mode: {mode}")

    parent = target.parent
    if not parent.exists():
        raise SafetyError(f"parent directory does not exist: {parent}")

    if mode == "new":
        new_path = target.with_suffix(target.suffix + ".new")
        _atomic_replace(new_path, content)
        before = ""
        if target.exists():
            try:
                before = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                before = ""
        sys.stderr.write(_unified_diff(before, content, target))
        return new_path

    if mode == "backup":
        if target.exists():
            suffix = backup_suffix or f".bak.{int(time.time())}"
            backup_path = target.with_suffix(target.suffix + suffix)
            shutil.copy2(target, backup_path)
        _atomic_replace(target, content)
        return target

    # mode == "overwrite"
    _atomic_replace(target, content)
    return target


def _atomic_replace(target: Path, content: str) -> None:
    """Write content to a temp file in target's dir and os.replace into place."""
    parent = target.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, target)
    except Exception:
        # Best-effort cleanup; do not mask the original error.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
