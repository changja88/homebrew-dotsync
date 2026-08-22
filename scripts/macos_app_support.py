#!/usr/bin/env python3
"""Identity-bound helpers for assembling the local macOS application bundle."""

from __future__ import annotations

import codecs
import contextlib
import ctypes
import errno
import hashlib
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


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

BUILD_DIRECTORY = "build"
FINAL_APP = "DotSync.app"
ARM_SCRATCH = "swift-arm64"
X86_SCRATCH = "swift-x86_64"
PACKAGE_SNAPSHOT = "package"
PLIST_SNAPSHOT = "DotSync-Info.plist.in"
RENAME_EXCL = 0x00000004
RENAME_SWAP = 0x00000002
RENAME_NOFOLLOW_ANY = 0x00000010
RENAME_RESOLVE_BENEATH = 0x00000020
CAPTURED_STDOUT_LIMIT = 64 * 1024
CAPTURE_READER_GRACE_SECONDS = 1.0


class PackagingError(Exception):
    """A filesystem or build input violates the local packaging contract."""


class PublicationOwnershipError(PackagingError):
    """Publication rollback cannot prove ownership of the public binding."""


class SignalInterruption(Exception):
    """A termination signal requested normal cleanup unwinding."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


@dataclass(frozen=True)
class NodeIdentity:
    device: int
    inode: int

    @classmethod
    def from_stat(cls, node_stat: os.stat_result) -> NodeIdentity:
        return cls(device=node_stat.st_dev, inode=node_stat.st_ino)

    def matches(self, node_stat: os.stat_result) -> bool:
        return (node_stat.st_dev, node_stat.st_ino) == (self.device, self.inode)


@dataclass
class StagingDirectory:
    name: str
    descriptor: int
    identity: NodeIdentity
    removed: bool = False
    preserve: bool = False


@dataclass
class CreatedDirectory:
    name: str
    descriptor: int
    identity: NodeIdentity


@dataclass
class ScannedApp:
    descriptor: int
    identity: NodeIdentity
    manifest: tuple[ManifestEntry, ...]
    checkout: bytes


@dataclass(frozen=True)
class ManifestEntry:
    relative_path: tuple[str, ...]
    kind: str
    device: int
    inode: int
    link_count: int
    mode: int
    size: int
    modified_ns: int
    digest: bytes | None


@dataclass(frozen=True)
class InputSnapshot:
    package_manifest: tuple[ManifestEntry, ...]
    plist_manifest: ManifestEntry


def _validate_child_name(child_name: str) -> None:
    if child_name in {"", ".", ".."} or Path(child_name).name != child_name:
        raise PackagingError("target is not an exact direct child")


class SignalCoordinator:
    managed_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    termination_grace_seconds = 0.5

    def __init__(self) -> None:
        self.first_signum: int | None = None
        self._previous_handlers: dict[int, signal.Handlers] = {}
        self._active_process: subprocess.Popen[bytes] | None = None
        self._signal_received_at: float | None = None
        self._committed = False

    def __enter__(self) -> SignalCoordinator:
        for signum in self.managed_signals:
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._active_process = None
        if self.interrupted:
            # assemble() is the terminal supervisor operation. Keep later
            # managed signals pending while its retained 128+signal status is
            # handed to main, so a restored default handler cannot replace it.
            signal.pthread_sigmask(signal.SIG_BLOCK, self.managed_signals)
        for signum, previous_handler in self._previous_handlers.items():
            signal.signal(signum, previous_handler)

    @property
    def interrupted(self) -> bool:
        return self.first_signum is not None

    def _handle_signal(self, signum: int, _frame: object) -> None:
        if self._committed:
            return
        if self.first_signum is None:
            self.first_signum = signum
            self._signal_received_at = time.monotonic()
        process = self._active_process
        if process is not None:
            self._signal_process_group(process.pid, signum)

    def record_pending_signal(self) -> None:
        pending = signal.sigpending()
        if self.first_signum is None:
            for signum in self.managed_signals:
                if signum in pending:
                    self.first_signum = signum
                    self._signal_received_at = time.monotonic()
                    break

    @staticmethod
    def _signal_process_group(process_group: int, signum: int) -> None:
        try:
            os.killpg(process_group, signum)
        except ProcessLookupError:
            pass

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        if self._active_process is not None:
            raise PackagingError("build tools must run serially")
        self._active_process = process
        if self.first_signum is not None:
            self._signal_process_group(process.pid, self.first_signum)

    def detach(self, process: subprocess.Popen[bytes]) -> None:
        if self._active_process is process:
            self._active_process = None

    def should_escalate(self) -> bool:
        return (
            self._signal_received_at is not None
            and time.monotonic() - self._signal_received_at
            >= self.termination_grace_seconds
        )

    def quiesce_process_group(
        self,
        process_group: int,
        *,
        already_escalated: bool = False,
    ) -> None:
        if not self.interrupted:
            return
        if already_escalated:
            return
        assert self.first_signum is not None
        try:
            self._signal_process_group(process_group, self.first_signum)
        except PermissionError:
            return
        assert self._signal_received_at is not None
        remaining_grace = (
            self._signal_received_at
            + self.termination_grace_seconds
            - time.monotonic()
        )
        if remaining_grace > 0:
            time.sleep(remaining_grace)
        # The group leader has only been observed with WNOWAIT here. Its PID and
        # PGID therefore cannot be reused while the last escalation is sent.
        try:
            self._signal_process_group(process_group, signal.SIGKILL)
        except PermissionError:
            return
        time.sleep(0.05)

    def commit(self, finalize: Callable[[], None] | None = None) -> None:
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            self.managed_signals,
        )
        try:
            self.record_pending_signal()
            if self.first_signum is not None:
                raise SignalInterruption(self.first_signum)
            self._committed = True
            try:
                if finalize is not None:
                    finalize()
            except BaseException:
                self._committed = False
                self.record_pending_signal()
                raise
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


@contextlib.contextmanager
def _temporary_signal_handlers() -> Iterator[None]:
    with SignalCoordinator():
        yield


def _require_device(node_stat: os.stat_result, expected_device: int) -> None:
    if node_stat.st_dev != expected_device:
        raise PackagingError("filesystem device boundary changed")


def _open_directory_at(
    parent_fd: int,
    child_name: str,
    *,
    expected_device: int,
    expected_identity: NodeIdentity | None = None,
) -> tuple[int, os.stat_result]:
    _validate_child_name(child_name)
    child_stat = os.stat(child_name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(child_stat.st_mode):
        raise PackagingError("expected a real directory")
    _require_device(child_stat, expected_device)
    child_fd = os.open(
        child_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )
    try:
        opened_stat = os.fstat(child_fd)
        if not NodeIdentity.from_stat(child_stat).matches(opened_stat):
            raise PackagingError("directory identity changed while opening")
        if expected_identity is not None and not expected_identity.matches(opened_stat):
            raise PackagingError("directory identity no longer matches its owner")
        _require_device(opened_stat, expected_device)
        return child_fd, opened_stat
    except BaseException:
        os.close(child_fd)
        raise


def _creation_timestamp_ns(node_stat: os.stat_result) -> int:
    birthtime_ns = getattr(node_stat, "st_birthtime_ns", None)
    if birthtime_ns is not None:
        return int(birthtime_ns)
    birthtime = getattr(node_stat, "st_birthtime", None)
    if birthtime is not None:
        return int(birthtime * 1_000_000_000)
    return node_stat.st_ctime_ns


def _adopt_new_private_directory(
    parent_fd: int,
    *,
    prefix: str,
    expected_device: int,
) -> CreatedDirectory:
    """Create and immediately adopt a cryptographically private empty directory.

    A same-user peer that learns the random name and replaces it with a second
    indistinguishable empty directory inside this tiny interval cannot be
    distinguished by portable filesystem metadata.  Any failed open or
    pristine-shape proof is therefore treated as non-adoption: the named entry
    is left untouched and is never chmodded or recursively removed.
    """
    for _ in range(16):
        candidate = f"{prefix}{secrets.token_hex(16)}"
        started_ns = time.time_ns()
        try:
            os.mkdir(candidate, mode=0o700, dir_fd=parent_fd)
        except OSError as error:
            if error.errno == errno.EEXIST:
                continue
            raise PackagingError("private directory could not be created") from error

        descriptor = -1
        try:
            # This must remain the first filesystem observation after mkdir.
            descriptor = os.open(
                candidate,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            opened_stat = os.fstat(descriptor)
            finished_ns = time.time_ns()
            if not stat.S_ISDIR(opened_stat.st_mode):
                raise PackagingError("new private entry is not a directory")
            _require_device(opened_stat, expected_device)
            if stat.S_IMODE(opened_stat.st_mode) != 0o700:
                raise PackagingError("new private directory mode is not pristine")
            if opened_stat.st_nlink != 2:
                raise PackagingError("new private directory link shape is not pristine")
            if os.listdir(descriptor):
                raise PackagingError("new private directory is not empty")
            timestamp_slack_ns = 2_000_000_000
            for timestamp_ns in (
                opened_stat.st_ctime_ns,
                _creation_timestamp_ns(opened_stat),
            ):
                if not (
                    started_ns - timestamp_slack_ns
                    <= timestamp_ns
                    <= finished_ns + timestamp_slack_ns
                ):
                    raise PackagingError(
                        "new private directory metadata predates creation"
                    )
            named_stat = os.stat(
                candidate,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            identity = NodeIdentity.from_stat(opened_stat)
            if not identity.matches(named_stat):
                raise PackagingError("new private directory binding changed")
            if (
                not stat.S_ISDIR(named_stat.st_mode)
                or stat.S_IMODE(named_stat.st_mode) != 0o700
                or named_stat.st_nlink != 2
            ):
                raise PackagingError("new private directory binding is not pristine")
            return CreatedDirectory(candidate, descriptor, identity)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            # The entry is deliberately preserved because ownership was not
            # established before the failure.
            raise
    raise PackagingError("private directory name could not be allocated")


def _remove_owned_empty_directory(
    parent_fd: int,
    directory: CreatedDirectory,
    expected_device: int,
) -> None:
    opened_stat = os.fstat(directory.descriptor)
    named_stat = os.stat(
        directory.name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    if (
        not directory.identity.matches(opened_stat)
        or not directory.identity.matches(named_stat)
    ):
        raise PackagingError("private directory cleanup ownership was lost")
    _require_device(opened_stat, expected_device)
    _require_device(named_stat, expected_device)
    if os.listdir(directory.descriptor):
        raise PackagingError("private directory is no longer empty")
    os.rmdir(directory.name, dir_fd=parent_fd)


def _verify_regular_file(
    file_stat: os.stat_result,
    *,
    expected_device: int,
    require_executable: bool = False,
) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise PackagingError("expected a regular file")
    _require_device(file_stat, expected_device)
    if file_stat.st_nlink != 1:
        raise PackagingError("regular files must have exactly one link")
    if require_executable and file_stat.st_mode & 0o111 == 0:
        raise PackagingError("expected an executable file")


def _open_regular_at(
    parent_fd: int,
    child_name: str,
    *,
    expected_device: int,
    require_executable: bool = False,
    writable: bool = False,
) -> tuple[int, os.stat_result]:
    _validate_child_name(child_name)
    child_stat = os.stat(child_name, dir_fd=parent_fd, follow_symlinks=False)
    _verify_regular_file(
        child_stat,
        expected_device=expected_device,
        require_executable=require_executable,
    )
    flags = os.O_RDWR if writable else os.O_RDONLY
    child_fd = os.open(child_name, flags | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        opened_stat = os.fstat(child_fd)
        if not NodeIdentity.from_stat(child_stat).matches(opened_stat):
            raise PackagingError("file identity changed while opening")
        _verify_regular_file(
            opened_stat,
            expected_device=expected_device,
            require_executable=require_executable,
        )
        return child_fd, opened_stat
    except BaseException:
        os.close(child_fd)
        raise


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
    if not isinstance(version, str) or SEMANTIC_VERSION.fullmatch(version) is None:
        raise PackagingError("project.version must be an exact semantic version")
    return version


def _prepare_build_root(repo_fd: int, repo_device: int) -> tuple[int, NodeIdentity]:
    try:
        build_stat = os.stat(BUILD_DIRECTORY, dir_fd=repo_fd, follow_symlinks=False)
    except OSError as error:
        if error.errno != errno.ENOENT:
            raise PackagingError("build entry could not be inspected") from error
        created = _adopt_new_private_directory(
            repo_fd,
            prefix=".dotsync-build.",
            expected_device=repo_device,
        )
        published = False
        try:
            os.fchmod(created.descriptor, 0o755)
            _rename_no_replace(
                repo_fd,
                created.name,
                repo_fd,
                BUILD_DIRECTORY,
            )
            published = True
            build_stat = os.stat(
                BUILD_DIRECTORY,
                dir_fd=repo_fd,
                follow_symlinks=False,
            )
            if not created.identity.matches(build_stat):
                raise PackagingError("new build directory binding changed")
            return created.descriptor, created.identity
        except BaseException:
            if not published:
                try:
                    _remove_owned_empty_directory(repo_fd, created, repo_device)
                finally:
                    os.close(created.descriptor)
            else:
                os.close(created.descriptor)
            raise
    if not stat.S_ISDIR(build_stat.st_mode):
        raise PackagingError("build entry must be a real directory")
    _require_device(build_stat, repo_device)
    build_fd, opened_stat = _open_directory_at(
        repo_fd,
        BUILD_DIRECTORY,
        expected_device=repo_device,
        expected_identity=NodeIdentity.from_stat(build_stat),
    )
    return build_fd, NodeIdentity.from_stat(opened_stat)


def _verify_build_binding(
    repo_fd: int,
    build_fd: int,
    build_identity: NodeIdentity,
) -> None:
    opened_stat = os.fstat(build_fd)
    if not build_identity.matches(opened_stat):
        raise PackagingError("open build directory identity changed")
    _require_device(opened_stat, build_identity.device)
    try:
        named_stat = os.stat(BUILD_DIRECTORY, dir_fd=repo_fd, follow_symlinks=False)
    except OSError as error:
        raise PackagingError("repository build binding disappeared") from error
    if not stat.S_ISDIR(named_stat.st_mode) or not build_identity.matches(named_stat):
        raise PackagingError("repository build binding was replaced")


def _require_final_absent(build_fd: int) -> None:
    try:
        os.stat(FINAL_APP, dir_fd=build_fd, follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return
        raise PackagingError("final app entry could not be inspected") from error
    raise PackagingError("remove the existing build/DotSync.app before building")


def _remove_directory_contents(directory_fd: int, expected_device: int) -> None:
    for name in os.listdir(directory_fd):
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _require_device(entry_stat, expected_device)
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd, _ = _open_directory_at(
                directory_fd,
                name,
                expected_device=expected_device,
                expected_identity=NodeIdentity.from_stat(entry_stat),
            )
            try:
                _remove_directory_contents(child_fd, expected_device)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _remove_directory_contents_except(
    directory_fd: int,
    expected_device: int,
    kept_names: frozenset[str],
) -> None:
    for name in os.listdir(directory_fd):
        if name in kept_names:
            continue
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _require_device(entry_stat, expected_device)
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd, _ = _open_directory_at(
                directory_fd,
                name,
                expected_device=expected_device,
                expected_identity=NodeIdentity.from_stat(entry_stat),
            )
            try:
                _remove_directory_contents(child_fd, expected_device)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _cleanup_owned_stage(
    build_fd: int,
    stage: StagingDirectory,
    expected_device: int,
) -> None:
    named_stat = os.stat(stage.name, dir_fd=build_fd, follow_symlinks=False)
    if not stage.identity.matches(named_stat):
        raise PackagingError("staging cleanup ownership was lost")
    _require_device(named_stat, expected_device)
    if not stage.identity.matches(os.fstat(stage.descriptor)):
        raise PackagingError("open staging identity changed")
    _remove_directory_contents(stage.descriptor, expected_device)
    os.rmdir(stage.name, dir_fd=build_fd)
    stage.removed = True


def _remove_owned_empty_stage(
    build_fd: int,
    stage: StagingDirectory,
    expected_device: int,
) -> None:
    named_stat = os.stat(stage.name, dir_fd=build_fd, follow_symlinks=False)
    opened_stat = os.fstat(stage.descriptor)
    if (
        not stage.identity.matches(named_stat)
        or not stage.identity.matches(opened_stat)
    ):
        raise PackagingError("empty staging cleanup ownership was lost")
    _require_device(named_stat, expected_device)
    _require_device(opened_stat, expected_device)
    if os.listdir(stage.descriptor):
        raise PackagingError("staging directory is not empty at publication commit")
    os.rmdir(stage.name, dir_fd=build_fd)
    stage.removed = True


@contextlib.contextmanager
def _owned_staging_directory(
    build_fd: int,
    expected_device: int,
) -> Iterator[StagingDirectory]:
    stage: StagingDirectory | None = None
    try:
        created = _adopt_new_private_directory(
            build_fd,
            prefix=".dotsync-app-stage.",
            expected_device=expected_device,
        )
        stage = StagingDirectory(
            name=created.name,
            descriptor=created.descriptor,
            identity=created.identity,
        )
        yield stage
    finally:
        cleanup_error: BaseException | None = None
        if stage is not None and not stage.removed and not stage.preserve:
            try:
                _cleanup_owned_stage(build_fd, stage, expected_device)
            except BaseException as error:
                cleanup_error = error
        if stage is not None:
            os.close(stage.descriptor)
        if cleanup_error is not None:
            raise PackagingError("private staging cleanup failed") from cleanup_error


def _create_directory(parent_fd: int, name: str, device: int, mode: int) -> int:
    _validate_child_name(name)
    os.mkdir(name, mode=mode, dir_fd=parent_fd)
    child_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    child_fd, _ = _open_directory_at(
        parent_fd,
        name,
        expected_device=device,
        expected_identity=NodeIdentity.from_stat(child_stat),
    )
    os.fchmod(child_fd, mode)
    return child_fd


def _write_all(file_fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(file_fd, view)
        if written == 0:
            raise PackagingError("file write made no progress")
        view = view[written:]


def _read_all(file_fd: int) -> bytes:
    payload = bytearray()
    os.lseek(file_fd, 0, os.SEEK_SET)
    while True:
        block = os.read(file_fd, 1024 * 1024)
        if not block:
            return bytes(payload)
        payload.extend(block)


def _copy_snapshot_file(
    source_parent_fd: int,
    destination_parent_fd: int,
    name: str,
    *,
    source_device: int,
    destination_device: int,
) -> None:
    source_fd, source_stat = _open_regular_at(
        source_parent_fd,
        name,
        expected_device=source_device,
    )
    try:
        if source_stat.st_mode & 0o444 == 0:
            raise PackagingError("build input contains an unreadable file")
        payload = _read_all(source_fd)
        final_source_stat = os.fstat(source_fd)
        if (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_mode,
            source_stat.st_nlink,
            source_stat.st_size,
            source_stat.st_mtime_ns,
        ) != (
            final_source_stat.st_dev,
            final_source_stat.st_ino,
            final_source_stat.st_mode,
            final_source_stat.st_nlink,
            final_source_stat.st_size,
            final_source_stat.st_mtime_ns,
        ):
            raise PackagingError("build input changed while snapshotting")
    finally:
        os.close(source_fd)
    destination_fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=destination_parent_fd,
    )
    try:
        _write_all(destination_fd, payload)
        os.fchmod(destination_fd, stat.S_IMODE(source_stat.st_mode))
        _verify_regular_file(
            os.fstat(destination_fd),
            expected_device=destination_device,
        )
    finally:
        os.close(destination_fd)


def _copy_snapshot_directory(
    source_fd: int,
    destination_fd: int,
    *,
    source_device: int,
    destination_device: int,
    is_package_root: bool = False,
) -> None:
    _require_device(os.fstat(source_fd), source_device)
    _require_device(os.fstat(destination_fd), destination_device)
    for name in sorted(os.listdir(source_fd), key=os.fsencode):
        if is_package_root and name == ".build":
            continue
        entry_stat = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        _require_device(entry_stat, source_device)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise PackagingError("build input contains a symlink")
        if stat.S_ISDIR(entry_stat.st_mode):
            child_source_fd, _ = _open_directory_at(
                source_fd,
                name,
                expected_device=source_device,
                expected_identity=NodeIdentity.from_stat(entry_stat),
            )
            try:
                child_destination_fd = _create_directory(
                    destination_fd,
                    name,
                    destination_device,
                    stat.S_IMODE(entry_stat.st_mode),
                )
                try:
                    _copy_snapshot_directory(
                        child_source_fd,
                        child_destination_fd,
                        source_device=source_device,
                        destination_device=destination_device,
                    )
                finally:
                    os.close(child_destination_fd)
            finally:
                os.close(child_source_fd)
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            raise PackagingError("build input contains a special file")
        _copy_snapshot_file(
            source_fd,
            destination_fd,
            name,
            source_device=source_device,
            destination_device=destination_device,
        )


def _input_manifest(
    directory_fd: int,
    expected_device: int,
    *,
    relative_root: tuple[str, ...] = (),
    exclude_package_build: bool = False,
) -> tuple[ManifestEntry, ...]:
    _require_device(os.fstat(directory_fd), expected_device)
    manifest: list[ManifestEntry] = []
    for name in sorted(os.listdir(directory_fd), key=os.fsencode):
        if not relative_root and exclude_package_build and name == ".build":
            continue
        relative_path = (*relative_root, name)
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _require_device(entry_stat, expected_device)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise PackagingError("build input contains a symlink")
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd, opened_stat = _open_directory_at(
                directory_fd,
                name,
                expected_device=expected_device,
                expected_identity=NodeIdentity.from_stat(entry_stat),
            )
            try:
                descendants = _input_manifest(
                    child_fd,
                    expected_device,
                    relative_root=relative_path,
                )
                final_stat = os.fstat(child_fd)
                if _stable_metadata(opened_stat) != _stable_metadata(final_stat):
                    raise PackagingError("build input directory changed while reading")
                manifest.append(
                    _manifest_entry(relative_path, "directory", final_stat, None)
                )
                manifest.extend(descendants)
            finally:
                os.close(child_fd)
            continue
        _verify_regular_file(entry_stat, expected_device=expected_device)
        if entry_stat.st_mode & 0o444 == 0:
            raise PackagingError("build input contains an unreadable file")
        file_fd, opened_stat = _open_regular_at(
            directory_fd,
            name,
            expected_device=expected_device,
        )
        try:
            payload = _read_all(file_fd)
            final_stat = os.fstat(file_fd)
            if _stable_metadata(opened_stat) != _stable_metadata(final_stat):
                raise PackagingError("build input file changed while reading")
            manifest.append(
                _manifest_entry(
                    relative_path,
                    "file",
                    final_stat,
                    hashlib.sha256(payload).digest(),
                )
            )
        finally:
            os.close(file_fd)
    return tuple(manifest)


def _input_tree_manifest(
    directory_fd: int,
    expected_device: int,
    *,
    exclude_package_build: bool = False,
) -> tuple[ManifestEntry, ...]:
    root_before = os.fstat(directory_fd)
    _require_device(root_before, expected_device)
    descendants = _input_manifest(
        directory_fd,
        expected_device,
        exclude_package_build=exclude_package_build,
    )
    root_after = os.fstat(directory_fd)
    if _stable_metadata(root_before) != _stable_metadata(root_after):
        raise PackagingError("build input root changed while reading")
    return (
        _manifest_entry((), "directory", root_after, None),
        *descendants,
    )


def _portable_input_manifest(
    manifest: tuple[ManifestEntry, ...],
) -> tuple[tuple[tuple[str, ...], str, int, int, bytes | None], ...]:
    return tuple(
        (
            entry.relative_path,
            entry.kind,
            stat.S_IMODE(entry.mode),
            entry.size if entry.kind == "file" else 0,
            entry.digest,
        )
        for entry in manifest
    )


def _named_input_file_manifest(
    parent_fd: int,
    name: str,
    expected_device: int,
) -> ManifestEntry:
    file_fd, opened_stat = _open_regular_at(
        parent_fd,
        name,
        expected_device=expected_device,
    )
    try:
        payload = _read_all(file_fd)
        final_stat = os.fstat(file_fd)
        if _stable_metadata(opened_stat) != _stable_metadata(final_stat):
            raise PackagingError("build input file changed while reading")
        return _manifest_entry(
            (name,),
            "file",
            final_stat,
            hashlib.sha256(payload).digest(),
        )
    finally:
        os.close(file_fd)


def _portable_input_entry(
    entry: ManifestEntry,
) -> tuple[tuple[str, ...], str, int, int, bytes | None]:
    return (
        entry.relative_path,
        entry.kind,
        stat.S_IMODE(entry.mode),
        entry.size,
        entry.digest,
    )


def _snapshot_build_inputs(
    repo_fd: int,
    repo_device: int,
    stage: StagingDirectory,
) -> InputSnapshot:
    macos_fd, _ = _open_directory_at(
        repo_fd,
        "macos",
        expected_device=repo_device,
    )
    try:
        package_source_fd, _ = _open_directory_at(
            macos_fd,
            "DotSyncApp",
            expected_device=repo_device,
        )
    finally:
        os.close(macos_fd)
    packaging_fd = -1
    package_destination_fd = -1
    try:
        packaging_fd, _ = _open_directory_at(
            repo_fd,
            "packaging",
            expected_device=repo_device,
        )
        source_package_before = _input_tree_manifest(
            package_source_fd,
            repo_device,
            exclude_package_build=True,
        )
        source_plist_before = _named_input_file_manifest(
            packaging_fd,
            PLIST_SNAPSHOT,
            repo_device,
        )
        package_destination_fd = _create_directory(
            stage.descriptor,
            PACKAGE_SNAPSHOT,
            stage.identity.device,
            0o755,
        )
        _copy_snapshot_directory(
            package_source_fd,
            package_destination_fd,
            source_device=repo_device,
            destination_device=stage.identity.device,
            is_package_root=True,
        )
        _copy_snapshot_file(
            packaging_fd,
            stage.descriptor,
            PLIST_SNAPSHOT,
            source_device=repo_device,
            destination_device=stage.identity.device,
        )

        source_package_after = _input_tree_manifest(
            package_source_fd,
            repo_device,
            exclude_package_build=True,
        )
        source_plist_after = _named_input_file_manifest(
            packaging_fd,
            PLIST_SNAPSHOT,
            repo_device,
        )
        if source_package_after != source_package_before:
            raise PackagingError("build source package changed during snapshot")
        if source_plist_after != source_plist_before:
            raise PackagingError("build source plist changed during snapshot")

        package_manifest = _input_tree_manifest(
            package_destination_fd,
            stage.identity.device,
        )
        if _portable_input_manifest(package_manifest) != _portable_input_manifest(
            source_package_before
        ):
            raise PackagingError("package snapshot does not match its source")
        snapshot_plist = _named_input_file_manifest(
            stage.descriptor,
            PLIST_SNAPSHOT,
            stage.identity.device,
        )
        if _portable_input_entry(snapshot_plist) != _portable_input_entry(
            source_plist_before
        ):
            raise PackagingError("plist snapshot does not match its source")
    finally:
        if package_destination_fd >= 0:
            os.close(package_destination_fd)
        if packaging_fd >= 0:
            os.close(packaging_fd)
        os.close(package_source_fd)
    return InputSnapshot(package_manifest, snapshot_plist)


def _verify_input_snapshot(
    stage_fd: int,
    expected_device: int,
    expected_snapshot: InputSnapshot,
) -> None:
    package_fd, _ = _open_directory_at(
        stage_fd,
        PACKAGE_SNAPSHOT,
        expected_device=expected_device,
    )
    try:
        current_manifest = _input_tree_manifest(package_fd, expected_device)
    finally:
        os.close(package_fd)
    if current_manifest != expected_snapshot.package_manifest:
        raise PackagingError("package snapshot changed between Swift invocations")
    current_plist = _named_input_file_manifest(
        stage_fd,
        PLIST_SNAPSHOT,
        expected_device,
    )
    if current_plist != expected_snapshot.plist_manifest:
        raise PackagingError("plist snapshot changed between Swift invocations")


def _run_tool(
    arguments: list[str],
    *,
    cwd_fd: int,
    pass_fds: tuple[int, ...] = (),
    capture_stdout: bool = False,
    coordinator: SignalCoordinator | None = None,
) -> str:
    inherited_fds = set(pass_fds)
    inherited_fds.add(cwd_fd)

    def enter_pinned_directory() -> None:
        os.fchdir(cwd_fd)

    process: subprocess.Popen[bytes] | None = None
    stdout = ""
    captured_chunks: list[str] = []
    capture_errors: list[PackagingError] = []
    capture_failed = threading.Event()
    capture_thread: threading.Thread | None = None
    capture_reader_started = False
    leader_observed = False
    leader_reaped = False
    kill_sent = False
    quiesced = False
    operation_error: BaseException | None = None

    def record_capture_error(error: PackagingError) -> None:
        if not capture_errors:
            capture_errors.append(error)
        capture_failed.set()

    def drain_captured_stdout(pipe_fd: int) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        captured_bytes = 0
        decoding = True
        try:
            while True:
                try:
                    block = os.read(pipe_fd, 16 * 1024)
                except OSError:
                    record_capture_error(
                        PackagingError("captured stdout could not be read")
                    )
                    return
                if not block:
                    if decoding:
                        try:
                            captured_chunks.append(decoder.decode(b"", final=True))
                        except UnicodeDecodeError:
                            record_capture_error(
                                PackagingError("captured stdout is not valid UTF-8")
                            )
                    return
                captured_bytes += len(block)
                if captured_bytes > CAPTURED_STDOUT_LIMIT:
                    record_capture_error(
                        PackagingError("captured stdout exceeded the 64 KiB limit")
                    )
                    decoding = False
                    continue
                if decoding:
                    try:
                        captured_chunks.append(decoder.decode(block, final=False))
                    except UnicodeDecodeError:
                        record_capture_error(
                            PackagingError("captured stdout is not valid UTF-8")
                        )
                        decoding = False
        except BaseException:
            record_capture_error(
                PackagingError("captured stdout reader failed unexpectedly")
            )

    def start_capture_reader() -> None:
        nonlocal capture_reader_started, capture_thread
        assert process is not None and process.stdout is not None
        capture_thread = threading.Thread(
            target=drain_captured_stdout,
            args=(process.stdout.fileno(),),
            name="dotsync-captured-stdout",
            daemon=True,
        )
        capture_thread.start()
        capture_reader_started = True

    def kill_failed_group(process_group: int) -> None:
        try:
            SignalCoordinator._signal_process_group(
                process_group,
                signal.SIGKILL,
            )
        except PermissionError:
            # A zombie-only group can report EPERM on macOS. The still-WNOWAIT
            # leader remains reserved and is reaped below.
            pass

    def wait_for_capture_reader(process_group: int, kill_sent: bool) -> bool:
        if capture_thread is None or not capture_reader_started:
            return kill_sent
        capture_thread.join(CAPTURE_READER_GRACE_SECONDS)
        if capture_thread.is_alive():
            record_capture_error(
                PackagingError("captured stdout did not close after leader exit")
            )
            if not kill_sent:
                kill_failed_group(process_group)
                kill_sent = True
            time.sleep(0.05)
            capture_thread.join(CAPTURE_READER_GRACE_SECONDS)
        if capture_thread.is_alive():
            assert process is not None and process.stdout is not None
            process.stdout.close()
            capture_thread.join(CAPTURE_READER_GRACE_SECONDS)
        return kill_sent

    def observe_leader_without_reaping() -> None:
        nonlocal kill_sent, leader_observed
        assert process is not None
        while not leader_observed:
            if capture_failed.is_set() and not kill_sent:
                kill_failed_group(process.pid)
                kill_sent = True
            observed = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            if observed is not None:
                if observed.si_pid != process.pid:
                    raise PackagingError(
                        "build tool leader observation identity changed"
                    )
                leader_observed = True
                return
            if (
                coordinator is not None
                and coordinator.should_escalate()
                and not kill_sent
            ):
                try:
                    coordinator._signal_process_group(
                        process.pid,
                        signal.SIGKILL,
                    )
                except PermissionError:
                    pass
                kill_sent = True
            time.sleep(0.01)

    def reap_observed_leader() -> None:
        nonlocal leader_reaped
        assert process is not None
        if not leader_observed or leader_reaped:
            raise PackagingError("build tool leader reap state was invalid")
        reaped = os.waitid(os.P_PID, process.pid, os.WEXITED)
        if reaped is None or reaped.si_pid != process.pid:
            raise PackagingError("build tool leader reap identity changed")
        if reaped.si_code == os.CLD_EXITED:
            process.returncode = reaped.si_status
        elif reaped.si_code in {os.CLD_KILLED, os.CLD_DUMPED}:
            process.returncode = -reaped.si_status
        else:
            raise PackagingError("build tool leader had an invalid exit state")
        leader_reaped = True

    def retain_operation_error(error: BaseException) -> None:
        nonlocal operation_error
        if operation_error is None:
            operation_error = error

    def finalize_owned_process() -> None:
        nonlocal kill_sent, quiesced
        if process is None:
            return
        previous_mask: set[signal.Signals] | None = None
        if coordinator is not None:
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                coordinator.managed_signals,
            )
        try:
            try:
                if coordinator is not None:
                    coordinator.record_pending_signal()
                interrupted = coordinator is not None and coordinator.interrupted
                if (
                    (operation_error is not None or capture_failed.is_set())
                    and not interrupted
                    and not kill_sent
                ):
                    kill_failed_group(process.pid)
                    kill_sent = True
                observe_leader_without_reaping()
                if interrupted:
                    assert coordinator is not None
                    coordinator.quiesce_process_group(
                        process.pid,
                        already_escalated=kill_sent,
                    )
                    quiesced = True
                if capture_stdout:
                    kill_sent = wait_for_capture_reader(process.pid, kill_sent)
                if coordinator is not None:
                    coordinator.record_pending_signal()
                    if coordinator.interrupted and not quiesced:
                        coordinator.quiesce_process_group(
                            process.pid,
                            already_escalated=kill_sent,
                        )
                        quiesced = True
                if (
                    (operation_error is not None or capture_failed.is_set())
                    and not quiesced
                ):
                    if not kill_sent:
                        kill_failed_group(process.pid)
                        kill_sent = True
                    time.sleep(0.05)
                    quiesced = True
            except BaseException as error:
                retain_operation_error(error)
                if not leader_reaped and not kill_sent:
                    kill_failed_group(process.pid)
                    kill_sent = True
                if not leader_reaped:
                    time.sleep(0.05)
                    quiesced = True
            finally:
                if not leader_observed:
                    try:
                        observe_leader_without_reaping()
                    except BaseException as error:
                        retain_operation_error(error)
                if leader_observed and not leader_reaped:
                    try:
                        reap_observed_leader()
                    except BaseException as error:
                        retain_operation_error(error)
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except BaseException as error:
                        retain_operation_error(error)
        finally:
            if coordinator is not None:
                coordinator.detach(process)
            if previous_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    try:
        if coordinator is not None:
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                coordinator.managed_signals,
            )
            try:
                coordinator.record_pending_signal()
                if coordinator.interrupted:
                    assert coordinator.first_signum is not None
                    raise SignalInterruption(coordinator.first_signum)
                process = subprocess.Popen(
                    arguments,
                    pass_fds=tuple(sorted(inherited_fds)),
                    preexec_fn=enter_pinned_directory,
                    stdout=subprocess.PIPE if capture_stdout else sys.stderr,
                    stderr=sys.stderr,
                    text=False,
                    start_new_session=True,
                )
                coordinator.attach(process)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        else:
            process = subprocess.Popen(
                arguments,
                pass_fds=tuple(sorted(inherited_fds)),
                preexec_fn=enter_pinned_directory,
                stdout=subprocess.PIPE if capture_stdout else sys.stderr,
                stderr=sys.stderr,
                text=False,
                start_new_session=True,
            )

        if capture_stdout:
            start_capture_reader()
        observe_leader_without_reaping()
    except BaseException as error:
        retain_operation_error(error)
    finally:
        finalize_owned_process()

    if coordinator is not None and coordinator.interrupted:
        assert coordinator.first_signum is not None
        interruption = SignalInterruption(coordinator.first_signum)
        if operation_error is not None:
            raise interruption from operation_error
        raise interruption
    if capture_errors:
        raise capture_errors[0]
    if operation_error is not None:
        if isinstance(operation_error, (PackagingError, SignalInterruption)):
            raise operation_error
        raise PackagingError(f"{arguments[0]} failed") from operation_error
    assert process is not None and leader_reaped
    if process.returncode != 0:
        raise PackagingError(f"{arguments[0]} failed")
    if capture_stdout:
        stdout = "".join(captured_chunks)
    return stdout


def _resolve_sdk(stage_fd: int, coordinator: SignalCoordinator) -> str:
    output = _run_tool(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
        cwd_fd=stage_fd,
        capture_stdout=True,
        coordinator=coordinator,
    )
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise PackagingError("macOS SDK could not be resolved exactly")
    return lines[0]


def _open_relative_directory(
    root_fd: int,
    relative_path: str,
    expected_device: int,
) -> int:
    components = relative_path.split("/")
    if not components or any(component in {"", ".", ".."} for component in components):
        raise PackagingError("Swift binary directory escaped its scratch path")
    current_fd = os.dup(root_fd)
    try:
        for component in components:
            next_fd, _ = _open_directory_at(
                current_fd,
                component,
                expected_device=expected_device,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _build_architecture(
    *,
    repo_fd: int,
    build_fd: int,
    build_identity: NodeIdentity,
    stage_fd: int,
    triple: str,
    scratch_name: str,
    sdk: str,
    input_snapshot: InputSnapshot,
    coordinator: SignalCoordinator,
) -> int:
    arguments = [
        "swift",
        "build",
        "--package-path",
        PACKAGE_SNAPSHOT,
        "--configuration",
        "release",
        "--triple",
        triple,
        "--sdk",
        sdk,
        "--scratch-path",
        scratch_name,
    ]
    _verify_input_snapshot(stage_fd, build_identity.device, input_snapshot)
    _run_tool(arguments, cwd_fd=stage_fd, coordinator=coordinator)
    _verify_input_snapshot(stage_fd, build_identity.device, input_snapshot)
    _verify_build_binding(repo_fd, build_fd, build_identity)
    _verify_input_snapshot(stage_fd, build_identity.device, input_snapshot)
    binary_directory = _run_tool(
        [*arguments, "--show-bin-path"],
        cwd_fd=stage_fd,
        capture_stdout=True,
        coordinator=coordinator,
    ).strip()
    _verify_input_snapshot(stage_fd, build_identity.device, input_snapshot)
    _verify_build_binding(repo_fd, build_fd, build_identity)
    scratch_fd, _ = _open_directory_at(
        stage_fd,
        scratch_name,
        expected_device=build_identity.device,
    )
    try:
        components = Path(binary_directory).parts
        scratch_positions = [
            index for index, component in enumerate(components) if component == scratch_name
        ]
        if len(scratch_positions) != 1:
            raise PackagingError("Swift binary directory did not name its scratch root")
        relative_components = components[scratch_positions[0] + 1 :]
        if not relative_components:
            raise PackagingError("Swift binary directory was the scratch root")
        relative_binary_directory = "/".join(relative_components)
        binary_dir_fd = _open_relative_directory(
            scratch_fd,
            relative_binary_directory,
            build_identity.device,
        )
        try:
            binary_fd, _ = _open_regular_at(
                binary_dir_fd,
                "DotSync",
                expected_device=build_identity.device,
                require_executable=True,
            )
            return binary_fd
        finally:
            os.close(binary_dir_fd)
    finally:
        os.close(scratch_fd)


def _render_info_plist(stage_fd: int, stage_device: int, version: str) -> bytes:
    template_fd, _ = _open_regular_at(
        stage_fd,
        PLIST_SNAPSHOT,
        expected_device=stage_device,
    )
    try:
        try:
            template = _read_all(template_fd).decode("utf-8")
        except UnicodeDecodeError as error:
            raise PackagingError("Info.plist template is not UTF-8") from error
    finally:
        os.close(template_fd)
    rendered = template.replace("__DOTSYNC_VERSION__", version).replace(
        "__DOTSYNC_BUILD__",
        version,
    )
    if "__DOTSYNC_VERSION__" in rendered or "__DOTSYNC_BUILD__" in rendered:
        raise PackagingError("Info.plist contains an unresolved build sentinel")
    return rendered.encode()


def _write_info_plist(contents_fd: int, payload: bytes, device: int) -> int:
    plist_fd = os.open(
        "Info.plist",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
        dir_fd=contents_fd,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(plist_fd, view)
            view = view[written:]
        os.fchmod(plist_fd, 0o644)
    finally:
        os.close(plist_fd)
    readable_fd, _ = _open_regular_at(
        contents_fd,
        "Info.plist",
        expected_device=device,
    )
    return readable_fd


def _payload_contains_leak(contents: bytes, checkout: bytes) -> bool:
    return (
        checkout in contents
        or QUERY_CAPABILITY.search(contents) is not None
        or JSON_CAPABILITY.search(contents) is not None
        or PROVIDER_HOME_COMPONENT.search(contents) is not None
    )


def _manifest_entry(
    relative_path: tuple[str, ...],
    kind: str,
    node_stat: os.stat_result,
    digest: bytes | None,
) -> ManifestEntry:
    return ManifestEntry(
        relative_path=relative_path,
        kind=kind,
        device=node_stat.st_dev,
        inode=node_stat.st_ino,
        link_count=node_stat.st_nlink,
        mode=node_stat.st_mode,
        size=node_stat.st_size,
        modified_ns=node_stat.st_mtime_ns,
        digest=digest,
    )


def _stable_metadata(node_stat: os.stat_result) -> tuple[int, ...]:
    return (
        node_stat.st_dev,
        node_stat.st_ino,
        node_stat.st_nlink,
        node_stat.st_mode,
        node_stat.st_size,
        node_stat.st_mtime_ns,
    )


def _scan_directory(
    directory_fd: int,
    checkout: bytes,
    expected_device: int,
    relative_root: tuple[str, ...] = (),
) -> tuple[ManifestEntry, ...]:
    _require_device(os.fstat(directory_fd), expected_device)
    manifest: list[ManifestEntry] = []
    for name in sorted(os.listdir(directory_fd), key=os.fsencode):
        relative_path = (*relative_root, name)
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _require_device(entry_stat, expected_device)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise PackagingError("bundle contains a symlink")
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd, opened_stat = _open_directory_at(
                directory_fd,
                name,
                expected_device=expected_device,
                expected_identity=NodeIdentity.from_stat(entry_stat),
            )
            try:
                descendants = _scan_directory(
                    child_fd,
                    checkout,
                    expected_device,
                    relative_path,
                )
                final_stat = os.fstat(child_fd)
                if _stable_metadata(opened_stat) != _stable_metadata(final_stat):
                    raise PackagingError("bundle directory changed while scanning")
                manifest.append(
                    _manifest_entry(relative_path, "directory", final_stat, None)
                )
                manifest.extend(descendants)
            finally:
                os.close(child_fd)
            continue
        _verify_regular_file(entry_stat, expected_device=expected_device)
        if entry_stat.st_mode & 0o444 == 0:
            raise PackagingError("bundle contains an unreadable file")
        file_fd, opened_stat = _open_regular_at(
            directory_fd,
            name,
            expected_device=expected_device,
        )
        try:
            payload = _read_all(file_fd)
            final_stat = os.fstat(file_fd)
            if _stable_metadata(opened_stat) != _stable_metadata(final_stat):
                raise PackagingError("bundle file changed while scanning")
            if _payload_contains_leak(payload, checkout):
                raise PackagingError("bundle contains forbidden private data")
            manifest.append(
                _manifest_entry(
                    relative_path,
                    "file",
                    final_stat,
                    hashlib.sha256(payload).digest(),
                )
            )
        finally:
            os.close(file_fd)
    return tuple(manifest)


def _open_and_scan_staged_app(
    stage_fd: int,
    *,
    checkout: bytes,
    expected_device: int,
) -> ScannedApp:
    app_stat = os.stat(FINAL_APP, dir_fd=stage_fd, follow_symlinks=False)
    app_fd, opened_stat = _open_directory_at(
        stage_fd,
        FINAL_APP,
        expected_device=expected_device,
        expected_identity=NodeIdentity.from_stat(app_stat),
    )
    try:
        manifest = _scan_directory(app_fd, checkout, expected_device)
        return ScannedApp(
            descriptor=app_fd,
            identity=NodeIdentity.from_stat(opened_stat),
            manifest=manifest,
            checkout=checkout,
        )
    except BaseException:
        os.close(app_fd)
        raise


def _rename_no_replace(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = libc.renameatx_np
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    result = renameatx_np(
        source_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(destination_name),
        RENAME_EXCL,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise PackagingError("final app could not be published without replacement") from OSError(
            error_number,
            os.strerror(error_number),
        )


def _rename_swap(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = libc.renameatx_np
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    result = renameatx_np(
        source_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(destination_name),
        RENAME_SWAP | RENAME_NOFOLLOW_ANY | RENAME_RESOLVE_BENEATH,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise PublicationOwnershipError(
            "published app rollback ownership was lost during atomic exchange"
        ) from OSError(error_number, os.strerror(error_number))


def _remove_owned_directory_binding(
    parent_fd: int,
    name: str,
    descriptor: int,
    identity: NodeIdentity,
    expected_device: int,
) -> None:
    opened_stat = os.fstat(descriptor)
    named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not identity.matches(opened_stat) or not identity.matches(named_stat):
        raise PublicationOwnershipError(
            "published app rollback private ownership was lost"
        )
    _require_device(opened_stat, expected_device)
    _require_device(named_stat, expected_device)
    _remove_directory_contents(descriptor, expected_device)
    named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not identity.matches(named_stat):
        raise PublicationOwnershipError(
            "published app rollback private binding changed"
        )
    os.rmdir(name, dir_fd=parent_fd)


def _publish_scanned_app(
    *,
    repo_fd: int,
    build_fd: int,
    build_identity: NodeIdentity,
    stage: StagingDirectory,
    scanned_app: ScannedApp,
    coordinator: SignalCoordinator,
) -> None:
    published = False
    try:
        _verify_build_binding(repo_fd, build_fd, build_identity)
        opened_stage_stat = os.fstat(stage.descriptor)
        if not stage.identity.matches(opened_stage_stat):
            raise PackagingError("open staging identity changed after scanning")
        _require_device(opened_stage_stat, build_identity.device)
        stage_stat = os.stat(stage.name, dir_fd=build_fd, follow_symlinks=False)
        if not stage.identity.matches(stage_stat):
            raise PackagingError("staging identity changed after scanning")
        _require_device(stage_stat, build_identity.device)
        app_stat = os.stat(FINAL_APP, dir_fd=stage.descriptor, follow_symlinks=False)
        if not scanned_app.identity.matches(app_stat):
            raise PackagingError("staged app changed after scanning")
        if not scanned_app.identity.matches(os.fstat(scanned_app.descriptor)):
            raise PackagingError("open staged app changed after scanning")
        _require_device(app_stat, build_identity.device)
        current_manifest = _scan_directory(
            scanned_app.descriptor,
            scanned_app.checkout,
            build_identity.device,
        )
        if current_manifest != scanned_app.manifest:
            raise PackagingError("staged app descendant manifest changed after scanning")
        held_app_stat = os.fstat(scanned_app.descriptor)
        if stat.S_IMODE(held_app_stat.st_mode) != 0o755:
            raise PackagingError("staged app mode must be exactly 0755")
        if coordinator.interrupted:
            assert coordinator.first_signum is not None
            raise SignalInterruption(coordinator.first_signum)
        _require_final_absent(build_fd)
        _rename_no_replace(stage.descriptor, FINAL_APP, build_fd, FINAL_APP)
        published = True
        _verify_build_binding(repo_fd, build_fd, build_identity)
        final_stat = os.stat(FINAL_APP, dir_fd=build_fd, follow_symlinks=False)
        _require_device(final_stat, build_identity.device)
        if not stat.S_ISDIR(final_stat.st_mode) or not scanned_app.identity.matches(
            final_stat
        ):
            raise PackagingError(
                "published final app identity does not match the held app"
            )
        held_app_stat = os.fstat(scanned_app.descriptor)
        if not scanned_app.identity.matches(held_app_stat):
            raise PackagingError("published final app held identity changed")
        if (
            stat.S_IMODE(final_stat.st_mode) != 0o755
            or stat.S_IMODE(held_app_stat.st_mode) != 0o755
        ):
            raise PackagingError("published final app mode is not exactly 0755")
        coordinator.commit(
            lambda: _remove_owned_empty_stage(
                build_fd,
                stage,
                build_identity.device,
            )
        )
    except BaseException as publication_error:
        if published:
            try:
                _rollback_published_app_to_stage(
                    build_fd,
                    build_identity,
                    stage,
                    scanned_app,
                )
            except PublicationOwnershipError:
                raise
            except BaseException as rollback_error:
                raise PublicationOwnershipError(
                    "published app rollback lost exact ownership"
                ) from rollback_error
        raise publication_error


def _rollback_published_app_to_stage(
    build_fd: int,
    build_identity: NodeIdentity,
    stage: StagingDirectory,
    scanned_app: ScannedApp,
) -> None:
    def remove_proven_empty_stage() -> None:
        # Keep fail-closed preservation active through the atomic rmdir. If an
        # unowned entry appears, empty-stage removal fails without allowing the
        # staging context to recurse into it during unwind.
        _remove_owned_empty_stage(build_fd, stage, build_identity.device)
        stage.preserve = False

    held_stat = os.fstat(scanned_app.descriptor)
    if not scanned_app.identity.matches(held_stat):
        raise PublicationOwnershipError(
            "published app rollback held ownership was lost"
        )
    _require_device(held_stat, build_identity.device)
    stage_stat = os.stat(stage.name, dir_fd=build_fd, follow_symlinks=False)
    if (
        not stage.identity.matches(stage_stat)
        or not stage.identity.matches(os.fstat(stage.descriptor))
    ):
        raise PublicationOwnershipError(
            "published app rollback staging ownership was lost"
        )
    _require_device(stage_stat, build_identity.device)
    # Placeholder adoption can deliberately preserve an unproven pathname.
    # Protect the containing stage before that first fallible ownership step.
    stage.preserve = True
    placeholder = _adopt_new_private_directory(
        stage.descriptor,
        prefix=".rollback-placeholder.",
        expected_device=build_identity.device,
    )
    try:
        _rename_swap(
            build_fd,
            FINAL_APP,
            stage.descriptor,
            placeholder.name,
        )
        captured_stat = os.stat(
            placeholder.name,
            dir_fd=stage.descriptor,
            follow_symlinks=False,
        )
        public_stat = os.stat(
            FINAL_APP,
            dir_fd=build_fd,
            follow_symlinks=False,
        )
        captured_owned = (
            stat.S_ISDIR(captured_stat.st_mode)
            and scanned_app.identity.matches(captured_stat)
        )
        public_is_placeholder = placeholder.identity.matches(public_stat)

        if captured_owned:
            _remove_owned_directory_binding(
                stage.descriptor,
                placeholder.name,
                scanned_app.descriptor,
                scanned_app.identity,
                build_identity.device,
            )
            if not public_is_placeholder:
                remove_proven_empty_stage()
                raise PublicationOwnershipError(
                    "published app rollback ownership was lost after atomic exchange"
                )
            _rename_no_replace(
                build_fd,
                FINAL_APP,
                stage.descriptor,
                placeholder.name,
            )
            returned_stat = os.stat(
                placeholder.name,
                dir_fd=stage.descriptor,
                follow_symlinks=False,
            )
            if placeholder.identity.matches(returned_stat):
                _remove_owned_empty_directory(
                    stage.descriptor,
                    placeholder,
                    build_identity.device,
                )
                remove_proven_empty_stage()
                return
            try:
                _rename_no_replace(
                    stage.descriptor,
                    placeholder.name,
                    build_fd,
                    FINAL_APP,
                )
            except BaseException:
                raise PublicationOwnershipError(
                    "published app rollback preserved an unowned private entry"
                )
            remove_proven_empty_stage()
            raise PublicationOwnershipError(
                "published app rollback lost exact ownership; restored an unowned final replacement"
            )

        if not public_is_placeholder:
            raise PublicationOwnershipError(
                "published app rollback preserved an unowned private entry"
            )
        _rename_swap(
            stage.descriptor,
            placeholder.name,
            build_fd,
            FINAL_APP,
        )
        restored_private_stat = os.stat(
            placeholder.name,
            dir_fd=stage.descriptor,
            follow_symlinks=False,
        )
        if not placeholder.identity.matches(restored_private_stat):
            raise PublicationOwnershipError(
                "published app rollback preserved an unowned private entry"
            )
        _remove_owned_empty_directory(
            stage.descriptor,
            placeholder,
            build_identity.device,
        )
        remove_proven_empty_stage()
        raise PublicationOwnershipError(
            "published app rollback lost exact ownership; restored an unowned final replacement"
        )
    finally:
        os.close(placeholder.descriptor)


def _assemble_staged_app(
    *,
    repo_path: Path,
    repo_fd: int,
    build_fd: int,
    build_identity: NodeIdentity,
    stage: StagingDirectory,
    version: str,
    coordinator: SignalCoordinator,
) -> ScannedApp:
    input_snapshot = _snapshot_build_inputs(
        repo_fd,
        os.fstat(repo_fd).st_dev,
        stage,
    )
    sdk = _resolve_sdk(stage.descriptor, coordinator)
    _verify_build_binding(repo_fd, build_fd, build_identity)
    arm_fd = _build_architecture(
        repo_fd=repo_fd,
        build_fd=build_fd,
        build_identity=build_identity,
        stage_fd=stage.descriptor,
        triple="arm64-apple-macosx13.0",
        scratch_name=ARM_SCRATCH,
        sdk=sdk,
        input_snapshot=input_snapshot,
        coordinator=coordinator,
    )
    try:
        x86_fd = _build_architecture(
            repo_fd=repo_fd,
            build_fd=build_fd,
            build_identity=build_identity,
            stage_fd=stage.descriptor,
            triple="x86_64-apple-macosx13.0",
            scratch_name=X86_SCRATCH,
            sdk=sdk,
            input_snapshot=input_snapshot,
            coordinator=coordinator,
        )
        try:
            app_fd = _create_directory(
                stage.descriptor,
                FINAL_APP,
                build_identity.device,
                0o755,
            )
            try:
                contents_fd = _create_directory(
                    app_fd,
                    "Contents",
                    build_identity.device,
                    0o755,
                )
                try:
                    macos_fd = _create_directory(
                        contents_fd,
                        "MacOS",
                        build_identity.device,
                        0o755,
                    )
                    try:
                        output_path = "DotSync"
                        _run_tool(
                            [
                                "lipo",
                                "-create",
                                f"/dev/fd/{arm_fd}",
                                f"/dev/fd/{x86_fd}",
                                "-output",
                                output_path,
                            ],
                            cwd_fd=macos_fd,
                            pass_fds=(arm_fd, x86_fd),
                            coordinator=coordinator,
                        )
                        binary_fd, _ = _open_regular_at(
                            macos_fd,
                            "DotSync",
                            expected_device=build_identity.device,
                            require_executable=True,
                            writable=True,
                        )
                        try:
                            os.fchmod(binary_fd, 0o755)
                        finally:
                            os.close(binary_fd)
                        _run_tool(
                            ["strip", "-S", output_path],
                            cwd_fd=macos_fd,
                            coordinator=coordinator,
                        )
                        stripped_fd, _ = _open_regular_at(
                            macos_fd,
                            "DotSync",
                            expected_device=build_identity.device,
                            require_executable=True,
                        )
                        try:
                            _run_tool(
                                [
                                    "lipo",
                                    output_path,
                                    "-verify_arch",
                                    "arm64",
                                    "x86_64",
                                ],
                                cwd_fd=macos_fd,
                                coordinator=coordinator,
                            )
                            current_stat = os.stat(
                                "DotSync",
                                dir_fd=macos_fd,
                                follow_symlinks=False,
                            )
                            if not NodeIdentity.from_stat(os.fstat(stripped_fd)).matches(
                                current_stat
                            ):
                                raise PackagingError("bundle executable identity changed")
                            _verify_regular_file(
                                current_stat,
                                expected_device=build_identity.device,
                                require_executable=True,
                            )
                        finally:
                            os.close(stripped_fd)
                    finally:
                        os.close(macos_fd)
                    _verify_input_snapshot(
                        stage.descriptor,
                        build_identity.device,
                        input_snapshot,
                    )
                    rendered_plist = _render_info_plist(
                        stage.descriptor,
                        build_identity.device,
                        version,
                    )
                    plist_fd = _write_info_plist(
                        contents_fd,
                        rendered_plist,
                        build_identity.device,
                    )
                    try:
                        _run_tool(
                            ["plutil", "-lint", "Info.plist"],
                            cwd_fd=contents_fd,
                            coordinator=coordinator,
                        )
                    finally:
                        os.close(plist_fd)
                finally:
                    os.close(contents_fd)
            finally:
                os.close(app_fd)
        finally:
            os.close(x86_fd)
    finally:
        os.close(arm_fd)
    scanned_app = _open_and_scan_staged_app(
        stage.descriptor,
        checkout=os.fsencode(repo_path),
        expected_device=build_identity.device,
    )
    try:
        _remove_directory_contents_except(
            stage.descriptor,
            build_identity.device,
            frozenset({FINAL_APP}),
        )
        if set(os.listdir(stage.descriptor)) != {FINAL_APP}:
            raise PackagingError("publication staging contains unexpected entries")
        _publish_scanned_app(
            repo_fd=repo_fd,
            build_fd=build_fd,
            build_identity=build_identity,
            stage=stage,
            scanned_app=scanned_app,
            coordinator=coordinator,
        )
        return scanned_app
    except BaseException:
        os.close(scanned_app.descriptor)
        raise


def assemble(repo_path: Path) -> Path:
    repo_path = repo_path.resolve(strict=True)
    repo_fd = os.open(repo_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    published_app: ScannedApp | None = None
    try:
        repo_stat = os.fstat(repo_fd)
        version = project_version(repo_path / "pyproject.toml")
        with SignalCoordinator() as coordinator:
            operation_error: BaseException | None = None
            try:
                build_fd, build_identity = _prepare_build_root(
                    repo_fd,
                    repo_stat.st_dev,
                )
                _verify_build_binding(repo_fd, build_fd, build_identity)
                _require_final_absent(build_fd)
                os.fchmod(build_fd, 0o755)
                with _owned_staging_directory(
                    build_fd,
                    build_identity.device,
                ) as stage:
                    published_app = _assemble_staged_app(
                        repo_path=repo_path,
                        repo_fd=repo_fd,
                        build_fd=build_fd,
                        build_identity=build_identity,
                        stage=stage,
                        version=version,
                        coordinator=coordinator,
                    )
            except BaseException as error:
                operation_error = error

            if isinstance(operation_error, PublicationOwnershipError):
                raise operation_error
            if coordinator.interrupted:
                assert coordinator.first_signum is not None
                interruption = SignalInterruption(coordinator.first_signum)
                if operation_error is not None:
                    raise interruption from operation_error
                raise interruption
            if operation_error is not None:
                raise operation_error

        if published_app is not None:
            os.close(published_app.descriptor)
            published_app = None
        return repo_path / BUILD_DIRECTORY / FINAL_APP
    except OSError as error:
        raise PackagingError("identity-bound app assembly failed") from error
    finally:
        if published_app is not None:
            os.close(published_app.descriptor)
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


def main(arguments: list[str]) -> int:
    try:
        if len(arguments) == 2 and arguments[0] == "version":
            print(project_version(Path(arguments[1])))
            return 0
        if len(arguments) == 2 and arguments[0] == "assemble":
            print(assemble(Path(arguments[1])))
            return 0
        print("macos_app_support: invalid arguments", file=sys.stderr)
        return 2
    except PackagingError as error:
        print(f"macos_app_support: {error}", file=sys.stderr)
        return 1
    except SignalInterruption as error:
        print(f"macos_app_support: {error}", file=sys.stderr)
        return 128 + error.signum


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
