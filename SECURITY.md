# Security

## Threat model

coworker-tools is a **local-only** tool. It runs on your machine, talks to a
model server that also runs on your machine, and writes output files on your
machine.

The realistic threats are:

1. **Accidentally sending secrets or sensitive files** to the local model's
   context window (e.g., `.env` files, SSH keys, files containing API tokens).
2. **Writing outside intended directories** — a poorly formed `--target` path
   escaping the working directory.

This is **not** a network-facing service. There is no authentication surface,
no incoming connections, and no shared multi-user state. Remote attacks are not
in scope.

---

## Guards and what they protect

### Path traversal / cwd escape

`resolve_paths()` in `safety.py` resolves every input path to its absolute form
and then checks it is a descendant of the current working directory. A path like
`../../etc/passwd` is refused with exit code 2.

Override: `--allow-outside-cwd` (use only when the target legitimately lives
outside the project tree, e.g., writing to `/tmp`).

### Symlink policy

Input paths that are themselves symlinks are refused by default. Symlinks in
parent path components are not checked but are caught by the cwd-escape check
if they resolve outside the working directory. A symlink could point anywhere
on the filesystem, making path-traversal checks unreliable.

Override: `--follow-symlinks` (safe if you control and understand every symlink
in your tree).

### `.gitignore` + `.coworkerignore`

`build_ignore_spec()` reads both files (if present) from the project root and
builds a combined pathspec. Any file matching a pattern in either file is skipped
before being sent to the model.

Add project-specific exclusions to `.coworkerignore` without editing
`.gitignore`.

Note: rules are evaluated at read-time against the files passed by the caller.
Files added to `.gitignore` after a scan has started are not re-checked mid-run.

### Secret filename block-list

Files whose names match any of the following patterns are refused by default:

- Exact names: `.env`
- Glob patterns: `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`, `*.pfx`, `*.p12`
- Stem patterns: `credentials*`, `secrets*`, `*.kdbx`

Matching is case-insensitive.

Override: `--include-secrets` (skips both filename and content checks).

### Secret content regex scan

Even if a file passes the filename check, `scan_secrets()` reads its content
and searches for these patterns:

| Pattern | What it catches |
|---|---|
| `AKIA[0-9A-Z]{16}` | AWS access key ID |
| `ghp_[A-Za-z0-9]{36}` | GitHub personal access token (`ghp_` prefix) |
| `github_pat_[A-Za-z0-9_]{82}` | GitHub fine-grained PAT |
| `xox[abprs]-[A-Za-z0-9\-]+` | Slack token |
| `(?:_KEY\|_SECRET\|_TOKEN)\s*=\s*["\']?[A-Za-z0-9/+]{20,}["\']?` | High-entropy key/secret/token assignment |

This scan is **not exhaustive** — it catches common patterns, not all secrets.
Do not rely on it as the sole mechanism for protecting sensitive files.

Override: `--include-secrets`.

### Binary file skip

`is_binary()` reads the first 8 KiB of each file and refuses it if a NUL byte
is found. Binary files (compiled artifacts, images, archives) have no value as
LLM context and may be large.

There is no override flag for this check.

### Size limits

`check_sizes()` enforces two limits:

- **Per-file**: 1 MB maximum.
- **Total context**: 5 MB maximum.

Exceeding either limit raises a `SafetyError` with exit code 2.

Override: `--force` disables size checks entirely.

### Atomic write (temp + os.replace)

`atomic_write()` in `safety.py` writes content to a temporary file in the same
directory as the target, then uses `os.replace()` to move it into place. This
ensures the target file is never in a partially-written state visible to other
processes.

### Overwrite protection (--overwrite / --backup / --new)

`coworker-write` refuses to write to an existing file unless one of these flags
is passed:

- `--overwrite` — replace the file in place (atomic).
- `--backup` — copy the existing file to `<target>.bak.<unix-timestamp>` first,
  then overwrite.
- `--new` — write to `<target>.new` and print a unified diff to stderr without
  touching the original.

### Localhost-only endpoint enforcement

`resolve_endpoint()` in `backend.py` resolves the hostname of every backend URL
via DNS. If any resolved address is not `127.0.0.1` or `::1` (and the hostname
is not literally `localhost`), the connection is refused with exit code 3.

Override: `--allow-remote` (use only when you have explicitly set up a trusted
local-network endpoint and understand the risks).

Note: the hostname `localhost` is accepted without DNS resolution. If your
`/etc/hosts` maps `localhost` to a non-loopback address (rare), this is not
caught.

---

## Override flags and their risks

| Flag | What it disables | Risk |
|---|---|---|
| `--allow-remote` | Localhost-only endpoint check | Sends context to a non-local model server |
| `--allow-outside-cwd` | cwd-escape check | Allows reading/writing anywhere on the filesystem |
| `--follow-symlinks` | Symlink refusal | A symlink could point to sensitive files outside the project |
| `--include-secrets` | Filename block-list + content regex scan | Secret files and tokens may be sent to the model |
| `--force` | Per-file and total size limits | Very large files may exhaust memory or produce slow responses |
| `--overwrite` | Existing-file protection | Overwrites a file without a backup |

---

## Known limits

- The secret content regex scan catches common patterns, not all secrets. A
  token with an unusual prefix or format will not be caught.
- Ignore rules are evaluated at read-time against files passed by the caller.
  Files added to `.gitignore` after a scan has started are not re-checked.
- `--force` disables both per-file and total size checks entirely. Use with care
  on large codebases.
- Traffic between the CLI and the model server is not encrypted. This is
  acceptable for localhost, but if you use `--allow-remote` to point at a
  network endpoint, ensure the connection is secured at the network layer (VPN,
  TLS termination, etc.).

---

## Reporting issues

Open an issue at the project's GitHub repository. Do not include actual secret
values in issue reports.
