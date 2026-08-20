"""Health checks for scoped Serena MCP servers."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def pid_is_alive(pid: int) -> bool:
    """Return true if a process id currently exists."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_identity(pid: int) -> str | None:
    """Return a high-resolution immutable start identity, or None if unusable.

    macOS uses libproc's microsecond process start timestamp; Linux uses the
    kernel start-tick field in ``/proc/<pid>/stat``. Other platforms fall back
    to ``ps lstart``. Command text is deliberately excluded because framework
    Python may re-exec with a different argv0 while retaining the same process
    identity. The PID plus immutable start data prevents PID-reuse mistakes;
    endpoint and dashboard probes provide the remaining health guarantees.
    """

    if pid <= 0:
        return None
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    if sys.platform.startswith("linux"):
        identity = _linux_process_identity(pid)
        if identity is not None:
            return identity
    return _portable_process_identity(pid)


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_process_identity(pid: int) -> str | None:
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = (
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        )
        proc_pidinfo.restype = ctypes.c_int
        info = _ProcBsdInfo()
        size = ctypes.sizeof(info)
        copied = proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if copied != size or info.pbi_pid != pid or info.pbi_status == 5:
        return None
    if info.pbi_start_tvsec <= 0 or info.pbi_start_tvusec >= 1_000_000:
        return None
    return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec:06d}"


def _linux_process_identity(pid: int) -> str | None:
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, UnicodeDecodeError):
        return None
    closing_paren = stat_line.rfind(")")
    if closing_paren < 0:
        return None
    fields = stat_line[closing_paren + 2 :].split()
    if len(fields) <= 19 or fields[0] == "Z":
        return None
    start_ticks = fields[19]
    if not start_ticks.isdigit():
        return None
    return f"linux:{start_ticks}"


def _portable_process_identity(pid: int) -> str | None:
    try:
        proc = subprocess.run(
            ["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    line = proc.stdout.strip()
    if not line:
        return None
    stat, _, rest = line.partition(" ")
    if "Z" in stat:
        return None
    identity = rest.strip()
    return f"ps:{identity}" if identity else None


def http_endpoint_alive(url: str, *, timeout: float = 1.0) -> bool:
    """Probe a streamable HTTP MCP endpoint with initialize."""

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "dotsync-serena-launcher", "version": "1"},
        },
    }).encode()
    request = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except HTTPError as exc:
        exc.close()
        return False
    except (OSError, URLError):
        return False


def dashboard_matches_project(
    dashboard_url: str,
    project_root: Path,
    *,
    timeout: float = 1.0,
) -> bool:
    """Return true when Serena dashboard reports this active project."""

    url = normalize_dashboard_url(dashboard_url) + "/get_config_overview"
    try:
        with urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        exc.close()
        return False
    except (OSError, URLError):
        return False
    if "Active Project: None" in body:
        return False
    expected = str(project_root.resolve())
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return expected in body and "Active Project: None" not in body
    active_project = data.get("active_project") if isinstance(data, dict) else None
    if not isinstance(active_project, dict):
        return False
    return active_project.get("path") == expected


def normalize_dashboard_url(url: str) -> str:
    """Normalize a Serena dashboard URL to scheme, host, and port."""

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid dashboard URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"
