"""Human-readable change summaries and diffs for plan previews.

Pure functions: read files, mutate nothing. The direction convention for
every function here is "dest becomes source" — the same source/dest meaning
`plan_file_copy` uses, so summaries read as "what applying this change does".
"""

from __future__ import annotations

import difflib
import json
import tomllib
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
    summary = f"+{added} −{removed}"
    keys = _key_summary(source.suffix, old_text, new_text)
    if keys:
        summary += f" · {keys}"
    return summary


_PARSERS = {".json": json.loads, ".toml": tomllib.loads}


def _key_summary(suffix: str, old_text: str, new_text: str) -> str:
    loads = _PARSERS.get(suffix)
    if loads is None:
        return ""
    try:
        old = loads(old_text)
        new = loads(new_text)
    except ValueError:  # JSONDecodeError/TOMLDecodeError 모두 ValueError 하위
        return ""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return ""
    keys = [k for k in new if k not in old or old[k] != new[k]]
    keys += [k for k in old if k not in new]
    if not keys:
        return ""
    if len(keys) > _KEY_LIMIT:
        return ", ".join(keys[:_KEY_LIMIT]) + f" …외 {len(keys) - _KEY_LIMIT}"
    return ", ".join(keys)


def _truncate(lines: "list[str]", max_lines: int) -> "list[str]":
    if len(lines) <= max_lines:
        return lines
    hidden = len(lines) - max_lines
    return lines[:max_lines] + [f"… (+{hidden} lines truncated)"]


def unified_diff_text(
    source: Path, dest: Path, *, max_lines: int = DIFF_MAX_LINES
) -> str:
    """Unified diff of dest → source (what applying the change does to dest)."""
    new_text, new_err = _load(source)
    old_text, old_err = _load(dest)
    if new_err or old_err:
        return f"(diff unavailable: {new_err or old_err})"
    if new_text is None or old_text is None:
        return "(binary — no diff)"
    lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=str(dest),
            tofile=str(source),
            lineterm="",
        )
    )
    if not lines:
        return "(no line-level changes)"
    return "\n".join(_truncate(lines, max_lines))


def full_file_lines(
    path: Path, prefix: str, *, max_lines: int = DIFF_MAX_LINES
) -> str:
    """Whole-file listing for create (+) / remove (-) entries in the diff view."""
    text, err = _load(path)
    if err:
        return f"(diff unavailable: {err})"
    if text is None:
        return "(binary — no diff)"
    lines = [f"{prefix}{line}" for line in text.splitlines()]
    return "\n".join(_truncate(lines, max_lines))
