"""Inspect installed and latest Graphify releases without third-party deps."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

MINIMUM_VERSION = "0.9.14"
_PYPI_JSON_URL = "https://pypi.org/pypi/graphifyy/json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_CACHE_MISS = object()


def version_key(version: str | None) -> tuple[int, ...] | None:
    """Parse a stable dotted release for numeric comparison."""
    if (
        version is None
        or len(version) > 128
        or not re.fullmatch(r"\d+(?:\.\d+)+", version)
    ):
        return None
    try:
        parts = [int(part) for part in version.split(".")]
    except ValueError:
        return None
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def installed_version(command: list[str] | None) -> str | None:
    """Run a resolved Graphify command and parse its stable version."""
    if command is None:
        return None
    try:
        proc = subprocess.run(
            [*command, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = re.fullmatch(
        r"\s*graphify\s+v?(\d+(?:\.\d+)+)\s*",
        proc.stdout or "",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _cache_path() -> Path | None:
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    try:
        cache_root = (
            Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
        )
    except (OSError, RuntimeError):
        return None
    if not cache_root.is_absolute():
        return None
    return cache_root / "dotsync" / "graphify-version.json"


def _fetch_latest_version() -> str | None:
    try:
        with urllib.request.urlopen(_PYPI_JSON_URL, timeout=2) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) and version_key(version) else None


def _read_fresh_cache(path: Path, now: float) -> str | None | object:
    """Return cached value, including None; sentinel object means cache miss."""
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _CACHE_MISS
    if not isinstance(cached, dict):
        return _CACHE_MISS
    cached_at = cached.get("checked_at")
    cached_latest = cached.get("latest")
    if not isinstance(cached_at, (int, float)):
        return _CACHE_MISS
    if not 0 <= now - cached_at < _CACHE_TTL_SECONDS:
        return _CACHE_MISS
    if cached_latest is not None and (
        not isinstance(cached_latest, str) or version_key(cached_latest) is None
    ):
        return _CACHE_MISS
    return cached_latest


def _write_cache(path: Path, checked_at: float, latest: str | None) -> None:
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temp_file:
            json.dump({"checked_at": checked_at, "latest": latest}, temp_file)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def latest_version(
    *,
    cache_path: Path | None = None,
    now: float | None = None,
    fetch_version: Callable[[], str | None] | None = None,
) -> str | None:
    """Return the latest Graphify version with a 24-hour user cache."""
    path = cache_path if cache_path is not None else _cache_path()
    checked_at = time.time() if now is None else now
    if path is not None:
        try:
            cached = _read_fresh_cache(path, checked_at)
        except Exception:
            cached = _CACHE_MISS
        if cached is not _CACHE_MISS:
            return cached

    fetch = fetch_version or _fetch_latest_version
    try:
        latest = fetch()
    except Exception:
        latest = None
    if latest is not None and version_key(latest) is None:
        latest = None
    if path is not None:
        try:
            _write_cache(path, checked_at, latest)
        except Exception:
            pass
    return latest
