"""Descriptor-anchored recursive deletion for validated generated data."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


_CLOSE_ON_EXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOSE_ON_EXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | _CLOSE_ON_EXEC
_WRITE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOSE_ON_EXEC
)


class SafeDeleteError(RuntimeError):
    """Raised when a deletion target changes or escapes its validated path."""


@dataclass(frozen=True)
class _Anchor:
    parent_fd: int
    name: str
    directory_fd: int
    device: int
    inode: int
    path: Path


def _identity(stat_result: os.stat_result) -> tuple[int, int]:
    return stat_result.st_dev, stat_result.st_ino


def _open_anchor(parent_fd: int, name: str, path: Path) -> _Anchor:
    try:
        directory_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise SafeDeleteError(f"unsafe directory while deleting {path}: {exc}") from exc
    try:
        stat_result = os.fstat(directory_fd)
        if not stat.S_ISDIR(stat_result.st_mode):
            raise SafeDeleteError(f"delete anchor is not a directory: {path}")
        return _Anchor(
            parent_fd=parent_fd,
            name=name,
            directory_fd=directory_fd,
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
            path=path,
        )
    except BaseException:
        os.close(directory_fd)
        raise


def _validate_anchors(anchors: tuple[_Anchor, ...]) -> None:
    for anchor in anchors:
        try:
            namespace_stat = os.stat(
                anchor.name,
                dir_fd=anchor.parent_fd,
                follow_symlinks=False,
            )
            descriptor_stat = os.fstat(anchor.directory_fd)
        except OSError as exc:
            raise SafeDeleteError(
                f"delete anchor changed during cleanup: {anchor.path}"
            ) from exc
        expected = (anchor.device, anchor.inode)
        if (
            not stat.S_ISDIR(namespace_stat.st_mode)
            or _identity(namespace_stat) != expected
            or _identity(descriptor_stat) != expected
        ):
            raise SafeDeleteError(
                f"delete anchor changed during cleanup: {anchor.path}"
            )


@contextmanager
def _open_parent_no_follow(
    path: Path,
) -> Iterator[tuple[int, tuple[_Anchor, ...]]]:
    if not path.is_absolute() or path == Path("/") or path.name in {"", ".", ".."}:
        raise SafeDeleteError(f"delete target must be a bounded absolute path: {path}")

    root_fd = os.open("/", _DIRECTORY_FLAGS)
    opened = [root_fd]
    anchors: list[_Anchor] = []
    current_fd = root_fd
    current_path = Path("/")
    try:
        for component in path.parent.parts[1:]:
            if component in {"", ".", ".."}:
                raise SafeDeleteError(f"unsafe delete path component: {path}")
            current_path /= component
            anchor = _open_anchor(current_fd, component, current_path)
            anchors.append(anchor)
            opened.append(anchor.directory_fd)
            current_fd = anchor.directory_fd
        anchor_tuple = tuple(anchors)
        _validate_anchors(anchor_tuple)
        yield current_fd, anchor_tuple
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _delete_regular_file(parent_fd: int, name: str, path: Path) -> None:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        file_fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise SafeDeleteError(f"cannot anchor generated file {path}: {exc}") from exc
    try:
        if _identity(os.fstat(file_fd)) != _identity(before):
            raise SafeDeleteError(f"generated file changed during cleanup: {path}")
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or _identity(current) != _identity(before):
            raise SafeDeleteError(f"generated file changed during cleanup: {path}")
        os.unlink(name, dir_fd=parent_fd)
    except OSError as exc:
        raise SafeDeleteError(f"cannot delete generated file {path}: {exc}") from exc
    finally:
        os.close(file_fd)


def _empty_directory(directory_fd: int, path: Path) -> None:
    try:
        names = tuple(os.listdir(directory_fd))
    except OSError as exc:
        raise SafeDeleteError(f"cannot read generated directory {path}: {exc}") from exc
    for name in names:
        child_path = path / name
        try:
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot inspect generated entry {child_path}: {exc}"
            ) from exc
        if stat.S_ISDIR(before.st_mode):
            child = _open_anchor(directory_fd, name, child_path)
            try:
                if _identity(os.fstat(child.directory_fd)) != _identity(before):
                    raise SafeDeleteError(
                        f"generated directory changed during cleanup: {child_path}"
                    )
                _empty_directory(child.directory_fd, child_path)
                current = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or _identity(current) != _identity(before)
                ):
                    raise SafeDeleteError(
                        f"generated directory changed during cleanup: {child_path}"
                    )
                os.rmdir(name, dir_fd=directory_fd)
            except OSError as exc:
                raise SafeDeleteError(
                    f"cannot delete generated directory {child_path}: {exc}"
                ) from exc
            finally:
                os.close(child.directory_fd)
        elif stat.S_ISLNK(before.st_mode):
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError as exc:
                raise SafeDeleteError(
                    f"cannot unlink generated symlink {child_path}: {exc}"
                ) from exc
        elif stat.S_ISREG(before.st_mode):
            _delete_regular_file(directory_fd, name, child_path)
        else:
            raise SafeDeleteError(f"unsupported generated entry type: {child_path}")


def delete_directory_tree(
    path: Path,
    *,
    allow_final_symlink: bool = False,
    before_mutation: Callable[[], None] | None = None,
) -> None:
    """Delete one directory without following ancestor or child symlinks."""

    target = path.absolute()
    with _open_parent_no_follow(target) as (parent_fd, anchors):
        try:
            before = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot inspect delete target {target}: {exc}"
            ) from exc

        if before_mutation is not None:
            before_mutation()
        _validate_anchors(anchors)

        try:
            current = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SafeDeleteError(
                f"delete target changed during cleanup: {target}"
            ) from exc
        if stat.S_IFMT(current.st_mode) != stat.S_IFMT(before.st_mode) or (
            _identity(current) != _identity(before)
        ):
            raise SafeDeleteError(f"delete target changed during cleanup: {target}")

        if stat.S_ISLNK(current.st_mode):
            if not allow_final_symlink:
                raise SafeDeleteError(f"delete target is a symlink: {target}")
            try:
                os.unlink(target.name, dir_fd=parent_fd)
            except OSError as exc:
                raise SafeDeleteError(
                    f"cannot unlink delete target {target}: {exc}"
                ) from exc
            _validate_anchors(anchors)
            return
        if not stat.S_ISDIR(current.st_mode):
            raise SafeDeleteError(f"delete target is not a directory: {target}")
        if current.st_uid != os.geteuid():
            raise SafeDeleteError(f"delete target is not owned by this user: {target}")

        directory = _open_anchor(parent_fd, target.name, target)
        try:
            if _identity(os.fstat(directory.directory_fd)) != _identity(current):
                raise SafeDeleteError(f"delete target changed during cleanup: {target}")
            _empty_directory(directory.directory_fd, target)
            _validate_anchors(anchors)
            final = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(final.st_mode)
                or _identity(final) != _identity(current)
            ):
                raise SafeDeleteError(f"delete target changed during cleanup: {target}")
            os.rmdir(target.name, dir_fd=parent_fd)
            _validate_anchors(anchors)
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot delete generated directory {target}: {exc}"
            ) from exc
        finally:
            os.close(directory.directory_fd)


def _read_all(file_fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(file_fd, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_all(file_fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(file_fd, content[offset:])
        if written <= 0:
            raise SafeDeleteError("short write while sanitizing generated JSON")
        offset += written


def _stable_file_identity(
    stat_result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def read_file_bytes_no_follow(
    path: Path,
    *,
    before_read: Callable[[], None] | None = None,
) -> bytes:
    """Read a regular file through pinned descriptors without symlinks."""

    target = path.absolute()
    with _open_parent_no_follow(target) as (parent_fd, anchors):
        try:
            before = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(before.st_mode):
                raise SafeDeleteError(f"file is a symlink: {target}")
            if not stat.S_ISREG(before.st_mode):
                raise SafeDeleteError(f"file is not regular: {target}")
            file_fd = os.open(target.name, _FILE_FLAGS, dir_fd=parent_fd)
        except SafeDeleteError:
            raise
        except OSError as exc:
            raise SafeDeleteError(f"cannot anchor JSON file {target}: {exc}") from exc
        try:
            descriptor_before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or _identity(descriptor_before) != _identity(before)
            ):
                raise SafeDeleteError(f"file changed while reading: {target}")
            if before_read is not None:
                before_read()
            _validate_anchors(anchors)
            namespace_current = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(namespace_current.st_mode)
                or _identity(namespace_current) != _identity(before)
            ):
                raise SafeDeleteError(f"file changed while reading: {target}")
            content = _read_all(file_fd)
            descriptor_after = os.fstat(file_fd)
            namespace_after = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                _stable_file_identity(descriptor_after)
                != _stable_file_identity(descriptor_before)
                or _identity(namespace_after) != _identity(before)
            ):
                raise SafeDeleteError(f"file changed while reading: {target}")
        except OSError as exc:
            raise SafeDeleteError(f"cannot safely read file {target}: {exc}") from exc
        finally:
            os.close(file_fd)
    return content


def read_json_file_no_follow(
    path: Path,
    *,
    before_read: Callable[[], None] | None = None,
) -> object:
    """Read JSON through pinned descriptors without following symlinks."""

    target = path.absolute()
    try:
        return json.loads(
            read_file_bytes_no_follow(
                target,
                before_read=before_read,
            ).decode("utf-8")
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SafeDeleteError(f"invalid JSON file {target}: {exc}") from exc


def directory_is_empty_no_follow(
    path: Path,
    *,
    before_read: Callable[[], None] | None = None,
    after_read: Callable[[], None] | None = None,
) -> bool:
    """Check emptiness while pinning every ancestor and the directory."""

    target = path.absolute()
    with _open_parent_no_follow(target) as (parent_fd, anchors):
        try:
            before = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot inspect directory {target}: {exc}"
            ) from exc
        if not stat.S_ISDIR(before.st_mode):
            raise SafeDeleteError(f"path is not a directory: {target}")
        directory = _open_anchor(parent_fd, target.name, target)
        try:
            descriptor_before = os.fstat(directory.directory_fd)
            if _identity(descriptor_before) != _identity(before):
                raise SafeDeleteError(f"directory changed while reading: {target}")
            if before_read is not None:
                before_read()
            _validate_anchors(anchors)
            namespace_current = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(namespace_current.st_mode)
                or _identity(namespace_current) != _identity(before)
            ):
                raise SafeDeleteError(f"directory changed while reading: {target}")
            empty = not os.listdir(directory.directory_fd)
            if after_read is not None:
                after_read()
            descriptor_after = os.fstat(directory.directory_fd)
            namespace_after = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                _stable_file_identity(descriptor_after)
                != _stable_file_identity(descriptor_before)
                or not stat.S_ISDIR(namespace_after.st_mode)
                or _identity(namespace_after) != _identity(before)
            ):
                raise SafeDeleteError(f"directory changed while reading: {target}")
            return empty
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot safely read directory {target}: {exc}"
            ) from exc
        finally:
            os.close(directory.directory_fd)


def _digest_field(digest: object, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _hash_tree_entry(
    parent_fd: int,
    name: str,
    path: Path,
    digest: object,
    *,
    after_read: Callable[[], None] | None = None,
) -> None:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise SafeDeleteError(f"cannot inspect preserved path {path}: {exc}") from exc

    _digest_field(digest, os.fsencode(name))
    _digest_field(digest, str(stat.S_IMODE(before.st_mode)).encode())
    _digest_field(digest, str(before.st_uid).encode())
    _digest_field(digest, str(before.st_gid).encode())
    _digest_field(digest, str(before.st_size).encode())

    if stat.S_ISDIR(before.st_mode):
        _digest_field(digest, b"directory")
        directory = _open_anchor(parent_fd, name, path)
        try:
            descriptor_before = os.fstat(directory.directory_fd)
            if (
                _stable_file_identity(descriptor_before)
                != _stable_file_identity(before)
            ):
                raise SafeDeleteError(f"preserved directory changed: {path}")
            try:
                names = sorted(os.listdir(directory.directory_fd))
            except OSError as exc:
                raise SafeDeleteError(
                    f"cannot read preserved directory {path}: {exc}"
                ) from exc
            _digest_field(digest, str(len(names)).encode())
            for child_name in names:
                _hash_tree_entry(
                    directory.directory_fd,
                    child_name,
                    path / child_name,
                    digest,
                )
            if after_read is not None:
                after_read()
            descriptor_after = os.fstat(directory.directory_fd)
            namespace_after = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                _stable_file_identity(descriptor_after)
                != _stable_file_identity(descriptor_before)
                or _stable_file_identity(namespace_after)
                != _stable_file_identity(before)
            ):
                raise SafeDeleteError(f"preserved directory changed: {path}")
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot safely inspect preserved directory {path}: {exc}"
            ) from exc
        finally:
            os.close(directory.directory_fd)
        return

    if stat.S_ISREG(before.st_mode):
        _digest_field(digest, b"file")
        try:
            file_fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise SafeDeleteError(f"cannot open preserved file {path}: {exc}") from exc
        try:
            descriptor_before = os.fstat(file_fd)
            if (
                _stable_file_identity(descriptor_before)
                != _stable_file_identity(before)
            ):
                raise SafeDeleteError(f"preserved file changed: {path}")
            content_digest = hashlib.sha256()
            while True:
                chunk = os.read(file_fd, 64 * 1024)
                if not chunk:
                    break
                content_digest.update(chunk)
            _digest_field(digest, content_digest.digest())
            if after_read is not None:
                after_read()
            descriptor_after = os.fstat(file_fd)
            namespace_after = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                _stable_file_identity(descriptor_after)
                != _stable_file_identity(descriptor_before)
                or _stable_file_identity(namespace_after)
                != _stable_file_identity(before)
            ):
                raise SafeDeleteError(f"preserved file changed: {path}")
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot safely inspect preserved file {path}: {exc}"
            ) from exc
        finally:
            os.close(file_fd)
        return

    if stat.S_ISLNK(before.st_mode):
        _digest_field(digest, b"symlink")
        try:
            target = os.readlink(name, dir_fd=parent_fd)
            if after_read is not None:
                after_read()
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot inspect preserved symlink {path}: {exc}"
            ) from exc
        if _stable_file_identity(current) != _stable_file_identity(before):
            raise SafeDeleteError(f"preserved symlink changed: {path}")
        _digest_field(digest, os.fsencode(target))
        return

    raise SafeDeleteError(f"unsupported preserved path type: {path}")


def tree_digest_no_follow(
    path: Path,
    *,
    after_read: Callable[[], None] | None = None,
) -> str:
    """Hash a file tree without following links and reject concurrent changes."""

    target = path.absolute()
    digest = hashlib.sha256()
    with _open_parent_no_follow(target) as (parent_fd, anchors):
        _validate_anchors(anchors)
        _hash_tree_entry(
            parent_fd,
            target.name,
            target,
            digest,
            after_read=after_read,
        )
        _validate_anchors(anchors)
    return digest.hexdigest()


def remove_json_object_key(
    path: Path,
    *,
    key: str,
    before_mutation: Callable[[], None] | None = None,
) -> bool:
    """Atomically remove one generated top-level key from a JSON object."""

    target = path.absolute()
    with _open_parent_no_follow(target) as (parent_fd, anchors):
        try:
            before = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            file_fd = os.open(target.name, _FILE_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise SafeDeleteError(
                f"cannot anchor generated JSON {target}: {exc}"
            ) from exc
        try:
            descriptor_before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or (
                _stable_file_identity(descriptor_before)
                != _stable_file_identity(before)
            ):
                raise SafeDeleteError(
                    f"generated JSON changed during cleanup: {target}"
                )
            if before.st_uid != os.geteuid():
                raise SafeDeleteError(
                    f"generated JSON is not owned by this user: {target}"
                )
            try:
                payload = json.loads(_read_all(file_fd).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise SafeDeleteError(
                    f"invalid generated JSON {target}: {exc}"
                ) from exc
            if (
                _stable_file_identity(os.fstat(file_fd))
                != _stable_file_identity(descriptor_before)
            ):
                raise SafeDeleteError(
                    f"generated JSON changed during cleanup: {target}"
                )
        finally:
            os.close(file_fd)

        if not isinstance(payload, dict):
            raise SafeDeleteError(f"generated JSON is not an object: {target}")
        if key not in payload:
            return False
        if key == "projects" and not isinstance(payload[key], dict):
            raise SafeDeleteError(
                f"generated JSON projects value is not an object: {target}"
            )
        del payload[key]
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        if before_mutation is not None:
            before_mutation()
        _validate_anchors(anchors)
        try:
            current = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SafeDeleteError(
                f"generated JSON changed during cleanup: {target}"
            ) from exc
        if not stat.S_ISREG(current.st_mode) or (
            _stable_file_identity(current) != _stable_file_identity(before)
        ):
            raise SafeDeleteError(f"generated JSON changed during cleanup: {target}")

        temporary_name = f".dotsync-reset-{secrets.token_hex(16)}"
        temporary_fd: int | None = None
        try:
            temporary_fd = os.open(
                temporary_name,
                _WRITE_FLAGS,
                stat.S_IMODE(before.st_mode),
                dir_fd=parent_fd,
            )
            _write_all(temporary_fd, content)
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            _validate_anchors(anchors)
            current = os.stat(
                target.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(current.st_mode)
                or _stable_file_identity(current)
                != _stable_file_identity(before)
            ):
                raise SafeDeleteError(
                    f"generated JSON changed during cleanup: {target}"
                )
            os.rename(
                temporary_name,
                target.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            _validate_anchors(anchors)
        except (OSError, SafeDeleteError) as exc:
            if isinstance(exc, SafeDeleteError):
                raise
            raise SafeDeleteError(
                f"cannot sanitize generated JSON {target}: {exc}"
            ) from exc
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return True
