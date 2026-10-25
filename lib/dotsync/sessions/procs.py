"""Child processes with timeouts, and the `ps` / `lsof` views of what runs."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

TOOL_TIMEOUT = 10  # seconds, for ps and lsof
_CHUNK = 65536  # bytes per read of a streamed child's stdout


class ToolFailure(Exception):
    """A child process could not give an answer. `code` is one of
    "missing" (not installed), "timeout", "failed" (ran, but not cleanly)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def run(
    argv: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `argv` to completion. The caller judges the return code."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure("timeout", f"{argv[0]} timed out") from exc
    except FileNotFoundError as exc:
        raise ToolFailure("missing", f"{argv[0]} not found") from exc
    except OSError as exc:
        raise ToolFailure("failed", f"{argv[0]} could not start: {exc.strerror}") from exc


def stream_lines(argv: list[str], *, timeout: float) -> Generator[bytes, None, None]:
    """The lines `argv` writes to stdout, each as it arrives (the last one may
    lack its newline). Reads wait in a selector, never in a blocking
    readline, so a child that writes no newline still meets the deadline.
    Passing the deadline kills the child and raises ToolFailure("timeout");
    closing the generator early kills it too. A non-zero exit raises
    ToolFailure("failed") after the last line, as does a failed read. Either
    way the child is reaped before the generator ends."""
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except FileNotFoundError as exc:
        raise ToolFailure("missing", f"{argv[0]} not found") from exc
    except OSError as exc:
        raise ToolFailure("failed", f"{argv[0]} could not start: {exc.strerror}") from exc
    try:
        yield from _read_lines(child.stdout.fileno(), deadline, argv[0])
        try:
            code = child.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired as exc:  # stdout closed, but it has not exited
            raise ToolFailure("timeout", f"{argv[0]} timed out") from exc
        if code != 0:
            raise ToolFailure("failed", f"{argv[0]} exited {code}")
    except OSError as exc:  # reading its stdout failed
        raise ToolFailure("failed", f"{argv[0]} could not be read: {exc.strerror}") from exc
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()


def _read_lines(fd: int, deadline: float, name: str) -> Generator[bytes, None, None]:
    tail = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not selector.select(left):
                raise ToolFailure("timeout", f"{name} timed out")
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            tail += chunk
            if b"\n" in chunk:  # only then can a line be complete
                *lines, rest = tail.split(b"\n")
                for line in lines:
                    yield bytes(line) + b"\n"
                tail = bytearray(rest)
    if tail:
        yield bytes(tail)


def _run_clean(argv: list[str]) -> str:
    """Run a system tool whose healthy run exits 0 with an empty stderr."""
    env = {**os.environ, "LC_ALL": "C"}
    result = run(argv, timeout=TOOL_TIMEOUT, env=env)
    if result.returncode != 0 or result.stderr:
        raise ToolFailure("failed", f"{argv[0]} exited {result.returncode}")
    return result.stdout


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    elapsed: int  # seconds
    comm: str


def _parse_etime(text: str) -> int:
    """`[[dd-]hh:]mm:ss` to seconds."""
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        days = int(day_text)
    seconds = 0
    for part in text.split(":"):
        seconds = seconds * 60 + int(part)
    return days * 86400 + seconds


def ps_table() -> dict[int, Proc]:
    out = _run_clean(["/bin/ps", "-axo", "pid=,ppid=,etime=,comm="])
    table: dict[int, Proc] = {}
    for line in out.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
            elapsed = _parse_etime(fields[2])
        except ValueError as exc:
            raise ToolFailure("failed", "unreadable ps line") from exc
        table[pid] = Proc(pid, ppid, elapsed, fields[3])
    return table


@dataclass
class OpenFiles:
    command: str
    cwd: str | None = None
    txt: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def lsof_scan(fds: str | None = None) -> dict[int, OpenFiles]:
    argv = ["/usr/sbin/lsof", "-nP", "-w", "-u", str(os.getuid()), "-F", "pcfn"]
    if fds:
        argv += ["-a", "-d", fds]
    out = _run_clean(argv)
    scan: dict[int, OpenFiles] = {}
    current: OpenFiles | None = None
    fd = ""
    for line in out.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError as exc:
                raise ToolFailure("failed", "unreadable lsof record") from exc
            current = scan[pid] = OpenFiles(command="")
        elif current is None:
            continue
        elif tag == "c":
            current.command = value
        elif tag == "f":
            fd = value
        elif tag == "n":
            if fd == "cwd":
                current.cwd = value
            elif fd == "txt":
                current.txt.append(value)
            else:
                current.files.append(value)
    return scan


def ancestors(pid: int, table: dict[int, Proc]) -> list[int]:
    """Parent, grandparent, ...; stops at pid 1, at an unknown pid, or at a loop."""
    chain: list[int] = []
    seen = {pid}
    current = table.get(pid)
    while current is not None and current.ppid > 1 and current.ppid not in seen:
        chain.append(current.ppid)
        seen.add(current.ppid)
        current = table.get(current.ppid)
    return chain


def exe_name(pid: int, table: dict[int, Proc], scan: dict[int, OpenFiles]) -> str:
    """The executable's basename: the first txt mapping if lsof saw one,
    else `comm` (which can be a launcher such as `node`). A pid in neither
    (it exited between the scans) gives "": an empty name matches no tool, so
    callers treat the process as unidentified."""
    opened = scan.get(pid)
    if opened is not None and opened.txt:
        return os.path.basename(opened.txt[0])
    proc = table.get(pid)
    return os.path.basename(proc.comm) if proc is not None else ""
