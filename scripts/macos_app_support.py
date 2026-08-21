#!/usr/bin/env python3
"""Identity-bound helpers for assembling the local macOS application bundle."""

from __future__ import annotations

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
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


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


class PackagingError(Exception):
    """A filesystem or build input violates the local packaging contract."""


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


def _validate_child_name(child_name: str) -> None:
    if child_name in {"", ".", ".."} or Path(child_name).name != child_name:
        raise PackagingError("target is not an exact direct child")


@contextlib.contextmanager
def _temporary_signal_handlers() -> Iterator[None]:
    previous_handlers: dict[int, signal.Handlers] = {}

    def interrupt(signum: int, _frame: object) -> None:
        raise SignalInterruption(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
        yield
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


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
        os.mkdir(BUILD_DIRECTORY, mode=0o755, dir_fd=repo_fd)
        build_stat = os.stat(BUILD_DIRECTORY, dir_fd=repo_fd, follow_symlinks=False)
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


@contextlib.contextmanager
def _owned_staging_directory(
    build_fd: int,
    expected_device: int,
) -> Iterator[StagingDirectory]:
    stage_name = ""
    stage: StagingDirectory | None = None
    created_identity: NodeIdentity | None = None
    created = False
    try:
        for _ in range(16):
            candidate = f".dotsync-app-stage.{secrets.token_hex(4)}"
            try:
                os.mkdir(candidate, mode=0o700, dir_fd=build_fd)
            except OSError as error:
                if error.errno == errno.EEXIST:
                    continue
                raise
            stage_name = candidate
            created = True
            break
        if not created:
            raise PackagingError("private staging name could not be allocated")
        stage_stat = os.stat(stage_name, dir_fd=build_fd, follow_symlinks=False)
        created_identity = NodeIdentity.from_stat(stage_stat)
        stage_fd, opened_stat = _open_directory_at(
            build_fd,
            stage_name,
            expected_device=expected_device,
            expected_identity=created_identity,
        )
        os.fchmod(stage_fd, 0o700)
        stage = StagingDirectory(
            name=stage_name,
            descriptor=stage_fd,
            identity=NodeIdentity.from_stat(opened_stat),
        )
        yield stage
    finally:
        cleanup_error: BaseException | None = None
        if created:
            try:
                if stage is None:
                    if created_identity is None:
                        raise PackagingError("staging identity was never captured")
                    named_stat = os.stat(
                        stage_name,
                        dir_fd=build_fd,
                        follow_symlinks=False,
                    )
                    if not created_identity.matches(named_stat):
                        raise PackagingError("staging cleanup ownership was lost")
                    provisional_fd, opened_stat = _open_directory_at(
                        build_fd,
                        stage_name,
                        expected_device=expected_device,
                        expected_identity=created_identity,
                    )
                    stage = StagingDirectory(
                        name=stage_name,
                        descriptor=provisional_fd,
                        identity=NodeIdentity.from_stat(opened_stat),
                    )
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


def _snapshot_build_inputs(
    repo_fd: int,
    repo_device: int,
    stage: StagingDirectory,
) -> None:
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
    try:
        package_destination_fd = _create_directory(
            stage.descriptor,
            PACKAGE_SNAPSHOT,
            stage.identity.device,
            0o755,
        )
        try:
            _copy_snapshot_directory(
                package_source_fd,
                package_destination_fd,
                source_device=repo_device,
                destination_device=stage.identity.device,
                is_package_root=True,
            )
        finally:
            os.close(package_destination_fd)
    finally:
        os.close(package_source_fd)
    packaging_fd, _ = _open_directory_at(
        repo_fd,
        "packaging",
        expected_device=repo_device,
    )
    try:
        _copy_snapshot_file(
            packaging_fd,
            stage.descriptor,
            PLIST_SNAPSHOT,
            source_device=repo_device,
            destination_device=stage.identity.device,
        )
    finally:
        os.close(packaging_fd)


def _run_tool(
    arguments: list[str],
    *,
    cwd_fd: int,
    pass_fds: tuple[int, ...] = (),
    capture_stdout: bool = False,
) -> str:
    inherited_fds = set(pass_fds)
    inherited_fds.add(cwd_fd)

    def enter_pinned_directory() -> None:
        os.fchdir(cwd_fd)

    try:
        result = subprocess.run(
            arguments,
            check=True,
            pass_fds=tuple(sorted(inherited_fds)),
            preexec_fn=enter_pinned_directory,
            stdout=subprocess.PIPE if capture_stdout else sys.stderr,
            stderr=sys.stderr,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PackagingError(f"{arguments[0]} failed") from error
    return result.stdout if capture_stdout else ""


def _resolve_sdk(stage_fd: int) -> str:
    output = _run_tool(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
        cwd_fd=stage_fd,
        capture_stdout=True,
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
    _run_tool(arguments, cwd_fd=stage_fd)
    _verify_build_binding(repo_fd, build_fd, build_identity)
    binary_directory = _run_tool(
        [*arguments, "--show-bin-path"],
        cwd_fd=stage_fd,
        capture_stdout=True,
    ).strip()
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


def _publish_scanned_app(
    *,
    repo_fd: int,
    build_fd: int,
    build_identity: NodeIdentity,
    stage: StagingDirectory,
    scanned_app: ScannedApp,
) -> None:
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
    _require_final_absent(build_fd)
    _rename_no_replace(stage.descriptor, FINAL_APP, build_fd, FINAL_APP)
    _verify_build_binding(repo_fd, build_fd, build_identity)
    final_stat = os.stat(FINAL_APP, dir_fd=build_fd, follow_symlinks=False)
    _require_device(final_stat, build_identity.device)
    if not stat.S_ISDIR(final_stat.st_mode) or not scanned_app.identity.matches(
        final_stat
    ):
        raise PackagingError("published final app identity does not match the held app")
    held_app_stat = os.fstat(scanned_app.descriptor)
    if not scanned_app.identity.matches(held_app_stat):
        raise PackagingError("published final app held identity changed")
    if (
        stat.S_IMODE(final_stat.st_mode) != 0o755
        or stat.S_IMODE(held_app_stat.st_mode) != 0o755
    ):
        raise PackagingError("published final app mode is not exactly 0755")


def _assemble_staged_app(
    *,
    repo_path: Path,
    repo_fd: int,
    build_fd: int,
    build_identity: NodeIdentity,
    stage: StagingDirectory,
    version: str,
) -> None:
    _snapshot_build_inputs(repo_fd, os.fstat(repo_fd).st_dev, stage)
    sdk = _resolve_sdk(stage.descriptor)
    _verify_build_binding(repo_fd, build_fd, build_identity)
    arm_fd = _build_architecture(
        repo_fd=repo_fd,
        build_fd=build_fd,
        build_identity=build_identity,
        stage_fd=stage.descriptor,
        triple="arm64-apple-macosx13.0",
        scratch_name=ARM_SCRATCH,
        sdk=sdk,
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
                    plist_fd = _write_info_plist(
                        contents_fd,
                        _render_info_plist(
                            stage.descriptor,
                            build_identity.device,
                            version,
                        ),
                        build_identity.device,
                    )
                    try:
                        _run_tool(
                            ["plutil", "-lint", "Info.plist"],
                            cwd_fd=contents_fd,
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
        _publish_scanned_app(
            repo_fd=repo_fd,
            build_fd=build_fd,
            build_identity=build_identity,
            stage=stage,
            scanned_app=scanned_app,
        )
    finally:
        os.close(scanned_app.descriptor)


def assemble(repo_path: Path) -> Path:
    repo_path = repo_path.resolve(strict=True)
    repo_fd = os.open(repo_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    try:
        repo_stat = os.fstat(repo_fd)
        version = project_version(repo_path / "pyproject.toml")
        build_fd, build_identity = _prepare_build_root(repo_fd, repo_stat.st_dev)
        _verify_build_binding(repo_fd, build_fd, build_identity)
        _require_final_absent(build_fd)
        with _temporary_signal_handlers():
            os.fchmod(build_fd, 0o755)
            with _owned_staging_directory(build_fd, build_identity.device) as stage:
                _assemble_staged_app(
                    repo_path=repo_path,
                    repo_fd=repo_fd,
                    build_fd=build_fd,
                    build_identity=build_identity,
                    stage=stage,
                    version=version,
                )
        _verify_build_binding(repo_fd, build_fd, build_identity)
        return repo_path / BUILD_DIRECTORY / FINAL_APP
    except OSError as error:
        raise PackagingError("identity-bound app assembly failed") from error
    finally:
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
