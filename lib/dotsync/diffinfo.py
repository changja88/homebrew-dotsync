"""Human-readable change summaries and diffs for plan previews.

Pure functions: read files, mutate nothing. The direction convention for
every function here is "dest becomes source" — the same source/dest meaning
`plan_file_copy` uses, so summaries read as "what applying this change does".
"""

from __future__ import annotations

import difflib
from pathlib import Path

DIFF_MAX_LINES = 200
_KEY_LIMIT = 4


def _load(path: Path) -> "tuple[str | None, str]":
    """Read a file as UTF-8 text. Returns (text, error).

    (None, "") means the file exists but is binary; (None, "<msg>") means
    the read itself failed (missing file, permissions, ...).
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, str(exc)
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError:
        return None, ""


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    raise AssertionError("unreachable")


def _line_counts(old: str, new: str) -> "tuple[int, int]":
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def summarize_pair(source: Path, dest: Path) -> str:
    """One-line summary of an update: what changes when dest becomes source."""
    new_text, new_err = _load(source)
    old_text, old_err = _load(dest)
    if new_err or old_err:
        return ""
    if new_text is None or old_text is None:
        old_size = dest.stat().st_size
        new_size = source.stat().st_size
        return f"binary · {_human_size(old_size)} → {_human_size(new_size)}"
    added, removed = _line_counts(old_text, new_text)
    return f"+{added} −{removed}"
