"""Deleting Claude sessions, and putting a half-deleted one back.

`delete` moves a session's transcripts aside, removes everything the session
owns, and keeps whatever another session still refers to. `restore` undoes a
delete that was stopped half way. What a session is, and whether it is still
in use, is decided by `claude` (scan, liveness, state)."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import os
import re
import stat
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from dotsync import locks
from dotsync.cli_json import now_iso
from dotsync.sessions import claude, log, roots, safety
from dotsync.sessions.stop import StopSignal

DELETABLE = ("idle", "interrupted")
_REF = re.compile(rf"/({claude._UUID})/tool-results/".encode())
_REF_MARK = b"/tool-results/"
_CHUNK = 8 * 1024 * 1024
_OVERLAP = 64  # longer than a whole reference, so none is cut in two
_RENAME_EXCL = 0x00000004  # <sys/stdio.h>


def tool_result_refs(files: Iterable[Path]) -> dict[str, set[str]]:
    """Referenced id → the ids whose transcripts name `/<id>/tool-results/`.
    A byte search: nothing else is taken from a transcript. A file that cannot
    be read raises OSError, since what it references is then unknown."""
    refs: dict[str, set[str]] = {}
    for path in files:
        owner = _transcript_owner(path)
        for ref in _refs_in(path):
            refs.setdefault(ref, set()).add(owner)
    return refs


def _refs_in(path: Path) -> set[str]:
    found: set[str] = set()
    # O_NOFOLLOW: a symlink in a transcript's place is not read through.
    with open(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
        carry = b""
        while chunk := f.read(_CHUNK):
            data = carry + chunk
            if _REF_MARK in data:  # a cheap test first: most chunks hold none
                found.update(m.group(1).decode() for m in _REF.finditer(data))
            carry = data[-_OVERLAP:]
    return found


def _transcript_owner(path: Path) -> str:
    """The id a transcript belongs to, or "" when its path does not say: its
    references then count against every id."""
    if path.parent.name == "subagents" and safety.is_valid_id(path.parent.parent.name):
        return path.parent.parent.name
    m = claude._TRANSCRIPT.match(path.name)
    return m.group(1) if m else ""


def _transcript_files() -> list[Path]:
    """Every top-level transcript (active or quarantined) and every subagent
    transcript. Unlike scan(), this fails instead of skipping: a folder that
    cannot be listed, or a symlink where a folder should be, raises OSError."""
    projects = roots.claude_home() / "projects"
    files: list[Path] = []
    for folder in _strict_listdir(projects):
        p = projects / folder
        if not _is_real_dir(p):
            continue
        for name in _strict_listdir(p):
            if claude._TRANSCRIPT.match(name):
                files.append(p / name)
            elif safety.is_valid_id(name) and _is_real_dir(p / name):
                sub = p / name / "subagents"
                if _is_real_dir(sub):
                    files.extend(sub / n for n in _strict_listdir(sub) if n.endswith(".jsonl"))
    return files


def _strict_listdir(directory: Path) -> list[str]:
    """Like _listdir, but only a missing folder gives []: any other error
    raises."""
    try:
        return sorted(os.listdir(directory))
    except FileNotFoundError:
        return []


def _is_real_dir(path: Path) -> bool:
    """Whether `path` is a directory. A symlink raises OSError: it is not
    followed, so what is behind it is unknown."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode):
        raise OSError(errno.ELOOP, "symlink not followed", str(path))
    return stat.S_ISDIR(mode)


@dataclass
class _Batch:
    """What one delete gathers once, before the first id."""

    sessions: dict[str, claude.ClaudeSession]
    live: claude.Liveness
    now: float
    refs: dict[str, set[str]] | None  # None: a transcript could not be read
    cwds: set[str]  # of listed sessions, agents and claude processes


def _gather() -> _Batch:
    now = time.time()
    sessions = {s.id: s for s in claude.scan()}
    live = claude.gather_liveness(now)
    refs: dict[str, set[str]] | None
    try:
        refs = tool_result_refs(_transcript_files())
    except OSError:
        refs = None
    cwds = {s.cwd for s in sessions.values() if s.cwd} | live.known_cwds
    return _Batch(sessions, live, now, refs, cwds)


