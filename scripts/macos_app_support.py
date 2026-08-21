#!/usr/bin/env python3
"""Fail-closed helpers for assembling the local macOS application bundle."""

from __future__ import annotations

import errno
import os
import re
import secrets
import stat
import sys
import tomllib
from pathlib import Path


SEMANTIC_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
QUERY_CAPABILITY = re.compile(
    rb"(?:^|[?&])token=[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"
)
JSON_CAPABILITY = re.compile(
    rb'(?<![A-Za-z0-9_])"(?:token|capability)"[ \t\r\n]*:'
    rb'[ \t\r\n]*"[A-Za-z0-9_-]{43}"'
)
PROVIDER_HOME_COMPONENT = re.compile(
    rb'(?:^|/)\.(?:codex|claude(?:\.json)?)(?=$|[/\x00\s"\'=:,}\]])'
)


class PackagingError(Exception):
    """An input does not satisfy the local packaging contract."""


def project_version(pyproject_path: Path) -> str:
    try:
        with pyproject_path.open("rb") as pyproject_file:
            document = tomllib.load(pyproject_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PackagingError("pyproject.toml could not be read") from error

    project = document.get("project")
    if not isinstance(project, dict):
        raise PackagingError("pyproject.toml has no project table")

    version = project.get("version")
    if type(version) is not str or SEMANTIC_VERSION.fullmatch(version) is None:
        raise PackagingError("project.version must be an exact semantic version")
    return version


def _remove_directory_contents(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                _remove_directory_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _validate_child_name(child_name: str) -> None:
    if child_name in {"", ".", ".."} or Path(child_name).name != child_name:
        raise PackagingError("cleanup target is not a direct child")


def _remove_direct_child_at(root_fd: int, child_name: str) -> None:
    try:
        child_stat = os.stat(child_name, dir_fd=root_fd, follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return
        raise
    if not stat.S_ISDIR(child_stat.st_mode):
        raise PackagingError("cleanup target must be a real directory")
    child_fd = os.open(
        child_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=root_fd,
    )
    try:
        if not _same_identity(child_stat, os.fstat(child_fd)):
            raise PackagingError("cleanup target changed during verification")
        _remove_directory_contents(child_fd)
    finally:
        os.close(child_fd)
    os.rmdir(child_name, dir_fd=root_fd)


def remove_direct_child(root_path: Path, child_name: str) -> None:
    _validate_child_name(child_name)

    try:
        root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise PackagingError("build directory identity could not be verified") from error

    try:
        _remove_direct_child_at(root_fd, child_name)
    except OSError as error:
        raise PackagingError("cleanup target could not be removed safely") from error
    finally:
        os.close(root_fd)


def publish_app(root_path: Path, staging_name: str, final_name: str) -> None:
    _validate_child_name(staging_name)
    _validate_child_name(final_name)
    previous_name = f".dotsync-app-previous.{secrets.token_hex(8)}"

    try:
        root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise PackagingError("build directory identity could not be verified") from error

    moved_previous = False
    moved_new = False
    try:
        staging_stat = os.stat(staging_name, dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(staging_stat.st_mode):
            raise PackagingError("staging target must be a real directory")
        staging_fd = os.open(
            staging_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        try:
            if not _same_identity(staging_stat, os.fstat(staging_fd)):
                raise PackagingError("staging target changed during verification")
            app_stat = os.stat(final_name, dir_fd=staging_fd, follow_symlinks=False)
            if not stat.S_ISDIR(app_stat.st_mode):
                raise PackagingError("staged app must be a real directory")

            try:
                existing_stat = os.stat(
                    final_name,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                if error.errno != errno.ENOENT:
                    raise
            else:
                if not stat.S_ISDIR(existing_stat.st_mode):
                    raise PackagingError("existing app must be a real directory")
                os.rename(
                    final_name,
                    previous_name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                )
                moved_previous = True

            os.rename(
                final_name,
                final_name,
                src_dir_fd=staging_fd,
                dst_dir_fd=root_fd,
            )
            moved_new = True
        finally:
            os.close(staging_fd)

        if moved_previous:
            _remove_direct_child_at(root_fd, previous_name)
            moved_previous = False
    except (OSError, PackagingError) as error:
        try:
            if moved_new:
                _remove_direct_child_at(root_fd, final_name)
                moved_new = False
            if moved_previous:
                os.rename(
                    previous_name,
                    final_name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                )
                moved_previous = False
        except (OSError, PackagingError) as rollback_error:
            raise PackagingError("app publication rollback failed") from rollback_error
        if isinstance(error, PackagingError):
            raise
        raise PackagingError("app could not be published safely") from error
    finally:
        os.close(root_fd)


def _same_identity(expected: os.stat_result, actual: os.stat_result) -> bool:
    return (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino)


def _file_contains_leak(file_fd: int, checkout: bytes) -> bool:
    payload = bytearray()
    while True:
        block = os.read(file_fd, 1024 * 1024)
        if not block:
            break
        payload.extend(block)
    contents = bytes(payload)
    return (
        checkout in contents
        or QUERY_CAPABILITY.search(contents) is not None
        or JSON_CAPABILITY.search(contents) is not None
        or PROVIDER_HOME_COMPONENT.search(contents) is not None
    )


def _scan_directory(directory_fd: int, checkout: bytes) -> None:
    for name in os.listdir(directory_fd):
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise PackagingError("bundle contains a symlink")
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                if not _same_identity(entry_stat, os.fstat(child_fd)):
                    raise PackagingError("bundle changed during traversal")
                _scan_directory(child_fd, checkout)
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            raise PackagingError("bundle contains a special file")
        if entry_stat.st_mode & 0o444 == 0:
            raise PackagingError("bundle contains an unreadable file")
        file_fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            if not _same_identity(entry_stat, os.fstat(file_fd)):
                raise PackagingError("bundle changed during traversal")
            if _file_contains_leak(file_fd, checkout):
                raise PackagingError("bundle contains forbidden private data")
        finally:
            os.close(file_fd)


def scan_bundle(bundle_path: Path, checkout_path: Path) -> None:
    checkout = os.fsencode(checkout_path)
    if not checkout:
        raise PackagingError("checkout path is empty")
    try:
        bundle_fd = os.open(
            bundle_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            _scan_directory(bundle_fd, checkout)
        finally:
            os.close(bundle_fd)
    except PackagingError:
        raise
    except OSError as error:
        raise PackagingError("bundle could not be scanned safely") from error


def main(arguments: list[str]) -> int:
    try:
        if len(arguments) == 2 and arguments[0] == "version":
            print(project_version(Path(arguments[1])))
            return 0
        if len(arguments) == 3 and arguments[0] == "remove-child":
            remove_direct_child(Path(arguments[1]), arguments[2])
            return 0
        if len(arguments) == 4 and arguments[0] == "publish":
            publish_app(Path(arguments[1]), arguments[2], arguments[3])
            return 0
        if len(arguments) == 3 and arguments[0] == "scan":
            scan_bundle(Path(arguments[1]), Path(arguments[2]))
            return 0
        print("macos_app_support: invalid arguments", file=sys.stderr)
        return 2
    except PackagingError as error:
        print(f"macos_app_support: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
