"""Tests for the thin macOS `security` (Keychain) wrapper.

Every test patches `dotsync.keychain.subprocess.run` — the autouse
`subprocess_blocked` fixture forbids real subprocess calls.
"""
from unittest.mock import MagicMock, patch

from dotsync import keychain


def _completed(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_read_secret_returns_value_and_builds_argv():
    with patch(
        "dotsync.keychain.subprocess.run", return_value=_completed(stdout="THEBLOB\n")
    ) as run:
        val = keychain.read_secret("svc", "acct")
    assert val == "THEBLOB"
    assert run.call_args[0][0] == [
        "security",
        "find-generic-password",
        "-s",
        "svc",
        "-a",
        "acct",
        "-w",
    ]


def test_read_secret_returns_none_when_absent():
    with patch(
        "dotsync.keychain.subprocess.run",
        return_value=_completed(returncode=44, stderr="not found"),
    ):
        assert keychain.read_secret("svc", "missing") is None


def test_write_secret_uses_update_flag_and_passes_secret_on_argv():
    with patch(
        "dotsync.keychain.subprocess.run", return_value=_completed()
    ) as run:
        keychain.write_secret("svc", "acct", "S3CRET-BLOB")
    argv = run.call_args[0][0]
    assert argv == [
        "security",
        "add-generic-password",
        "-U",
        "-s",
        "svc",
        "-a",
        "acct",
        "-w",
        "S3CRET-BLOB",
    ]


def test_write_secret_raises_on_failure():
    import pytest

    with patch(
        "dotsync.keychain.subprocess.run",
        return_value=_completed(returncode=1, stderr="boom"),
    ):
        with pytest.raises(keychain.KeychainError):
            keychain.write_secret("svc", "acct", "x")


def test_delete_secret_returns_true_on_success_and_builds_argv():
    with patch(
        "dotsync.keychain.subprocess.run", return_value=_completed()
    ) as run:
        assert keychain.delete_secret("svc", "acct") is True
    assert run.call_args[0][0] == [
        "security",
        "delete-generic-password",
        "-s",
        "svc",
        "-a",
        "acct",
    ]


def test_delete_secret_returns_false_when_absent():
    with patch(
        "dotsync.keychain.subprocess.run",
        return_value=_completed(returncode=44, stderr="not found"),
    ):
        assert keychain.delete_secret("svc", "missing") is False
