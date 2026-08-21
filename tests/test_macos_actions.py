from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dotsync.macos_actions import (
    choose_sync_folder,
    open_provider_login_url,
    reveal_in_finder,
)


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_choose_sync_folder_uses_fixed_osascript_argv_and_sanitized_process(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(stdout="/Users/example/Sync Folder/\n")

    monkeypatch.setattr("dotsync.macos_actions.subprocess.run", run)

    selected = choose_sync_folder()

    assert selected == Path("/Users/example/Sync Folder")
    assert calls == [
        (
            [
                "/usr/bin/osascript",
                "-e",
                'POSIX path of (choose folder with prompt "DotSync 동기화 폴더 선택")',
            ],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 300,
                "env": {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                "shell": False,
            },
        )
    ]


def test_choose_sync_folder_maps_only_macos_user_cancellation_to_none(monkeypatch):
    monkeypatch.setattr(
        "dotsync.macos_actions.subprocess.run",
        lambda *args, **kwargs: _completed(returncode=1, stderr="execution error: (-128)"),
    )

    assert choose_sync_folder() is None


def test_choose_sync_folder_uses_fixed_failure_without_process_output(monkeypatch):
    monkeypatch.setattr(
        "dotsync.macos_actions.subprocess.run",
        lambda *args, **kwargs: _completed(returncode=2, stderr="private picker path"),
    )

    with pytest.raises(RuntimeError) as captured:
        choose_sync_folder()

    assert str(captured.value) == "The sync-folder picker could not be opened."
    assert "private picker path" not in str(captured.value)


@pytest.mark.parametrize("stdout", ["/tmp/bad\0path\n", "x" * 32_769])
def test_choose_sync_folder_rejects_unbounded_or_nul_results(monkeypatch, stdout):
    monkeypatch.setattr(
        "dotsync.macos_actions.subprocess.run",
        lambda *args, **kwargs: _completed(stdout=stdout),
    )

    with pytest.raises(RuntimeError, match="invalid result"):
        choose_sync_folder()


def test_reveal_in_finder_uses_fixed_open_argv_and_never_a_shell(monkeypatch):
    calls = []
    target = Path("/Users/example/Library/Application Support/DotSync")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed()

    monkeypatch.setattr("dotsync.macos_actions.subprocess.run", run)

    reveal_in_finder(target)

    assert calls == [
        (
            ["/usr/bin/open", target],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 30,
                "env": {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                "shell": False,
            },
        )
    ]


def test_provider_login_opens_only_an_https_url_with_a_hostname(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dotsync.macos_actions.subprocess.run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or _completed(),
    )

    open_provider_login_url("https://auth.example.test/device?code=public")

    assert calls[0][0] == [
        "/usr/bin/open",
        "https://auth.example.test/device?code=public",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "http://auth.example.test/device",
        "file:///tmp/provider",
        "/tmp/provider",
        "https:///missing-host",
    ],
)
def test_provider_login_rejects_non_https_or_hostless_values_without_opening(
    monkeypatch, value
):
    monkeypatch.setattr(
        "dotsync.macos_actions.subprocess.run",
        lambda *args, **kwargs: pytest.fail("invalid provider URL reached /usr/bin/open"),
    )

    with pytest.raises(RuntimeError) as captured:
        open_provider_login_url(value)

    assert str(captured.value) == "The provider login URL is invalid."


def test_open_failure_never_exposes_the_path_or_url(monkeypatch):
    private_url = "https://auth.example.test/private-value"
    monkeypatch.setattr(
        "dotsync.macos_actions.subprocess.run",
        lambda *args, **kwargs: _completed(returncode=3, stderr=private_url),
    )

    with pytest.raises(RuntimeError) as captured:
        open_provider_login_url(private_url)

    assert str(captured.value) == "The requested macOS action could not be completed."
    assert private_url not in str(captured.value)
