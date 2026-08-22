#!/usr/bin/env python3
"""Render the public DotSync Cask from verified release artifact facts."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
from pathlib import Path


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
    packaging = repository_root / TEMPLATE_PARTS[0]
    template_path = repository_root.joinpath(*TEMPLATE_PARTS)
    packaging_stat = os.lstat(packaging)
    template_stat = os.lstat(template_path)
    if not stat.S_ISDIR(packaging_stat.st_mode):
        raise ValueError("packaging must be a real directory")
    if not stat.S_ISREG(template_stat.st_mode):
        raise ValueError("Cask template must be a regular file")
    try:
        template = template_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Cask template must be UTF-8") from error
    if any(template.count(sentinel) != 1 for sentinel in SENTINELS):
        raise ValueError("Cask template must contain every sentinel exactly once")
    return template


def _render_template(template: str, version: str, sha256: str, url: str) -> bytes:
    rendered = (
        template.replace(SENTINELS[0], version)
        .replace(SENTINELS[1], sha256)
        .replace(SENTINELS[2], url)
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


def _publish_rendered_cask(
    repository_root: Path,
    payload: bytes,
    *,
    replace_existing: bool,
) -> None:
    root_fd = os.open(
        repository_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    casks_fd = -1
    temporary_name = f".dotsync-app.{secrets.token_hex(16)}.tmp"
    temporary_created = False
    try:
        root_stat = os.fstat(root_fd)
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

        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=casks_fd,
        )
        temporary_created = True
        try:
            _write_all(temporary_fd, payload)
            os.fchmod(temporary_fd, 0o644)
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)

        if replace_existing:
            os.replace(
                temporary_name,
                OUTPUT_PARTS[1],
                src_dir_fd=casks_fd,
                dst_dir_fd=casks_fd,
            )
            temporary_created = False
        else:
            os.link(
                temporary_name,
                OUTPUT_PARTS[1],
                src_dir_fd=casks_fd,
                dst_dir_fd=casks_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary_name, dir_fd=casks_fd)
            temporary_created = False
        os.fsync(casks_fd)

        final_stat = os.stat(
            OUTPUT_PARTS[1],
            dir_fd=casks_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(final_stat.st_mode):
            raise ValueError("rendered Cask is not a regular file")
        if stat.S_IMODE(final_stat.st_mode) != 0o644 or final_stat.st_nlink != 1:
            raise ValueError("rendered Cask does not have exact 0644 ownership shape")
        final_casks_stat = os.stat(
            OUTPUT_PARTS[0],
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if (
            final_casks_stat.st_dev,
            final_casks_stat.st_ino,
        ) != (
            opened_casks_stat.st_dev,
            opened_casks_stat.st_ino,
        ):
            raise ValueError("Casks directory binding changed during publication")
    finally:
        if temporary_created and casks_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=casks_fd)
            except FileNotFoundError:
                pass
        if casks_fd >= 0:
            os.close(casks_fd)
        os.close(root_fd)


def render_cask(
    *,
    version: str,
    sha256: str,
    url: str,
    output: Path,
    repository_root: Path,
    replace_existing: bool = False,
) -> Path:
    """Render exact release facts without network access or checksum inference."""
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
    _publish_rendered_cask(root, payload, replace_existing=replace_existing)
    return exact_output


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
    return parser


def main(arguments: list[str] | None = None) -> int:
    namespace = _parser().parse_args(arguments)
    try:
        render_cask(
            version=namespace.version,
            sha256=namespace.sha256,
            url=namespace.url,
            output=namespace.output,
            repository_root=namespace.repository_root,
            replace_existing=namespace.replace_existing,
        )
    except (OSError, ValueError) as error:
        print(f"render_cask: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
