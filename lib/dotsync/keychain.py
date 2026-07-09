"""Thin wrapper over the macOS `security` CLI (generic passwords).

dotsync uses this to store/switch Claude Code account credentials, which live
in the login Keychain (never in a plaintext file). Pure argv construction over
`subprocess.run` so tests can patch `dotsync.keychain.subprocess.run`.

Why argv `-w <secret>` and not stdin: `security add-generic-password` reads an
interactive `-w` value from stdin capped at 128 chars, which silently truncates
(and corrupts) the ~900-byte OAuth blob. Passing the secret as an argv value is
the only form that round-trips exactly. The trade-off is a brief `ps` exposure
during the write; acceptable on a single-user machine where any same-user
process can already read the Keychain.
"""
from __future__ import annotations

import subprocess


class KeychainError(RuntimeError):
    """A `security` invocation failed (non-zero exit)."""


# `security` exit code for errSecItemNotFound — the item is genuinely absent.
# EVERY OTHER non-zero exit (45 ACL deny, 51 keychain locked, 128 dismissed
# prompt, ...) is a REAL error and must NOT be mistaken for "absent".
_ITEM_NOT_FOUND = 44


def read_secret(service: str, account: str) -> str | None:
    """Return the stored secret for (service, account), or None if genuinely absent.

    Raises `KeychainError` on any real read failure. This distinction is
    load-bearing: a caller that does read-modify-write over a consolidated item
    would, if a transient failure were reported as None (= "empty"), persist an
    empty value and destroy the stored data. Fail closed instead.
    """
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.rstrip("\n")
    if result.returncode == _ITEM_NOT_FOUND:
        return None
    raise KeychainError(
        f"failed to read keychain item {service}/{account}: "
        f"exit {result.returncode}: {result.stderr.strip() or 'unknown error'}"
    )


def write_secret(service: str, account: str, secret: str) -> None:
    """Create or update (service, account) with `secret`.

    Uses `-U` so an existing item is updated in place — critical for the live
    Claude item, whose access-control list must be preserved (a delete+add would
    strip it and lock Claude out).
    """
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-w",
            secret,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise KeychainError(
            f"failed to write keychain item {service}/{account}: "
            f"{result.stderr.strip() or 'unknown error'}"
        )


def delete_secret(service: str, account: str) -> bool:
    """Delete (service, account). Returns True if deleted, False if absent."""
    result = subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
