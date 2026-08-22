#!/usr/bin/env python3
"""Private descriptor-bound filesystem operations for macOS app releases."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _simple_name(value: str) -> str:
    if value in {"", ".", ".."} or "/" in value or "\0" in value:
        raise ValueError("release entry name must be one simple path component")
    return value


def _open_directory(path: str | os.PathLike[str], *, dir_fd: int | None = None) -> int:
    return os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=dir_fd,
    )


def _validate_private_directory(metadata: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real directory")
    if metadata.st_uid != os.geteuid():
        raise ValueError(f"{label} must be owned by the effective user")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(f"{label} must not be group or other writable")


def validate_temp_root(path: Path) -> str:
    if not path.is_absolute() or path == Path("/"):
        raise ValueError("TMPDIR must be an absolute non-root path")
    descriptor = _open_directory(path)
    root_descriptor = _open_directory("/")
    try:
        metadata = os.fstat(descriptor)
        if _identity(metadata) == _identity(os.fstat(root_descriptor)):
            raise ValueError("TMPDIR must resolve to a non-root directory")
        _validate_private_directory(metadata, "TMPDIR")
        return f"{metadata.st_dev}:{metadata.st_ino}"
    finally:
        os.close(root_descriptor)
        os.close(descriptor)


def identity_current(*, require_mode: int | None = None) -> str:
    descriptor = _open_directory(".")
    try:
        metadata = os.fstat(descriptor)
        _validate_private_directory(metadata, "current release directory")
        if require_mode is not None and stat.S_IMODE(metadata.st_mode) != require_mode:
            raise ValueError(
                f"current release directory must have exact mode {require_mode:04o}"
            )
        return f"{metadata.st_dev}:{metadata.st_ino}"
    finally:
        os.close(descriptor)


def _entry_binding(directory_fd: int, name: str) -> tuple[os.stat_result, str]:
    name = _simple_name(name)
    path_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if stat.S_ISDIR(path_metadata.st_mode):
        descriptor = _open_directory(name, dir_fd=directory_fd)
        kind = "d"
    elif stat.S_ISREG(path_metadata.st_mode):
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        kind = "f"
    else:
        raise ValueError(f"{name} must be a regular file or real directory")
    try:
        opened_metadata = os.fstat(descriptor)
        if _identity(path_metadata) != _identity(opened_metadata):
            raise ValueError(f"{name} identity changed while binding")
        return opened_metadata, kind
    finally:
        os.close(descriptor)


def identity_entry(name: str, *, parent: bool) -> str:
    directory_fd = _open_directory(".." if parent else ".")
    try:
        metadata, kind = _entry_binding(directory_fd, name)
        return f"{_simple_name(name)}:{metadata.st_dev}:{metadata.st_ino}:{kind}"
    finally:
        os.close(directory_fd)


def identity_path_entry(directory: Path, name: str) -> str:
    directory_fd = _open_directory(directory)
    try:
        metadata, kind = _entry_binding(directory_fd, name)
        return f"{_simple_name(name)}:{metadata.st_dev}:{metadata.st_ino}:{kind}"
    finally:
        os.close(directory_fd)


def read_cask_binding(name: str, expected_value: str) -> str:
    expected_name, expected_identity, expected_kind = _parse_owned(expected_value)
    if _simple_name(name) != expected_name or expected_kind != "f":
        raise ValueError("Cask binding ownership did not match its expected file")
    directory_fd = _open_directory(".")
    descriptor = -1
    try:
        metadata, kind = _entry_binding(directory_fd, name)
        if kind != "f" or _identity(metadata) != expected_identity:
            raise ValueError("Cask binding file was rebound")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        opened = os.fstat(descriptor)
        if _identity(opened) != expected_identity:
            raise ValueError("Cask binding file was rebound while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(map(len, chunks)) > 4096:
                raise ValueError("Cask binding file was unexpectedly large")
        data = json.loads(b"".join(chunks))
        keys = ("casks_dev", "casks_ino", "cask_dev", "cask_ino")
        if not isinstance(data, dict) or set(data) != set(keys):
            raise ValueError("Cask binding JSON had an unexpected schema")
        values = [data[key] for key in keys]
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values):
            raise ValueError("Cask binding JSON identities were invalid")
        return " ".join(str(value) for value in values)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Cask binding file was not valid JSON") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _parse_identity(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdecimal() for part in parts):
        raise ValueError("directory identity must be DEV:INO")
    return int(parts[0]), int(parts[1])


def _parse_owned(value: str) -> tuple[str, tuple[int, int], str]:
    parts = value.split(":")
    if (
        len(parts) != 4
        or not parts[1].isdecimal()
        or not parts[2].isdecimal()
        or parts[3] not in {"d", "f"}
    ):
        raise ValueError("owned entry must be NAME:DEV:INO:KIND")
    return _simple_name(parts[0]), (int(parts[1]), int(parts[2])), parts[3]


def _remove_bound_file(directory_fd: int, name: str, expected: tuple[int, int]) -> None:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=directory_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or _identity(metadata) != expected:
            raise ValueError(f"refusing to unlink rebound release file {name}")
        os.unlink(name, dir_fd=directory_fd)
    finally:
        os.close(descriptor)


def _remove_directory_contents(directory_fd: int) -> None:
    with os.scandir(directory_fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        expected = _identity(metadata)
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = _open_directory(name, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                if _identity(opened) != expected:
                    raise ValueError(f"release directory entry {name} was rebound")
                _remove_directory_contents(child_fd)
                os.fsync(child_fd)
            finally:
                os.close(child_fd)
            rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(rebound) != expected or not stat.S_ISDIR(rebound.st_mode):
                raise ValueError(f"release directory entry {name} was rebound")
            os.rmdir(name, dir_fd=directory_fd)
        elif stat.S_ISREG(metadata.st_mode):
            _remove_bound_file(directory_fd, name, expected)
        elif stat.S_ISLNK(metadata.st_mode):
            rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(rebound) != expected or not stat.S_ISLNK(rebound.st_mode):
                raise ValueError(f"release symlink entry {name} was rebound")
            os.unlink(name, dir_fd=directory_fd)
        else:
            raise ValueError(f"refusing to remove special release entry {name}")


def cleanup_current_workdir(
    *,
    parent: Path,
    name: str,
    parent_identity: str,
    work_identity: str,
    owned_values: list[str],
) -> None:
    name = _simple_name(name)
    expected_parent = _parse_identity(parent_identity)
    expected_work = _parse_identity(work_identity)
    owned = [_parse_owned(value) for value in owned_values]
    if len({entry_name for entry_name, _identity_value, _kind in owned}) != len(owned):
        raise ValueError("owned release entries must be unique")

    work_fd = _open_directory(".")
    cleanup_errors: list[str] = []
    try:
        opened_work = os.fstat(work_fd)
        if _identity(opened_work) != expected_work:
            raise ValueError("current directory is not the pinned release workdir")
        for entry_name, expected_entry, expected_kind in owned:
            try:
                metadata = os.stat(
                    entry_name,
                    dir_fd=work_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                cleanup_errors.append(f"owned release entry disappeared: {entry_name}")
                continue
            if _identity(metadata) != expected_entry:
                cleanup_errors.append(f"owned release entry was rebound: {entry_name}")
                continue
            try:
                if expected_kind == "f" and stat.S_ISREG(metadata.st_mode):
                    _remove_bound_file(work_fd, entry_name, expected_entry)
                elif expected_kind == "d" and stat.S_ISDIR(metadata.st_mode):
                    child_fd = _open_directory(entry_name, dir_fd=work_fd)
                    try:
                        if _identity(os.fstat(child_fd)) != expected_entry:
                            raise ValueError(
                                f"owned release directory was rebound: {entry_name}"
                            )
                        _remove_directory_contents(child_fd)
                        os.fsync(child_fd)
                    finally:
                        os.close(child_fd)
                    os.rmdir(entry_name, dir_fd=work_fd)
                else:
                    raise ValueError(f"owned release entry changed type: {entry_name}")
            except (OSError, ValueError) as error:
                cleanup_errors.append(str(error))
        os.fsync(work_fd)
        with os.scandir(work_fd) as entries:
            remaining = sorted(entry.name for entry in entries)
        if remaining:
            cleanup_errors.append(
                "unowned or rebound release entries remain: " + ", ".join(remaining)
            )
        if cleanup_errors:
            raise ValueError("; ".join(cleanup_errors))

        parent_fd = _open_directory(parent)
        try:
            opened_parent = os.fstat(parent_fd)
            _validate_private_directory(opened_parent, "TMPDIR")
            if _identity(opened_parent) != expected_parent:
                raise ValueError("TMPDIR identity changed before workdir removal")
            named_work = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(named_work.st_mode) or _identity(named_work) != expected_work:
                raise ValueError("workdir name no longer identifies the pinned directory")
            named_fd = _open_directory(name, dir_fd=parent_fd)
            try:
                if _identity(os.fstat(named_fd)) != expected_work:
                    raise ValueError("workdir binding changed before removal")
            finally:
                os.close(named_fd)
            os.rmdir(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        os.close(work_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    validate = subparsers.add_parser("validate-temp-root")
    validate.add_argument("path", type=Path)
    current = subparsers.add_parser("identity-current")
    current.add_argument("--require-mode", type=lambda value: int(value, 8))
    here = subparsers.add_parser("identity-here")
    here.add_argument("name")
    parent = subparsers.add_parser("identity-parent")
    parent.add_argument("name")
    path_entry = subparsers.add_parser("identity-path-entry")
    path_entry.add_argument("directory", type=Path)
    path_entry.add_argument("name")
    binding = subparsers.add_parser("read-cask-binding")
    binding.add_argument("name")
    binding.add_argument("expected")
    cleanup = subparsers.add_parser("cleanup-current")
    cleanup.add_argument("--parent", required=True, type=Path)
    cleanup.add_argument("--name", required=True)
    cleanup.add_argument("--parent-identity", required=True)
    cleanup.add_argument("--work-identity", required=True)
    cleanup.add_argument("--owned", action="append", default=[])
    return parser


def main(arguments: list[str] | None = None) -> int:
    namespace = _parser().parse_args(arguments)
    try:
        if namespace.operation == "validate-temp-root":
            print(validate_temp_root(namespace.path))
        elif namespace.operation == "identity-current":
            print(identity_current(require_mode=namespace.require_mode))
        elif namespace.operation == "identity-here":
            print(identity_entry(namespace.name, parent=False))
        elif namespace.operation == "identity-parent":
            print(identity_entry(namespace.name, parent=True))
        elif namespace.operation == "identity-path-entry":
            print(identity_path_entry(namespace.directory, namespace.name))
        elif namespace.operation == "read-cask-binding":
            print(read_cask_binding(namespace.name, namespace.expected))
        elif namespace.operation == "cleanup-current":
            cleanup_current_workdir(
                parent=namespace.parent,
                name=namespace.name,
                parent_identity=namespace.parent_identity,
                work_identity=namespace.work_identity,
                owned_values=namespace.owned,
            )
        else:  # pragma: no cover - argparse owns this invariant.
            raise AssertionError("unknown release support operation")
    except (OSError, ValueError) as error:
        print(f"macos_release_support: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