def delete(
    ids: list[str],
    *,
    stop: StopSignal,
    progress: Callable[[int, int], None],
    log_command: str = "delete",
) -> dict:
    """Delete each session that is still idle or interrupted once its
    transcripts are set aside, keeping what other sessions use. The report,
    plus the paths removed, goes to the result log under `log_command`."""
    for sid in ids:
        safety.validate_id(sid)  # one bad id and nothing is touched
    # Once per id: a repeat would be judged on the batch-start snapshot again,
    # after its first turn may have found it running.
    ids = list(dict.fromkeys(ids))
    started_at = now_iso()
    results: list[dict] = []
    report = {"tool": "claude", "results": results, "stopped": False}
    removed: dict[str, list[str]] = {}
    with locks.tool_lock("claude-sessions", shared=False):
        try:
            batch = _gather()
            for done, sid in enumerate(ids, start=1):
                if stop.requested:
                    report["stopped"] = True
                    results.extend(_skipped(rest, "stopped") for rest in ids[done - 1 :])
                    break
                result, gone = _delete_one(sid, batch)
                results.append(result)
                if gone:
                    removed[sid] = [str(p) for p in gone]
                progress(done, len(ids))
        finally:
            log.write(log_command, "claude", started_at, {**report, "removed": removed})
    return report


def restore(sid: str) -> dict:
    """Undo a delete that stopped halfway: rename every quarantined transcript
    of `sid` back. A copy whose `<id>.jsonl` exists again stays as it is, with
    the existing file, and is reported in `failed`. The outcome is `restored`
    only when every copy came back."""
    safety.validate_id(sid)
    started_at = now_iso()
    with locks.tool_lock("claude-sessions", shared=False):
        result = _restore_one(sid)
        report = {"tool": "claude", "results": [result], "stopped": False}
        log.write("recover", "claude", started_at, report)
    return report


def _restore_one(sid: str) -> dict:
    entry = claude._transcripts_by_id().get(sid)
    asides = entry["quarantined"] if entry else []
    if not asides:
        return {"id": sid, "outcome": "skipped", "reason": "not_found"}
    failed: list[dict] = []
    for aside in asides:
        path = aside.with_name(aside.name.removesuffix(claude.QUARANTINE_SUFFIX))
        try:
            _rename_no_replace(aside, path)
        except OSError as e:
            failed.append(_failure(aside, e))
    if failed:
        return {"id": sid, "outcome": "failed", "failed": failed}
    return {"id": sid, "outcome": "restored"}


def _delete_one(sid: str, batch: _Batch) -> tuple[dict, list[Path]]:
    """One id's result, and the paths it removed."""
    s = batch.sessions.get(sid)
    if s is None:
        return _skipped(sid, "not_found"), []
    # Already in use when the batch started: not even renamed.
    reason = _not_deletable(s, batch.live, batch.now)
    if reason:
        return _skipped(sid, reason), []
    moved, failed = _quarantine(s.transcripts)
    if failed:
        return _skipped(sid, "rename_failed", failed), []
    now = time.time()
    live = claude.gather_liveness(now)
    # Scanned after the liveness check, so it sees every write until now,
    # including one from a process still writing to the renamed file.
    fresh = claude._scan_one(sid)
    reason = _not_deletable(fresh, live, now) if fresh else "not_found"
    if reason is None and fresh.transcripts:
        # created after the rename: a transcript not set aside is never deleted
        reason = "unknown:new_transcript"
    if reason:
        return _skipped(sid, reason, _restore(moved)), []
    return _remove(fresh, batch.refs, batch.cwds | live.known_cwds)


def _not_deletable(s: claude.ClaudeSession, live: claude.Liveness, now: float) -> str | None:
    """None when `s` may be deleted, else the reason it is skipped."""
    state, reason = claude.state_of(s, live, now)
    if state in DELETABLE:
        return None
    return f"unknown:{reason}" if state == "unknown" else state


def _skipped(sid: str, reason: str, failed: list[dict] | None = None) -> dict:
    result: dict = {"id": sid, "outcome": "skipped", "reason": reason}
    if failed:
        result["failed"] = failed
    result["freed_bytes"] = 0
    return result


def _quarantine(transcripts: list[Path]) -> tuple[list[tuple[Path, Path]], list[dict]]:
    """Rename every transcript to its quarantine name, so `claude --resume`
    finds nothing to open. On a failure the ones already renamed go back and
    the failures are returned instead."""
    moved: list[tuple[Path, Path]] = []
    for path in transcripts:
        aside = path.with_name(path.name + claude.QUARANTINE_SUFFIX)
        try:
            _rename_no_replace(path, aside)
        except OSError as e:
            return [], [_failure(path, e), *_restore(moved)]
        moved.append((path, aside))
    return moved, []


