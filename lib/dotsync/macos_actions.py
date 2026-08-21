"""Fixed, non-shell macOS UI actions for the local DotSync application."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlsplit


_PROCESS_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}


def choose_sync_folder() -> Path | None:
    try:
        result = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'POSIX path of (choose folder with prompt "DotSync 동기화 폴더 선택")',
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
            env=_PROCESS_ENVIRONMENT,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("The sync-folder picker could not be opened.") from None
    if result.returncode == 1 and "(-128)" in result.stderr:
        return None
    if result.returncode != 0:
        raise RuntimeError("The sync-folder picker could not be opened.")
    if "\0" in result.stdout or len(result.stdout.encode("utf-8")) > 32_768:
        raise RuntimeError("The sync-folder picker returned an invalid result.")
    return Path(result.stdout.rstrip("\n"))


def reveal_in_finder(path: Path) -> None:
    _run_fixed_open(path)


def open_provider_login_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        raise RuntimeError("The provider login URL is invalid.") from None
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("The provider login URL is invalid.")
    _run_fixed_open(value)


def _run_fixed_open(value: str | Path) -> None:
    try:
        result = subprocess.run(
            ["/usr/bin/open", value],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=_PROCESS_ENVIRONMENT,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "The requested macOS action could not be completed."
        ) from None
    if result.returncode != 0:
        raise RuntimeError("The requested macOS action could not be completed.")
