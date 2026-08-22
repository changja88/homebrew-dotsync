"""Descriptor-anchored persistence helpers for DotSync-owned private state."""

from __future__ import annotations

import errno
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class UnsafePrivatePath(ValueError):
    """Raised when a private filesystem operation would leave its boundary."""


class PrivateAtomicWriteUncertain(OSError):
    """Raised when replacement occurred but directory durability is uncertain."""


@dataclass(frozen=True)
class PrivateDirectoryIdentity:
    """Stable filesystem identity for one validated private directory."""

    device: int
    inode: int


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


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


def _parts(path: Path) -> list[str]:
    return list(path.parts[1:])


def _relative_parts(path: Path, root: Path) -> tuple[list[str], list[str]]:
    target, private_root = _within_root(path, root)
    root_parts = _parts(private_root)
    target_parts = _parts(target)
    relative = target_parts[len(root_parts) :]
    if not relative:
        raise UnsafePrivatePath("private file or tree must be below its root")
    return root_parts, relative


def _entry_metadata(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _open_error(error: OSError, parent_fd: int, name: str) -> UnsafePrivatePath | OSError:
    metadata = _entry_metadata(parent_fd, name)
    if metadata is not None and stat.S_ISLNK(metadata.st_mode):
        return UnsafePrivatePath(f"symlink is not allowed in private path: {name}")
    if error.errno == errno.ELOOP:
        return UnsafePrivatePath(f"symlink is not allowed in private path: {name}")
    if error.errno == errno.ENOTDIR:
        return UnsafePrivatePath(f"private path is not a directory: {name}")
    return error


def _open_directory_at(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise _open_error(error, parent_fd, name) from error


def _open_directory(
    parts: list[str], *, create: bool, managed_start: int | None
) -> int:
    """Open a directory path from ``/`` without following any component link."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, name in enumerate(parts):
            try:
                child = _open_directory_at(descriptor, name)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = _open_directory_at(descriptor, name)

            if managed_start is not None and index >= managed_start:
                os.fchmod(child, 0o700)
                if stat.S_IMODE(os.fstat(child).st_mode) != 0o700:
                    os.close(child)
                    raise UnsafePrivatePath("private directory has unsafe permissions")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_managed_directory(path: Path, *, root: Path, create: bool) -> int:
    directory, private_root = _within_root(path, root)
    root_parts = _parts(private_root)
    return _open_directory(
        _parts(directory),
        create=create,
        managed_start=len(root_parts) - 1 if root_parts else None,
    )


def ensure_private_dir(path: Path, *, root: Path) -> None:
    """Create a private directory tree with mode ``0700`` without symlinks."""
    descriptor = _open_managed_directory(path, root=root, create=True)
    os.close(descriptor)


def ensure_private_root_identity(path: Path) -> PrivateDirectoryIdentity:
    """Create/validate a private root and return its no-follow inode identity."""
    descriptor = _open_managed_directory(path, root=path, create=True)
    try:
        metadata = os.fstat(descriptor)
        return PrivateDirectoryIdentity(metadata.st_dev, metadata.st_ino)
    finally:
        os.close(descriptor)


def fsync_private_directory(path: Path, *, root: Path) -> None:
    """Durably flush one descriptor-validated private directory."""
    descriptor = _open_managed_directory(path, root=root, create=False)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_regular_target(parent_fd: int, name: str) -> None:
    metadata = _entry_metadata(parent_fd, name)
    if metadata is None:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafePrivatePath(f"symlink is not allowed in private path: {name}")
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafePrivatePath(f"private path is not a regular file: {name}")


def _create_sibling_file(parent_fd: int, target_name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(100):
        temporary_name = f".{target_name}.{secrets.token_hex(16)}"
        try:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise FileExistsError("could not create a private temporary file")


def atomic_write_json(path: Path, data: Any, *, root: Path) -> None:
    """Atomically persist JSON as a private ``0600`` regular file."""
    root_parts, relative = _relative_parts(path, root)
    target_name = relative[-1]
    parent_fd = _open_directory(
        [*root_parts, *relative[:-1]],
        create=True,
        managed_start=len(root_parts) - 1 if root_parts else None,
    )
    temporary_name: str | None = None
    try:
        _validate_regular_target(parent_fd, target_name)
        temporary_fd, temporary_name = _create_sibling_file(parent_fd, target_name)
        try:
            os.fchmod(temporary_fd, 0o600)
            with os.fdopen(temporary_fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(
                temporary_name,
                target_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temporary_name = None
            try:
                os.fsync(parent_fd)
            except OSError:
                raise PrivateAtomicWriteUncertain(
                    "private JSON replacement durability is uncertain"
                ) from None
            final_fd = os.open(target_name, _READ_FILE_FLAGS, dir_fd=parent_fd)
            try:
                final_metadata = os.fstat(final_fd)
                if not stat.S_ISREG(final_metadata.st_mode):
                    raise UnsafePrivatePath("private path is not a regular file")
                if stat.S_IMODE(final_metadata.st_mode) != 0o600:
                    raise UnsafePrivatePath("private file has unsafe permissions")
            finally:
                os.close(final_fd)
        except BaseException:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            raise
    finally:
        os.close(parent_fd)


def read_private_json(path: Path, *, root: Path) -> Any:
    """Read a regular JSON file through an anchored no-follow file descriptor."""
    root_parts, relative = _relative_parts(path, root)
    parent_fd = _open_directory(
        [*root_parts, *relative[:-1]],
        create=False,
        managed_start=None,
    )
    try:
        target_name = relative[-1]
        try:
            descriptor = os.open(target_name, _READ_FILE_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise _open_error(error, parent_fd, target_name) from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise UnsafePrivatePath("private path is not a regular file")
            with os.fdopen(descriptor, encoding="utf-8") as file:
                return json.load(file)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    finally:
        os.close(parent_fd)


@dataclass
class _ScannedDirectory:
    descriptor: int | None
    files: list[str] = field(default_factory=list)
    directories: list[tuple[str, "_ScannedDirectory"]] = field(default_factory=list)


def _close_tree(tree: _ScannedDirectory) -> None:
    for _, child in tree.directories:
        _close_tree(child)
    if tree.descriptor is not None:
        os.close(tree.descriptor)
        tree.descriptor = None


def _scan_tree(descriptor: int) -> _ScannedDirectory:
    tree = _ScannedDirectory(descriptor)
    try:
        with os.scandir(os.dup(descriptor)) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise UnsafePrivatePath(
                        f"symlink is not allowed in private path: {entry.name}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    child = _open_directory_at(descriptor, entry.name)
                    tree.directories.append((entry.name, _scan_tree(child)))
                elif entry.is_file(follow_symlinks=False):
                    tree.files.append(entry.name)
                else:
                    raise UnsafePrivatePath(
                        f"unsupported private tree entry: {entry.name}"
                    )
        return tree
    except BaseException:
        _close_tree(tree)
        raise


def _remove_scanned_tree(tree: _ScannedDirectory) -> None:
    assert tree.descriptor is not None
    try:
        for name in tree.files:
            os.unlink(name, dir_fd=tree.descriptor)
        for name, child in tree.directories:
            _remove_scanned_tree(child)
            os.rmdir(name, dir_fd=tree.descriptor)
    finally:
        os.close(tree.descriptor)
        tree.descriptor = None


def validate_private_tree(path: Path, *, allowed_root: Path) -> bool:
    """Validate a regular private tree without following links or mutating it."""
    root_parts, relative = _relative_parts(path, allowed_root)
    try:
        parent_fd = _open_directory(
            [*root_parts, *relative[:-1]],
            create=False,
            managed_start=None,
        )
    except FileNotFoundError:
        return False
    tree: _ScannedDirectory | None = None
    try:
        try:
            target_fd = _open_directory_at(parent_fd, relative[-1])
        except FileNotFoundError:
            return False
        tree = _scan_tree(target_fd)
        return True
    finally:
        if tree is not None:
            _close_tree(tree)
        os.close(parent_fd)


def _tree_matches_fresh_validation(
    parent_fd: int,
    name: str,
    expected_metadata: os.stat_result,
) -> bool:
    """Return true only for a freshly scanned tree with the expected identity."""
    target_fd: int | None = None
    tree: _ScannedDirectory | None = None
    try:
        current_metadata = _entry_metadata(parent_fd, name)
        if (
            current_metadata is None
            or not stat.S_ISDIR(current_metadata.st_mode)
            or (current_metadata.st_dev, current_metadata.st_ino)
            != (expected_metadata.st_dev, expected_metadata.st_ino)
        ):
            return False
        target_fd = _open_directory_at(parent_fd, name)
        opened_metadata = os.fstat(target_fd)
        if (opened_metadata.st_dev, opened_metadata.st_ino) != (
            expected_metadata.st_dev,
            expected_metadata.st_ino,
        ):
            return False
        scan_fd = target_fd
        target_fd = None
        tree = _scan_tree(scan_fd)
        final_metadata = _entry_metadata(parent_fd, name)
        return bool(
            final_metadata is not None
            and stat.S_ISDIR(final_metadata.st_mode)
            and (final_metadata.st_dev, final_metadata.st_ino)
            == (expected_metadata.st_dev, expected_metadata.st_ino)
        )
    except BaseException:
        return False
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if tree is not None:
            _close_tree(tree)


def move_private_tree(
    source: Path,
    destination: Path,
    *,
    allowed_root: Path,
) -> bool:
    """Atomically move one validated tree within a descriptor-anchored root."""
    absolute_source, private_root = _within_root(source, allowed_root)
    absolute_destination, _ = _within_root(destination, allowed_root)
    if absolute_source == absolute_destination:
        raise UnsafePrivatePath("private tree source and destination must differ")
    try:
        absolute_destination.relative_to(absolute_source)
    except ValueError:
        pass
    else:
        raise UnsafePrivatePath("private tree destination cannot be inside source")

    root_parts = _parts(private_root)
    source_relative = list(absolute_source.relative_to(private_root).parts)
    destination_relative = list(absolute_destination.relative_to(private_root).parts)
    if not source_relative or not destination_relative:
        raise UnsafePrivatePath(
            "private tree must be a strict descendant of allowed_root"
        )

    try:
        source_parent_fd = _open_directory(
            [*root_parts, *source_relative[:-1]],
            create=False,
            managed_start=None,
        )
    except FileNotFoundError:
        return False
    destination_parent_fd: int | None = None
    tree: _ScannedDirectory | None = None
    moved = False
    try:
        try:
            source_fd = _open_directory_at(source_parent_fd, source_relative[-1])
        except FileNotFoundError:
            return False
        tree = _scan_tree(source_fd)
        assert tree.descriptor is not None
        scanned_metadata = os.fstat(tree.descriptor)

        destination_parent_fd = _open_directory(
            [*root_parts, *destination_relative[:-1]],
            create=True,
            managed_start=len(root_parts) - 1 if root_parts else None,
        )
        if _entry_metadata(destination_parent_fd, destination_relative[-1]) is not None:
            raise UnsafePrivatePath("private tree destination already exists")

        current_metadata = _entry_metadata(source_parent_fd, source_relative[-1])
        if (
            current_metadata is None
            or not stat.S_ISDIR(current_metadata.st_mode)
            or (current_metadata.st_dev, current_metadata.st_ino)
            != (scanned_metadata.st_dev, scanned_metadata.st_ino)
        ):
            raise UnsafePrivatePath("private tree changed during validation")

        os.rename(
            source_relative[-1],
            destination_relative[-1],
            src_dir_fd=source_parent_fd,
            dst_dir_fd=destination_parent_fd,
        )
        moved = True
        destination_metadata = _entry_metadata(
            destination_parent_fd,
            destination_relative[-1],
        )
        if (
            destination_metadata is None
            or not stat.S_ISDIR(destination_metadata.st_mode)
            or (destination_metadata.st_dev, destination_metadata.st_ino)
            != (scanned_metadata.st_dev, scanned_metadata.st_ino)
        ):
            raise UnsafePrivatePath("private tree move could not be verified")
        moved_fd = _open_directory_at(
            destination_parent_fd,
            destination_relative[-1],
        )
        moved_tree = _scan_tree(moved_fd)
        _close_tree(moved_tree)
        os.fsync(source_parent_fd)
        if destination_parent_fd != source_parent_fd:
            os.fsync(destination_parent_fd)
        return True
    except BaseException as error:
        if moved and destination_parent_fd is not None:
            destination_is_valid = _tree_matches_fresh_validation(
                destination_parent_fd,
                destination_relative[-1],
                scanned_metadata,
            )
            if isinstance(error, Exception):
                detail = "interrupted" if destination_is_valid else "unsafe"
                raise UnsafePrivatePath(
                    f"private tree move {detail}; destination left quarantined"
                ) from None
        raise
    finally:
        if tree is not None:
            _close_tree(tree)
        if destination_parent_fd is not None:
            os.close(destination_parent_fd)
        os.close(source_parent_fd)


def remove_empty_private_directory(path: Path, *, allowed_root: Path) -> bool:
    """Remove only a descriptor-validated empty private directory."""
    target, root = _within_root(path, allowed_root)
    if target == root:
        raise UnsafePrivatePath("private directory must be below its root")
    root_parts, relative = _relative_parts(path, allowed_root)
    try:
        parent_fd = _open_directory(
            [*root_parts, *relative[:-1]],
            create=False,
            managed_start=None,
        )
    except FileNotFoundError:
        return False
    target_fd: int | None = None
    try:
        try:
            target_fd = _open_directory_at(parent_fd, relative[-1])
        except FileNotFoundError:
            return False
        expected_metadata = os.fstat(target_fd)
        with os.scandir(os.dup(target_fd)) as entries:
            if next(entries, None) is not None:
                raise UnsafePrivatePath("private directory is not empty")
        current_metadata = _entry_metadata(parent_fd, relative[-1])
        if (
            current_metadata is None
            or not stat.S_ISDIR(current_metadata.st_mode)
            or (current_metadata.st_dev, current_metadata.st_ino)
            != (expected_metadata.st_dev, expected_metadata.st_ino)
        ):
            raise UnsafePrivatePath("private directory changed during validation")
        os.rmdir(relative[-1], dir_fd=parent_fd)
        return True
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(parent_fd)


def remove_private_tree(path: Path, *, allowed_root: Path) -> None:
    """Remove a regular private tree only after a descriptor-anchored scan."""
    target, root = _within_root(path, allowed_root)
    if target == root:
        raise UnsafePrivatePath("private tree must be a strict descendant of allowed_root")
    root_parts, relative = _relative_parts(path, allowed_root)
    parent_fd = _open_directory(
        [*root_parts, *relative[:-1]],
        create=False,
        managed_start=None,
    )
    target_fd: int | None = None
    tree: _ScannedDirectory | None = None
    try:
        try:
            target_fd = _open_directory_at(parent_fd, relative[-1])
        except FileNotFoundError:
            return
        scan_fd = target_fd
        target_fd = None
        tree = _scan_tree(scan_fd)
        try:
            _remove_scanned_tree(tree)
        except BaseException:
            _close_tree(tree)
            raise
        os.rmdir(relative[-1], dir_fd=parent_fd)
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if tree is not None:
            _close_tree(tree)
        os.close(parent_fd)
