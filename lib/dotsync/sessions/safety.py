"""Rules every deletion goes through: a strict id, one path component under a
known root, and a remove that never follows a symlink."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from dotsync.cli_json import JSONCommandError

SESSION_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class InvalidSessionId(JSONCommandError):
    def __init__(self, value: object) -> None:
        super().__init__("invalid_session_id", f"not a session id: {value!r}")


class UnsafePath(ValueError):
    """A path that is not directly inside the root it was meant for."""


def is_valid_id(value: object) -> bool:
    # fullmatch: `$` alone would accept a trailing newline.
    return isinstance(value, str) and SESSION_ID.fullmatch(value) is not None


def validate_id(value: object) -> str:
    """The id itself, or InvalidSessionId — before any filesystem call."""
    if not is_valid_id(value):
        raise InvalidSessionId(value)
    return value  # type: ignore[return-value]


def child(root: Path, name: str) -> Path:
    """`root/name`, where `name` is one component and the parent really is
    `root` (a root that is itself a symlink is refused)."""
    if name in ("", ".", "..") or "/" in name:
        raise UnsafePath(f"not a single path component: {name!r}")
    path = root / name
    try:
        parent = path.parent.resolve()
    except (RuntimeError, OSError) as e:  # RuntimeError: a symlink loop
        raise UnsafePath(f"{root} cannot be resolved: {e}") from e
    if parent != root.absolute():
        raise UnsafePath(f"{path} is not directly inside {root}")
    return path


def remove(path: Path) -> str | None:
    """Delete a file, symlink (never its target) or directory tree. None when
    done or already gone, else a one-line error naming the path."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return None
        if not path.exists():
            return None
    except FileNotFoundError:
        return None
    except OSError as e:
        return _one_line(path, e)

    errors: list[str] = []

    def collect(_func, failed_path, exc) -> None:
        errors.append(_one_line(Path(failed_path), exc))

    shutil.rmtree(path, onexc=collect)
    return errors[0] if errors else None


def _one_line(path: Path, error: BaseException) -> str:
    return f"{path}: {error}".replace("\n", " ")
