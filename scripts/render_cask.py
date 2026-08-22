#!/usr/bin/env python3
"""Render the public DotSync Cask from verified release artifact facts."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import re
import secrets
import signal
import stat
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


CANONICAL_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
SHA256 = re.compile(r"[0-9a-f]{64}")
OUTPUT_PARTS = ("Casks", "dotsync-app.rb")
TEMPLATE_PARTS = ("packaging", "dotsync-app.rb.in")
SENTINELS = (
    "__DOTSYNC_VERSION__",
    "__DOTSYNC_SHA256__",
    "__DOTSYNC_URL__",
)
CASK_RELEASE_URL = (
    "https://github.com/changja88/homebrew-dotsync/releases/download/"
    "v#{version}/DotSync-#{version}-macOS.zip"
)
MANAGED_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


@dataclass(frozen=True)
class PublicationBinding:
    casks_dev: int
    casks_ino: int
    cask_dev: int
    cask_ino: int


def _validated_release_url(version: str) -> str:
    return (
        "https://github.com/changja88/homebrew-dotsync/releases/download/"
        f"v{version}/DotSync-{version}-macOS.zip"
    )


def _validate_inputs(version: str, sha256: str, url: str) -> None:
    if CANONICAL_VERSION.fullmatch(version) is None or version == "0.0.0":
        raise ValueError("version must be a canonical non-zero X.Y.Z value")
    if SHA256.fullmatch(sha256) is None or sha256 == "0" * 64:
        raise ValueError("SHA-256 must be 64 lowercase hexadecimal characters")
    if url != _validated_release_url(version):
        raise ValueError("URL must be the exact matching GitHub release asset")


def _exact_output_path(repository_root: Path, output: Path) -> Path:
    if ".." in output.parts:
        raise ValueError("output must be the exact Casks/dotsync-app.rb path")
    absolute_output = Path(os.path.abspath(output))
    expected_output = repository_root.joinpath(*OUTPUT_PARTS)
    if absolute_output != expected_output:
        raise ValueError("output must be the exact Casks/dotsync-app.rb path")
    return expected_output


def _read_template(repository_root: Path) -> str:
    root_fd = os.open(
        repository_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    packaging_fd = -1
    template_fd = -1
    try:
        packaging_stat = os.stat(
            TEMPLATE_PARTS[0],
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(packaging_stat.st_mode):
            raise ValueError("packaging must be a real directory")
        packaging_fd = os.open(
            TEMPLATE_PARTS[0],
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        opened_packaging_stat = os.fstat(packaging_fd)
        if _identity(packaging_stat) != _identity(opened_packaging_stat):
            raise ValueError("packaging directory identity changed while opening")

        template_stat = os.stat(
            TEMPLATE_PARTS[1],
            dir_fd=packaging_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(template_stat.st_mode):
            raise ValueError("Cask template must be a regular file")
        template_fd = os.open(
            TEMPLATE_PARTS[1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=packaging_fd,
        )
        opened_template_stat = os.fstat(template_fd)
        if _identity(template_stat) != _identity(opened_template_stat):
            raise ValueError("Cask template identity changed while opening")
        template_bytes = _read_all(template_fd)
        final_template_stat = os.stat(
            TEMPLATE_PARTS[1],
            dir_fd=packaging_fd,
            follow_symlinks=False,
        )
        if _identity(final_template_stat) != _identity(opened_template_stat):
            raise ValueError("Cask template binding changed while reading")
        template = template_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Cask template must be UTF-8") from error
    finally:
        if template_fd >= 0:
            os.close(template_fd)
        if packaging_fd >= 0:
            os.close(packaging_fd)
        os.close(root_fd)
    if any(template.count(sentinel) != 1 for sentinel in SENTINELS):
        raise ValueError("Cask template must contain every sentinel exactly once")
    return template


def _render_template(template: str, version: str, sha256: str, url: str) -> bytes:
    if url != _validated_release_url(version):
        raise ValueError("URL must be the exact matching GitHub release asset")
    rendered = (
        template.replace(SENTINELS[0], version)
        .replace(SENTINELS[1], sha256)
        .replace(SENTINELS[2], CASK_RELEASE_URL)
    )
    if "__DOTSYNC_" in rendered:
        raise ValueError("rendered Cask contains an unresolved sentinel")
    return rendered.encode("utf-8")


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError("Cask write made no progress")
        remaining = remaining[written:]


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _open_exact_regular(
    directory_fd: int,
    name: str,
    expected: tuple[int, int],
) -> tuple[int, os.stat_result]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or _identity(metadata) != expected:
        os.close(descriptor)
        raise ValueError(f"{name} identity changed")
    return descriptor, metadata


def _unlink_exact(
    directory_fd: int,
    name: str,
    expected: tuple[int, int],
) -> None:
    descriptor, _metadata = _open_exact_regular(directory_fd, name, expected)
    try:
        os.unlink(name, dir_fd=directory_fd)
    finally:
        os.close(descriptor)


def _entry_is_exact_regular(
    directory_fd: int,
    name: str,
    expected: tuple[int, int],
) -> bool:
    try:
        descriptor, _metadata = _open_exact_regular(directory_fd, name, expected)
    except (OSError, ValueError):
        return False
    os.close(descriptor)
    return True


def _swap_entries(directory_fd: int, first: str, second: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameatx_np = libc.renameatx_np
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "renameatx_np is required for safe replacement") from error
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    rename_swap = 0x00000002
    if (
        renameatx_np(
            directory_fd,
            os.fsencode(first),
            directory_fd,
            os.fsencode(second),
            rename_swap,
        )
        != 0
    ):
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


@contextmanager
def _defer_managed_signals():
    prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, MANAGED_SIGNALS)
    commit_state = [False]
    try:
        yield commit_state
    finally:
        if commit_state[0]:
            try:
                _discard_pending_managed_signals()
            except BaseException:
                pass
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
            except BaseException:
                pass
        else:
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)


def _validate_published_entry(
    directory_fd: int,
    expected: tuple[int, int],
    *,
    expected_link_count: int,
) -> None:
    path_metadata = os.stat(
        OUTPUT_PARTS[1],
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    if not stat.S_ISREG(path_metadata.st_mode):
        raise ValueError("rendered Cask is not a regular file")
    descriptor, opened_metadata = _open_exact_regular(
        directory_fd,
        OUTPUT_PARTS[1],
        expected,
    )
    try:
        if (
            _identity(path_metadata) != expected
            or stat.S_IMODE(opened_metadata.st_mode) != 0o644
            or opened_metadata.st_nlink != expected_link_count
        ):
            raise ValueError("rendered Cask does not have exact 0644 ownership shape")
    finally:
        os.close(descriptor)


def _rollback_new_publication(
    casks_fd: int,
    temporary_name: str,
    published_identity: tuple[int, int],
) -> None:
    _unlink_exact(casks_fd, OUTPUT_PARTS[1], published_identity)
    _unlink_exact(casks_fd, temporary_name, published_identity)
    os.fsync(casks_fd)


def _rollback_replacement(
    casks_fd: int,
    temporary_name: str,
    published_identity: tuple[int, int],
    prior_identity: tuple[int, int],
) -> None:
    retained_descriptor, _retained_metadata = _open_exact_regular(
        casks_fd,
        temporary_name,
        prior_identity,
    )
    os.close(retained_descriptor)
    _swap_entries(casks_fd, temporary_name, OUTPUT_PARTS[1])
    restored_descriptor, _restored_metadata = _open_exact_regular(
        casks_fd,
        OUTPUT_PARTS[1],
        prior_identity,
    )
    os.close(restored_descriptor)
    _unlink_exact(casks_fd, temporary_name, published_identity)
    os.fsync(casks_fd)


def _raise_if_managed_signal_pending() -> None:
    pending = set(signal.sigpending()).intersection(MANAGED_SIGNALS)
    if pending:
        signal_number = min(int(item) for item in pending)
        raise InterruptedError(
            f"renderer interrupted before commit by signal {signal_number}"
        )


def _discard_pending_managed_signals() -> None:
    while True:
        pending = set(signal.sigpending()).intersection(MANAGED_SIGNALS)
        if not pending:
            return
        signal.sigwait(pending)


def _commit_retained_entry(
    casks_fd: int,
    temporary_name: str,
    retained_identity: tuple[int, int],
) -> None:
    descriptor, _metadata = _open_exact_regular(
        casks_fd,
        temporary_name,
        retained_identity,
    )
    committed = False
    try:
        _raise_if_managed_signal_pending()
        os.unlink(temporary_name, dir_fd=casks_fd)
        committed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            if not committed:
                raise


def _publish_rendered_cask(
    repository_root: Path,
    payload: bytes,
    *,
    replace_existing: bool,
    binding_callback: Callable[[PublicationBinding], None] | None = None,
    expected_casks_identity: tuple[int, int] | None = None,
    inherited_casks_descriptor: int | None = None,
) -> PublicationBinding:
    root_fd = os.open(
        repository_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    casks_fd = -1
    temporary_name = f".dotsync-app.{secrets.token_hex(16)}.tmp"
    temporary_created = False
    publication_may_own_output = False
    publication_committed = False
    published_identity: tuple[int, int] | None = None
    replacement_prior_identity: tuple[int, int] | None = None
    try:
        root_stat = os.fstat(root_fd)
        if inherited_casks_descriptor is None:
            casks_stat = os.stat(
                OUTPUT_PARTS[0],
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(casks_stat.st_mode):
                raise ValueError("Casks must be a real directory")
            casks_fd = os.open(
                OUTPUT_PARTS[0],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        else:
            casks_fd = os.dup(inherited_casks_descriptor)
            casks_stat = os.fstat(casks_fd)
        opened_casks_stat = os.fstat(casks_fd)
        if (
            casks_stat.st_dev,
            casks_stat.st_ino,
        ) != (
            opened_casks_stat.st_dev,
            opened_casks_stat.st_ino,
        ):
            raise ValueError("Casks directory identity changed while opening")
        if opened_casks_stat.st_dev != root_stat.st_dev:
            raise ValueError("Casks directory crossed a filesystem boundary")
        if (
            expected_casks_identity is not None
            and _identity(opened_casks_stat) != expected_casks_identity
        ):
            raise ValueError("Casks directory did not match the caller binding")

        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=casks_fd,
        )
        temporary_created = True
        published_identity = _identity(os.fstat(temporary_fd))
        try:
            _write_all(temporary_fd, payload)
            os.fchmod(temporary_fd, 0o644)
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)

        with _defer_managed_signals() as signal_commit_state:
            try:
                if replace_existing:
                    prior_descriptor = os.open(
                        OUTPUT_PARTS[1],
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=casks_fd,
                    )
                    try:
                        prior_metadata = os.fstat(prior_descriptor)
                        if not stat.S_ISREG(prior_metadata.st_mode):
                            raise ValueError("existing Cask must be a regular file")
                        replacement_prior_identity = _identity(prior_metadata)
                    finally:
                        os.close(prior_descriptor)
                    publication_may_own_output = True
                    _swap_entries(casks_fd, temporary_name, OUTPUT_PARTS[1])
                    expected_link_count = 1
                else:
                    publication_may_own_output = True
                    os.link(
                        temporary_name,
                        OUTPUT_PARTS[1],
                        src_dir_fd=casks_fd,
                        dst_dir_fd=casks_fd,
                        follow_symlinks=False,
                    )
                    expected_link_count = 2
                assert published_identity is not None
                _validate_published_entry(
                    casks_fd,
                    published_identity,
                    expected_link_count=expected_link_count,
                )
                os.fsync(casks_fd)
                if inherited_casks_descriptor is None:
                    final_casks_stat = os.stat(
                        OUTPUT_PARTS[0],
                        dir_fd=root_fd,
                        follow_symlinks=False,
                    )
                    if _identity(final_casks_stat) != _identity(opened_casks_stat):
                        raise ValueError(
                            "Casks directory binding changed during publication"
                        )
                binding = PublicationBinding(
                    casks_dev=opened_casks_stat.st_dev,
                    casks_ino=opened_casks_stat.st_ino,
                    cask_dev=published_identity[0],
                    cask_ino=published_identity[1],
                )
                if binding_callback is not None:
                    binding_callback(binding)
                retained_identity = (
                    replacement_prior_identity
                    if replace_existing
                    else published_identity
                )
                assert retained_identity is not None
                _commit_retained_entry(
                    casks_fd,
                    temporary_name,
                    retained_identity,
                )
                publication_committed = True
                signal_commit_state[0] = True
                temporary_created = False
                publication_may_own_output = False
            except BaseException as error:
                output_is_owned = (
                    publication_may_own_output
                    and published_identity is not None
                    and _entry_is_exact_regular(
                        casks_fd,
                        OUTPUT_PARTS[1],
                        published_identity,
                    )
                )
                if output_is_owned:
                    try:
                        if replace_existing:
                            assert replacement_prior_identity is not None
                            _rollback_replacement(
                                casks_fd,
                                temporary_name,
                                published_identity,
                                replacement_prior_identity,
                            )
                            temporary_created = False
                        else:
                            _rollback_new_publication(
                                casks_fd,
                                temporary_name,
                                published_identity,
                            )
                            temporary_created = False
                    except BaseException as rollback_error:
                        raise RuntimeError(
                            "Cask publication failed and exact rollback failed: "
                            f"{rollback_error}"
                        ) from error
                raise
        return binding
    finally:
        if (
            temporary_created
            and casks_fd >= 0
            and published_identity is not None
            and _entry_is_exact_regular(
                casks_fd,
                temporary_name,
                published_identity,
            )
        ):
            try:
                _unlink_exact(casks_fd, temporary_name, published_identity)
            except FileNotFoundError:
                pass
        if casks_fd >= 0:
            try:
                os.close(casks_fd)
            except OSError:
                if not publication_committed:
                    raise
        try:
            os.close(root_fd)
        except OSError:
            if not publication_committed:
                raise


def render_cask(
    *,
    version: str,
    sha256: str,
    url: str,
    output: Path,
    repository_root: Path,
    replace_existing: bool = False,
    expected_casks_identity: tuple[int, int] | None = None,
    inherited_casks_descriptor: int | None = None,
) -> Path:
    """Render exact release facts without network access or checksum inference."""
    exact_output, _binding = _render_cask_with_binding(
        version=version,
        sha256=sha256,
        url=url,
        output=output,
        repository_root=repository_root,
        replace_existing=replace_existing,
        expected_casks_identity=expected_casks_identity,
        inherited_casks_descriptor=inherited_casks_descriptor,
    )
    return exact_output


def _render_cask_with_binding(
    *,
    version: str,
    sha256: str,
    url: str,
    output: Path,
    repository_root: Path,
    replace_existing: bool,
    binding_callback: Callable[[PublicationBinding], None] | None = None,
    expected_casks_identity: tuple[int, int] | None = None,
    inherited_casks_descriptor: int | None = None,
) -> tuple[Path, PublicationBinding]:
    _validate_inputs(version, sha256, url)
    root_input = Path(repository_root)
    if root_input.is_symlink():
        raise ValueError("repository root must not be a symlink")
    try:
        root = root_input.resolve(strict=True)
    except OSError as error:
        raise ValueError("repository root must be an existing directory") from error
    if not root.is_dir():
        raise ValueError("repository root must be an existing directory")
    exact_output = _exact_output_path(root, Path(output))
    template = _read_template(root)
    payload = _render_template(template, version, sha256, url)
    binding = _publish_rendered_cask(
        root,
        payload,
        replace_existing=replace_existing,
        binding_callback=binding_callback,
        expected_casks_identity=expected_casks_identity,
        inherited_casks_descriptor=inherited_casks_descriptor,
    )
    return exact_output, binding


def rollback_created_cask(
    *,
    repository_root: Path,
    casks_identity: tuple[int, int],
    cask_identity: tuple[int, int] | None,
    remove_casks_directory: bool,
    inherited_casks_descriptor: int | None = None,
) -> None:
    root_fd = os.open(
        repository_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    casks_fd = -1
    try:
        if inherited_casks_descriptor is None:
            casks_metadata = os.stat(
                OUTPUT_PARTS[0],
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(casks_metadata.st_mode)
                or _identity(casks_metadata) != casks_identity
            ):
                raise ValueError("refusing Cask rollback after Casks rebinding")
            casks_fd = os.open(
                OUTPUT_PARTS[0],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        else:
            casks_fd = os.dup(inherited_casks_descriptor)
        if _identity(os.fstat(casks_fd)) != casks_identity:
            raise ValueError("held Casks descriptor did not match rollback binding")
        if cask_identity is None:
            try:
                os.stat(
                    OUTPUT_PARTS[1],
                    dir_fd=casks_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise ValueError("refusing to remove an unbound Cask entry")
        else:
            _unlink_exact(casks_fd, OUTPUT_PARTS[1], cask_identity)
            os.fsync(casks_fd)
        if remove_casks_directory:
            os.close(casks_fd)
            casks_fd = -1
            rebound_casks = os.stat(
                OUTPUT_PARTS[0],
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if _identity(rebound_casks) != casks_identity:
                raise ValueError("refusing Casks removal after directory rebinding")
            os.rmdir(OUTPUT_PARTS[0], dir_fd=root_fd)
            os.fsync(root_fd)
    finally:
        if casks_fd >= 0:
            os.close(casks_fd)
        os.close(root_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a verified DotSync macOS release Cask.",
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--expected-casks-dev", type=int)
    parser.add_argument("--expected-casks-ino", type=int)
    parser.add_argument("--casks-fd", type=int)
    return parser


def _rollback_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Internal exact-inode rollback for the macOS release transaction.",
    )
    parser.add_argument("--rollback-created", action="store_true", required=True)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--casks-dev", required=True, type=int)
    parser.add_argument("--casks-ino", required=True, type=int)
    parser.add_argument("--casks-fd", type=int)
    parser.add_argument("--cask-dev", type=int)
    parser.add_argument("--cask-ino", type=int)
    parser.add_argument("--remove-casks-directory", action="store_true")
    return parser


def _raise_signal(signal_number: int, _frame: object) -> None:
    raise InterruptedError(f"renderer interrupted by signal {signal_number}")


def _write_binding_to_stdout(binding: PublicationBinding) -> None:
    payload = (
        json.dumps(asdict(binding), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    _write_all(sys.stdout.fileno(), payload)
    sys.stdout.flush()


def main(arguments: list[str] | None = None) -> int:
    actual_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if actual_arguments[:1] == ["--rollback-created"]:
        namespace = _rollback_parser().parse_args(actual_arguments)
        try:
            if (namespace.cask_dev is None) != (namespace.cask_ino is None):
                raise ValueError("both Cask identity fields are required together")
            cask_identity = (
                None
                if namespace.cask_dev is None
                else (namespace.cask_dev, namespace.cask_ino)
            )
            rollback_created_cask(
                repository_root=namespace.repository_root,
                casks_identity=(namespace.casks_dev, namespace.casks_ino),
                cask_identity=cask_identity,
                remove_casks_directory=namespace.remove_casks_directory,
                inherited_casks_descriptor=namespace.casks_fd,
            )
        except (OSError, ValueError) as error:
            print(f"render_cask: {error}", file=sys.stderr)
            return 1
        return 0

    namespace = _parser().parse_args(actual_arguments)
    watched_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    prior_handlers = {
        signal_number: signal.signal(signal_number, _raise_signal)
        for signal_number in watched_signals
    }
    try:
        if (namespace.expected_casks_dev is None) != (
            namespace.expected_casks_ino is None
        ):
            raise ValueError("both expected Casks identity fields are required together")
        expected_casks_identity = (
            None
            if namespace.expected_casks_dev is None
            else (namespace.expected_casks_dev, namespace.expected_casks_ino)
        )
        _output, _binding = _render_cask_with_binding(
            version=namespace.version,
            sha256=namespace.sha256,
            url=namespace.url,
            output=namespace.output,
            repository_root=namespace.repository_root,
            replace_existing=namespace.replace_existing,
            binding_callback=_write_binding_to_stdout,
            expected_casks_identity=expected_casks_identity,
            inherited_casks_descriptor=namespace.casks_fd,
        )
    except (OSError, ValueError) as error:
        print(f"render_cask: {error}", file=sys.stderr)
        return 1
    finally:
        for signal_number, prior_handler in prior_handlers.items():
            signal.signal(signal_number, prior_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