def _restore(moved: list[tuple[Path, Path]]) -> list[dict]:
    """Rename quarantined transcripts back. A name taken in the meantime is
    reported, and both files are left as they are."""
    failed: list[dict] = []
    for path, aside in moved:
        try:
            _rename_no_replace(aside, path)
        except OSError as e:
            failed.append(_failure(aside, e))
    return failed


@functools.cache
def _renamex_np():
    """libc's renamex_np, resolved once. AttributeError when libc lacks it."""
    rename = ctypes.CDLL(None, use_errno=True).renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    return rename


def _rename_no_replace(src: Path, dst: Path) -> None:
    """Rename in one step, or raise FileExistsError when `dst` exists.
    os.rename would replace it: an older quarantined copy, or a transcript
    Claude created again after the first rename."""
    try:
        rename = _renamex_np()
    except AttributeError as e:  # not macOS: a failure of this path, not a crash
        raise OSError(errno.ENOTSUP, f"renamex_np is not available: {e}", str(src), None, str(dst))
    if rename(os.fsencode(src), os.fsencode(dst), _RENAME_EXCL) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(src), None, str(dst))


def _failure(path: Path, error: object) -> dict:
    return {"path": str(path), "error": str(error).replace("\n", " ")}


def _remove(
    s: claude.ClaudeSession, refs: dict[str, set[str]] | None, cwds: set[str]
) -> tuple[dict, list[Path]]:
    """Remove what `s` owns except what other sessions use. Its quarantined
    transcripts go last, and only when everything else went: otherwise it
    stays listed as interrupted, and deleting it again retries what is left."""
    kept_paths, kept = _kept(s, refs, cwds)
    others = [p for p in s.paths if p not in kept_paths and p not in s.quarantined]
    freed, removed, failed = _remove_all(others)
    for folder in s.project_dirs:
        with contextlib.suppress(OSError, safety.UnsafePath):
            os.rmdir(safety.child(folder, s.id))  # only once it is empty
    if not failed:
        more, removed_last, failed = _remove_all(s.quarantined)
        freed += more
        removed += removed_last
    result: dict = {"id": s.id, "outcome": "partial" if failed else "deleted"}
    if kept:
        result["kept"] = kept
    if failed:
        result["failed"] = failed
    result["freed_bytes"] = freed
    return result, removed


def _remove_all(paths: Iterable[Path]) -> tuple[int, list[Path], list[dict]]:
    """Bytes freed, the paths removed, and the failures."""
    freed, removed, failed = 0, [], []
    for path in paths:
        try:
            safety.child(path.parent, path.name)  # no symlink crept into its parents
        except safety.UnsafePath as e:
            failed.append(_failure(path, e))
            continue
        before = _bytes_under(path)
        error = safety.remove(path)
        if error:
            freed += before - _bytes_under(path)
            failed.append(_failure(path, error))
        else:
            freed += before
            removed.append(path)
    return freed, removed, failed


def _bytes_under(path: Path) -> int:
    return sum(st.st_size for st in claude._files_under(path))


def _kept(
    s: claude.ClaudeSession, refs: dict[str, set[str]] | None, cwds: set[str]
) -> tuple[set[Path], list[str]]:
    """Owned paths other sessions may still use, and the parts they are:
    `tool-results` another transcript references (every one, when the
    references are unknown), and a temp dir a known cwd is inside. A
    reference from another session in the same batch still counts."""
    paths: set[Path] = set()
    parts: list[str] = []
    if refs is None or refs.get(s.id, set()) - {s.id}:
        results = {folder / s.id / "tool-results" for folder in s.project_dirs} & set(s.paths)
        if results:
            paths |= results
            parts.append("tool_results")
    temp_root = roots.claude_temp_root()
    temps = {
        p
        for p in s.paths
        if temp_root is not None and p.parent.parent == temp_root and _holds_any(p, cwds)
    }
    if temps:
        paths |= temps
        parts.append("temp_dir")
    return paths, parts


def _holds_any(directory: Path, cwds: Iterable[str]) -> bool:
    """Whether a cwd is `directory` or below it, as written or with symlinks
    such as /tmp → /private/tmp resolved."""
    tops = {str(directory), os.path.realpath(directory)}
    for cwd in cwds:
        spellings = {cwd}
        if cwd.startswith("/"):
            with contextlib.suppress(ValueError):  # a NUL byte
                spellings.add(os.path.realpath(cwd))
        if any(c == t or c.startswith(t + "/") for c in spellings for t in tops):
            return True
    return False
