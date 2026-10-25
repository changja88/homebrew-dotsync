"""What is using ~/.codex. The Codex cleanup may run only when nothing is, so
this finds every process that runs a Codex binary or holds a file under the
Codex home. It only looks: it never stops or signals anything, and it reports
executable basenames, never arguments, environment or file paths."""

from __future__ import annotations

import os
from pathlib import Path

from dotsync.sessions import procs, roots, safety

CHATGPT_MAIN_SUFFIX = "ChatGPT.app/Contents/MacOS/ChatGPT"
CODEX_NAMES = ("codex", "codex-code-mode-host")
LOCK_SUFFIX = ".lock"


def detect() -> dict:
    """{"blockers", "chatgpt_helpers", "chatgpt_open_threads"}. When `ps` or
    `lsof` cannot answer, the one blocker is a `detection_failed` entry: not
    knowing is never reported as nothing running."""
    try:
        table = procs.ps_table()
    except Exception:  # any failure, not only ToolFailure: unknown means keep
        return _failed("ps")
    try:
        scan = procs.lsof_scan()
    except Exception:
        return _failed("lsof")

    me = os.getpid()
    pids = sorted((set(table) | set(scan)) - {me})
    exe_paths = {pid: _exe_path(pid, table, scan) for pid in pids}
    chatgpt_mains = {pid for pid in pids if exe_paths[pid].endswith(CHATGPT_MAIN_SUFFIX)}
    code_dirs = [os.path.realpath(roots.codex_home()), os.path.realpath(roots.codex_sqlite_home())]
    lock_dir = os.path.join(code_dirs[0], "thread-writer-locks")

    blockers: list[dict] = []
    helpers: list[dict] = []
    chatgpt_locks: set[str] = set()
    for pid in pids:
        under_chatgpt = pid in chatgpt_mains or any(
            a in chatgpt_mains for a in procs.ancestors(pid, table)
        )
        files = scan[pid].files if pid in scan else []
        if under_chatgpt:
            chatgpt_locks |= _thread_locks(files, lock_dir)
        name = procs.exe_name(pid, table, scan)
        if pid in chatgpt_mains or _runs_codex(name, exe_paths[pid]) or _holds_file(files, code_dirs):
            blockers.append(_entry(pid, _kind(pid, name, chatgpt_mains, under_chatgpt), name, table))
        elif under_chatgpt:
            helpers.append(_entry(pid, "chatgpt_helper", name, table))
    return {
        "blockers": blockers,
        "chatgpt_helpers": helpers,
        "chatgpt_open_threads": len(chatgpt_locks),
    }


def _failed(tool: str) -> dict:
    entry = {"pid": 0, "kind": "detection_failed", "name": tool, "elapsed": 0}
    return {"blockers": [entry], "chatgpt_helpers": [], "chatgpt_open_threads": 0}


def _entry(pid: int, kind: str, name: str, table: dict[int, procs.Proc]) -> dict:
    proc = table.get(pid)
    return {"pid": pid, "kind": kind, "name": name, "elapsed": proc.elapsed if proc else 0}


def _kind(pid: int, name: str, chatgpt_mains: set[int], under_chatgpt: bool) -> str:
    if pid in chatgpt_mains:
        return "chatgpt_app"
    if under_chatgpt:
        return "chatgpt_child"
    return "terminal_codex" if name == "codex" else "other"


def _exe_path(pid: int, table: dict[int, procs.Proc], scan: dict[int, procs.OpenFiles]) -> str:
    """The executable's full path: lsof's first txt mapping, else `comm`
    (which `ps` prints as a path)."""
    opened = scan.get(pid)
    if opened is not None and opened.txt:
        return opened.txt[0]
    proc = table.get(pid)
    return proc.comm if proc is not None else ""


def _runs_codex(name: str, exe_path: str) -> bool:
    if name in CODEX_NAMES:
        return True
    resolved = os.path.realpath(exe_path) if exe_path else ""
    if name == "SkyComputerUseService":
        return _is_under(resolved, os.path.realpath(roots.codex_home() / "computer-use"))
    if name == "node":
        app_support = Path.home() / "Library" / "Application Support" / "Codex"
        return _is_under(resolved, os.path.realpath(app_support))
    return False


def _holds_file(files: list[str], directories: list[str]) -> bool:
    return any(_is_under(os.path.realpath(f), d) for f in files for d in directories)


def _thread_locks(files: list[str], lock_dir: str) -> set[str]:
    """The `thread-writer-locks/<thread-id>.lock` files among `files`."""
    found: set[str] = set()
    for f in files:
        real = os.path.realpath(f)
        if os.path.dirname(real) == lock_dir and real.endswith(LOCK_SUFFIX):
            if safety.is_valid_id(os.path.basename(real)[: -len(LOCK_SUFFIX)]):
                found.add(real)
    return found


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")
