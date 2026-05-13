"""Tests for coworker.safety — one allow + one refuse per guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from coworker.safety import (
    MAX_FILE_BYTES,
    SafetyError,
    atomic_write,
    build_ignore_spec,
    check_sizes,
    is_binary,
    is_ignored,
    is_secret_filename,
    resolve_paths,
    scan_secrets,
)


# 1. resolve_paths -----------------------------------------------------------


def test_resolve_paths_allows_inside_cwd(tmp_path: Path) -> None:
    inside = tmp_path / "a.txt"
    inside.write_text("hi")
    out = resolve_paths(["a.txt"], cwd=tmp_path)
    assert out == [inside.resolve()]


def test_resolve_paths_refuses_cwd_escape(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "outside.txt").write_text("nope")
    with pytest.raises(SafetyError) as exc:
        resolve_paths(["../outside.txt"], cwd=sub)
    assert exc.value.exit_code == 2


# 2. resolve_paths — symlink policy -----------------------------------------


def test_resolve_paths_allows_symlink_when_opted_in(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    out = resolve_paths(
        ["link.txt"], cwd=tmp_path, follow_symlinks=True
    )
    assert out == [target.resolve()]


def test_resolve_paths_refuses_symlink_by_default(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(SafetyError):
        resolve_paths(["link.txt"], cwd=tmp_path)


# 3. is_ignored --------------------------------------------------------------


def test_is_ignored_allows_normal_file(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("build/\n")
    f = tmp_path / "src.py"
    f.write_text("print(1)")
    spec = build_ignore_spec(tmp_path)
    assert is_ignored(f, tmp_path, spec) is False


def test_is_ignored_refuses_gitignored_file(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("build/\n*.log\n")
    (tmp_path / "build").mkdir()
    f = tmp_path / "build" / "out.txt"
    f.write_text("x")
    log = tmp_path / "app.log"
    log.write_text("x")
    spec = build_ignore_spec(tmp_path)
    assert is_ignored(f, tmp_path, spec) is True
    assert is_ignored(log, tmp_path, spec) is True


# 4. is_secret_filename ------------------------------------------------------


def test_is_secret_filename_allows_main_py(tmp_path: Path) -> None:
    assert is_secret_filename(tmp_path / "main.py") is False


def test_is_secret_filename_refuses_known_secrets(tmp_path: Path) -> None:
    assert is_secret_filename(tmp_path / ".env") is True
    assert is_secret_filename(tmp_path / ".env.production") is True
    assert is_secret_filename(tmp_path / "id_rsa") is True
    assert is_secret_filename(tmp_path / "id_ed25519") is True
    assert is_secret_filename(tmp_path / "credentials.json") is True
    assert is_secret_filename(tmp_path / "server.pem") is True
    assert is_secret_filename(tmp_path / "wildcard.key") is True
    # Case-insensitive
    assert is_secret_filename(tmp_path / ".ENV") is True


# 5. is_binary ---------------------------------------------------------------


def test_is_binary_allows_text(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("plain ascii\nsecond line\n")
    assert is_binary(f) is False


def test_is_binary_refuses_nul_bytes(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"prefix\x00suffix")
    assert is_binary(f) is True


# 6. scan_secrets ------------------------------------------------------------


def test_scan_secrets_allows_clean_file(tmp_path: Path) -> None:
    f = tmp_path / "clean.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    assert scan_secrets(f) == []


def test_scan_secrets_refuses_aws_key(tmp_path: Path) -> None:
    f = tmp_path / "leak.txt"
    f.write_text("aws_access_key=AKIAIOSFODNN7EXAMPLE\n")
    hits = scan_secrets(f)
    assert any("AWS" in name for name, _ in hits)


def test_scan_secrets_refuses_github_pat_ghp(tmp_path: Path) -> None:
    token = "ghp_" + "A" * 36
    f = tmp_path / "leak.txt"
    f.write_text(f"token = {token}\n")
    hits = scan_secrets(f)
    assert any("ghp_" in name for name, _ in hits)


def test_scan_secrets_refuses_github_pat_fine_grained(tmp_path: Path) -> None:
    token = "github_pat_" + "A" * 82
    f = tmp_path / "leak.txt"
    f.write_text(f"token = {token}\n")
    hits = scan_secrets(f)
    assert any("github_pat_" in name for name, _ in hits)


def test_scan_secrets_refuses_slack_token(tmp_path: Path) -> None:
    f = tmp_path / "leak.txt"
    f.write_text("token = xoxb-1234567890-abcdefghij\n")
    hits = scan_secrets(f)
    assert any("Slack" in name for name, _ in hits)


def test_scan_secrets_refuses_generic_token_assignment(tmp_path: Path) -> None:
    f = tmp_path / "leak.txt"
    f.write_text('MY_TOKEN = "abcdefghijklmnopqrstuvwxyz1234"\n')
    hits = scan_secrets(f)
    assert any("token" in name.lower() or "key" in name.lower() or "secret" in name.lower() for name, _ in hits)


# 7. check_sizes -------------------------------------------------------------


def test_check_sizes_allows_small_file(tmp_path: Path) -> None:
    f = tmp_path / "small.txt"
    f.write_text("tiny")
    check_sizes([f])  # no raise


def test_check_sizes_refuses_oversize(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.touch()
    os.truncate(big, MAX_FILE_BYTES + 1)
    with pytest.raises(SafetyError):
        check_sizes([big])


def test_check_sizes_force_bypass(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.touch()
    os.truncate(big, MAX_FILE_BYTES + 1)
    check_sizes([big], force=True)  # no raise


def test_check_sizes_refuses_total_over_5mb(tmp_path: Path) -> None:
    files = []
    for i in range(6):
        f = tmp_path / f"part{i}.bin"
        f.touch()
        os.truncate(f, 900 * 1024)  # ~900 KB each, 6 × ~900 KB > 5 MB
        files.append(f)
    with pytest.raises(SafetyError):
        check_sizes(files)
    check_sizes(files, force=True)  # no raise


# 8. atomic_write overwrite --------------------------------------------------


def test_atomic_write_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old")
    result = atomic_write(target, "new content", mode="overwrite")
    assert result == target
    assert target.read_text() == "new content"
    # No leftover temp files in the directory
    leftovers = [
        p for p in tmp_path.iterdir() if p.name.startswith(".out.txt.") and p.name.endswith(".tmp")
    ]
    assert leftovers == []


# 9. atomic_write backup -----------------------------------------------------


def test_atomic_write_backup(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("original")
    result = atomic_write(target, "replacement", mode="backup")
    assert result == target
    assert target.read_text() == "replacement"
    backups = [p for p in tmp_path.iterdir() if ".bak." in p.name]
    assert len(backups) == 1
    assert backups[0].read_text() == "original"


# 10. atomic_write new -------------------------------------------------------


def test_atomic_write_new(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out.txt"
    target.write_text("v1\n")
    result = atomic_write(target, "v2\n", mode="new")
    assert result == tmp_path / "out.txt.new"
    assert result.read_text() == "v2\n"
    # Original file is untouched
    assert target.read_text() == "v1\n"
    captured = capsys.readouterr()
    assert "v1" in captured.err
    assert "v2" in captured.err


# 11. atomic_write refuses secret target -------------------------------------


def test_atomic_write_refuses_secret_target(tmp_path: Path) -> None:
    with pytest.raises(SafetyError):
        atomic_write(tmp_path / ".env", "SECRET=1", mode="overwrite")
    with pytest.raises(SafetyError):
        atomic_write(tmp_path / "id_rsa", "key", mode="overwrite")


# 12. .coworkerignore -----------------------------------------------------------


def test_coworkerignore_pattern(tmp_path: Path) -> None:
    (tmp_path / ".coworkerignore").write_text("*.secret\n")
    secret_file = tmp_path / "foo.secret"
    secret_file.write_text("shh")
    spec = build_ignore_spec(tmp_path)
    assert is_ignored(secret_file, tmp_path, spec) is True
