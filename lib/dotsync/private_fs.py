"""Symlink-safe persistence helpers for DotSync-owned private state."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


class UnsafePrivatePath(ValueError):
    """Raised when a private filesystem operation would leave its boundary."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _within_root(path: Path, root: Path) -> tuple[Path, Path]:
    absolute_path = _absolute(path)
    absolute_root = _absolute(root)
    try:
        absolute_path.relative_to(absolute_root)
    except ValueError as error:
        raise UnsafePrivatePath("path must stay inside the private root") from error
    return absolute_path, absolute_root


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _reject_symlink_components(path: Path) -> None:
    """Reject an existing symlink in ``path`` or any of its ancestors."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        metadata = _lstat(current)
        if metadata is None:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePrivatePath(f"symlink is not allowed in private path: {current}")


def _require_directory(path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePrivatePath(f"private path is not a directory: {path}")


def ensure_private_dir(path: Path, *, root: Path) -> None:
    """Create a private directory tree with mode ``0700`` without symlinks."""
    directory, private_root = _within_root(path, root)
    _reject_symlink_components(private_root)
    _reject_symlink_components(directory)

    missing: list[Path] = []
    current = directory
    while _lstat(current) is None:
        missing.append(current)
        current = current.parent

    _require_directory(current)
    for new_directory in reversed(missing):
        try:
            os.mkdir(new_directory, 0o700)
        except FileExistsError:
            metadata = _lstat(new_directory)
            if metadata is None or stat.S_ISLNK(metadata.st_mode):
                raise UnsafePrivatePath(
                    f"symlink is not allowed in private path: {new_directory}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise UnsafePrivatePath(f"private path is not a directory: {new_directory}")
        os.chmod(new_directory, 0o700)

    os.chmod(directory, 0o700)


def _validate_private_file(path: Path, *, root: Path) -> tuple[Path, Path]:
    target, private_root = _within_root(path, root)
    _reject_symlink_components(private_root)
    _reject_symlink_components(target)
    metadata = _lstat(target)
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePrivatePath(f"symlink is not allowed in private path: {target}")
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePrivatePath(f"private path is not a regular file: {target}")
    return target, private_root


def atomic_write_json(path: Path, data: Any, *, root: Path) -> None:
    """Atomically persist JSON as a private ``0600`` regular file."""
    target, private_root = _validate_private_file(path, root=root)
    ensure_private_dir(target.parent, root=private_root)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
        if stat.S_IMODE(os.stat(target).st_mode) != 0o600:
            raise UnsafePrivatePath(f"private file has unsafe permissions: {target}")
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def read_private_json(path: Path, *, root: Path) -> Any:
    """Read a regular JSON file after enforcing the private path boundary."""
    target, _ = _validate_private_file(path, root=root)
    metadata = _lstat(target)
    if metadata is None:
        raise FileNotFoundError(target)
    with target.open(encoding="utf-8") as file:
        return json.load(file)


def remove_private_tree(path: Path, *, allowed_root: Path) -> None:
    """Remove a regular private tree only after scanning it for symlinks."""
    target, root = _within_root(path, allowed_root)
    if target == root:
        raise UnsafePrivatePath("private tree must be a strict descendant of allowed_root")
    _reject_symlink_components(root)
    _reject_symlink_components(target)

    target_metadata = _lstat(target)
    if target_metadata is None:
        return
    if stat.S_ISLNK(target_metadata.st_mode):
        raise UnsafePrivatePath(f"symlink is not allowed in private path: {target}")
    if not stat.S_ISDIR(target_metadata.st_mode):
        raise UnsafePrivatePath(f"private tree is not a directory: {target}")

    resolved_target = target.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if resolved_target == resolved_root or resolved_root not in resolved_target.parents:
        raise UnsafePrivatePath("private tree must be a strict descendant of allowed_root")

    files: list[Path] = []
    directories: list[Path] = []

    def scan(directory: Path) -> None:
        directories.append(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_path = Path(entry.path)
                if entry.is_symlink():
                    raise UnsafePrivatePath(
                        f"symlink is not allowed in private path: {entry_path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    scan(entry_path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(entry_path)
                else:
                    raise UnsafePrivatePath(
                        f"unsupported private tree entry: {entry_path}"
                    )

    scan(target)
    for file_path in files:
        file_path.unlink()
    for directory in reversed(directories):
        directory.rmdir()
