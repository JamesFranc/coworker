"""Path validation helpers used to confine file access to the working tree."""

import os


def resolve_within(base_dir, user_path):
    """Resolve ``user_path`` and ensure it stays inside ``base_dir``.

    Returns the absolute path, or raises ValueError if it escapes the base.
    """
    base = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base, user_path))
    if not candidate.startswith(base + os.sep) and candidate != base:
        raise ValueError(f"path escapes base directory: {user_path}")
    return candidate


def is_secret_filename(name):
    lowered = name.lower()
    return any(token in lowered for token in (".env", "id_rsa", ".pem", "secret"))
