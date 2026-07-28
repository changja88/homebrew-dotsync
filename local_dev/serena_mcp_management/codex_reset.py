"""Hard reset of every persisted Codex session and conversation trace."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import tomllib
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .agent_paths import canonical_codex_homes, lexical_codex_homes
from .memory_management import ClientProcess, running_client_processes
from .serena_mcp.health import pid_is_alive, process_identity
from .serena_mcp.paths import find_project_root
from .serena_mcp.termination import terminate_pid


_STATE_DB_RE = re.compile(r"state_\d+\.sqlite")
_RESET_DB_RE = re.compile(
    r"(?:goals|logs|memories|state)_\d+\.sqlite(?:-(?:journal|shm|wal))?"
)
_CONFIGURED_LOG_FILE_RE = re.compile(r"codex-tui\.log(?:\..+)?")
_FULL_RESET_DIRECTORY_NAMES = (
    "sessions",
    "archived_sessions",
    "memories",
    "memories_extensions",
    "shell_snapshots",
    "session_snapshots",
    "snapshots",
    "visualizations",
    "log",
    "logs",
    "ambient-suggestions",
    "process_manager",
)
_FULL_RESET_FILE_NAMES = (
    "history.jsonl",
)
_GLOBAL_STATE_FILE_NAMES = (
    ".codex-global-state.json",
    ".codex-global-state.json.bak",
)
_GLOBAL_STATE_TOP_LEVEL_TRACE_KEYS = frozenset(
    {
        "queued-follow-ups",
    }
)
_GLOBAL_STATE_ATOM_TRACE_KEYS = frozenset(
    {
        "composer-prompt-drafts-v1",
        "heartbeat-thread-permissions-by-id",
        "prompt-history",
        "thread-summary-panel-section-expanded-artifacts",
        "unread-thread-ids-by-host-v1",
    }
)
_GLOBAL_STATE_ATOM_TRACE_PREFIXES = (
    "thread-browser-tabs-v1:",
)
_DESKTOP_PRESERVED_TABLES = (
    "automations",
    "local_app_server_feature_enablement",
)
_PRESERVED_CODEX_DIRECTORY_NAMES = (
    "plugins",
    "skills",
)
_DEFAULT_SYSTEM_CONFIG_PATH = Path("/private/etc/codex/config.toml")
_CTL_KERN = 1
_KERN_PROCARGS2 = 49


@dataclass(frozen=True)
class CodexCatalogFile:
    session_id: str
    parent_id: str | None
    path: Path
    codex_home: Path
    cwd: str
    updated_ns: int
    archived: bool


@dataclass(frozen=True)
class CodexCatalogThread:
    session_id: str
    parent_id: str | None
    codex_home: Path
    state_db: Path
    cwd: str
    preview: str
    updated_ns: int
    archived: bool


@dataclass(frozen=True)
class CodexCatalogOwner:
    codex_home: Path
    delete_ids: tuple[str, ...]


@dataclass(frozen=True)
class CodexSessionSummary:
    root_id: str
    cwd: str
    preview: str
    updated_ns: int
    archived: bool
    files: tuple[CodexCatalogFile, ...]
    owners: tuple[CodexCatalogOwner, ...]


@dataclass(frozen=True)
class CodexSessionCatalog:
    homes: tuple[Path, ...]
    sessions: tuple[CodexSessionSummary, ...]
    app_log_root: Path | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodexResetResult:
    discovered_sessions: int = 0
    deleted_sessions: int = 0
    deleted_trace_targets: int = 0
    terminated_processes: int = 0
    desktop_restarted: bool = False
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class _ResetTarget:
    path: Path
    allowed_root: Path
    kind: str


@dataclass(frozen=True)
class _RuntimeInvocation:
    working_directory: Path
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _PinnedRuntime:
    process: ClientProcess
    identity: str


@dataclass(frozen=True)
class _RuntimeSnapshot:
    pinned: tuple[_PinnedRuntime, ...]
    invocations: tuple[_RuntimeInvocation, ...]
    desktop_was_running: bool = False


@dataclass(frozen=True)
class _RuntimeTermination:
    terminated: int = 0
    invocations: tuple[_RuntimeInvocation, ...] = ()
    desktop_was_running: bool = False


def _normalized_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _find_parent_thread_id(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("parent_thread_id", "parentThreadId"):
            parent = _normalized_uuid(value.get(key))
            if parent is not None:
                return parent
        for child in value.values():
            parent = _find_parent_thread_id(child)
            if parent is not None:
                return parent
    elif isinstance(value, list):
        for child in value:
            parent = _find_parent_thread_id(child)
            if parent is not None:
                return parent
    return None


def _safe_label(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    printable = "".join(
        character if character.isprintable() else " "
        for character in value
    )
    collapsed = " ".join(printable.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _read_history_previews(
    homes: tuple[Path, ...],
    warnings: list[str],
) -> dict[str, str]:
    previews: dict[str, str] = {}
    for codex_home in homes:
        path = codex_home / "history.jsonl"
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            warnings.append(f"cannot inspect Codex history {path}: {exc}")
            continue
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(
            path_stat.st_mode
        ):
            warnings.append(f"unsafe Codex history path: {path}")
            continue
        try:
            with path.open(encoding="utf-8") as history:
                for line in history:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    session_id = _normalized_uuid(row.get("session_id"))
                    preview = _safe_label(row.get("text"), limit=72)
                    if session_id is not None and preview:
                        previews[session_id] = preview
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"cannot read Codex history {path}: {exc}")
    return previews


def _read_catalog_files(
    homes: tuple[Path, ...],
    warnings: list[str],
) -> list[CodexCatalogFile]:
    files: list[CodexCatalogFile] = []
    for codex_home in homes:
        for directory_name, archived in (
            ("sessions", False),
            ("archived_sessions", True),
        ):
            directory = codex_home / directory_name
            try:
                directory_stat = directory.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                warnings.append(
                    f"cannot inspect Codex session directory {directory}: {exc}"
                )
                continue
            if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(
                directory_stat.st_mode
            ):
                warnings.append(f"unsafe Codex session directory: {directory}")
                continue
            try:
                paths = sorted(directory.rglob("*.jsonl"))
            except OSError as exc:
                warnings.append(
                    f"cannot enumerate Codex session directory {directory}: {exc}"
                )
                continue
            for path in paths:
                try:
                    path_stat = path.lstat()
                    if not stat.S_ISREG(path_stat.st_mode):
                        raise ValueError("rollout is not a regular file")
                    with path.open(encoding="utf-8") as rollout:
                        row = json.loads(rollout.readline())
                    payload = row.get("payload")
                    if (
                        row.get("type") != "session_meta"
                        or not isinstance(payload, dict)
                    ):
                        raise ValueError("first row is not session_meta")
                    session_id = _normalized_uuid(payload.get("id"))
                    if session_id is None:
                        raise ValueError("session UUID is missing or invalid")
                    files.append(
                        CodexCatalogFile(
                            session_id=session_id,
                            parent_id=_find_parent_thread_id(payload),
                            path=path,
                            codex_home=codex_home,
                            cwd=_safe_label(payload.get("cwd"), limit=160),
                            updated_ns=path_stat.st_mtime_ns,
                            archived=archived,
                        )
                    )
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ValueError,
                ) as exc:
                    warnings.append(
                        f"cannot read Codex rollout {path.name}: {exc}"
                    )
    return files


def _read_catalog_threads(
    homes: tuple[Path, ...],
) -> list[CodexCatalogThread]:
    threads: list[CodexCatalogThread] = []
    for codex_home in homes:
        try:
            candidates = tuple(
                path
                for path in codex_home.iterdir()
                if _STATE_DB_RE.fullmatch(path.name)
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(
                f"cannot enumerate Codex state databases in {codex_home}: {exc}"
            ) from exc

        for state_db in sorted(candidates):
            try:
                state_stat = state_db.lstat()
            except OSError as exc:
                raise RuntimeError(
                    f"cannot inspect Codex state database {state_db}: {exc}"
                ) from exc
            if stat.S_ISLNK(state_stat.st_mode) or not stat.S_ISREG(
                state_stat.st_mode
            ):
                raise RuntimeError(
                    f"unsafe Codex state database: {state_db}"
                )

            try:
                with sqlite3.connect(
                    f"{state_db.as_uri()}?mode=ro",
                    uri=True,
                    timeout=1,
                ) as connection:
                    connection.execute("BEGIN")
                    rows = connection.execute(
                        """
                        SELECT
                            id, cwd, title, preview, first_user_message,
                            updated_at, updated_at_ms, archived
                        FROM threads
                        """
                    ).fetchall()
                    edge_rows = connection.execute(
                        """
                        SELECT parent_thread_id, child_thread_id
                        FROM thread_spawn_edges
                        """
                    ).fetchall()
            except sqlite3.Error as exc:
                raise RuntimeError(
                    f"cannot read Codex state database {state_db}: {exc}"
                ) from exc

            parents: dict[str, str] = {}
            for parent_value, child_value in edge_rows:
                parent = _normalized_uuid(parent_value)
                child = _normalized_uuid(child_value)
                if parent is None or child is None:
                    raise RuntimeError(
                        "invalid spawn edge in Codex state database "
                        f"{state_db}"
                    )
                previous = parents.setdefault(child, parent)
                if previous != parent:
                    raise RuntimeError(
                        f"conflicting spawn edge for Codex thread {child}"
                    )

            for row in rows:
                session_id = _normalized_uuid(row[0])
                if session_id is None:
                    raise RuntimeError(
                        "invalid thread UUID in Codex state database "
                        f"{state_db}"
                    )
                try:
                    updated_ms = (
                        int(row[6])
                        if row[6] is not None
                        else int(row[5]) * 1_000
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "invalid thread timestamp in Codex state database "
                        f"{state_db}"
                    ) from exc
                preview = row[3] or row[2] or row[4]
                threads.append(
                    CodexCatalogThread(
                        session_id=session_id,
                        parent_id=parents.get(session_id),
                        codex_home=codex_home,
                        state_db=state_db,
                        cwd=_safe_label(row[1], limit=160),
                        preview=_safe_label(preview, limit=72),
                        updated_ns=updated_ms * 1_000_000,
                        archived=bool(row[7]),
                    )
                )
    return threads


def _group_catalog_entries(
    files: list[CodexCatalogFile],
    threads: list[CodexCatalogThread],
    warnings: list[str],
) -> tuple[dict[str, list[str]], dict[str, str | None]]:
    all_ids = {
        *(item.session_id for item in files),
        *(item.session_id for item in threads),
    }
    parent_candidates: dict[str, set[str | None]] = {
        session_id: set() for session_id in all_ids
    }
    for item in (*files, *threads):
        parent_candidates[item.session_id].add(item.parent_id)
    parents: dict[str, str | None] = {}
    for session_id, candidates in parent_candidates.items():
        concrete = candidates - {None}
        if len(concrete) > 1:
            raise RuntimeError(
                f"conflicting parent for Codex session {session_id}"
            )
        parents[session_id] = next(iter(concrete), None)

    invalid: set[str] = set()
    groups: dict[str, list[str]] = {}
    for session_id in sorted(all_ids):
        if session_id in invalid:
            continue
        current = session_id
        trail: list[str] = []
        seen: set[str] = set()
        while current in all_ids:
            if current in seen or current in invalid:
                warnings.append(f"parent cycle for Codex session {session_id}")
                invalid.update(trail)
                break
            seen.add(current)
            trail.append(current)
            parent = parents.get(current)
            if parent is None:
                groups.setdefault(current, []).append(session_id)
                break
            if parent not in all_ids:
                groups.setdefault(parent, []).append(session_id)
                break
            current = parent

    if invalid:
        for root_id in tuple(groups):
            groups[root_id] = [
                session_id
                for session_id in groups[root_id]
                if session_id not in invalid
            ]
            if not groups[root_id]:
                del groups[root_id]
    for group_ids in groups.values():
        group_ids.sort()
    return groups, parents


def _depth(
    session_id: str,
    *,
    parents: dict[str, str | None],
    group_ids: set[str],
) -> int:
    depth = 0
    current = session_id
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        parent = parents.get(current)
        if parent is None or parent not in group_ids:
            return depth
        depth += 1
        current = parent
    return depth


def scan_codex_session_catalog(
    *,
    home: Path,
    codex_home: Path,
    orca_codex_home: Path | None = None,
    _validated_homes: tuple[Path, ...] | None = None,
) -> CodexSessionCatalog:
    """List every persisted Codex logical session, including archived ones."""
    warnings: list[str] = []
    user_home = home.absolute()
    if _validated_homes is None:
        discovered_homes, _, _ = canonical_codex_homes(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
        )
        homes = tuple(
            candidate
            for candidate in discovered_homes
            if not _unsafe_broad_codex_home(
                candidate,
                user_home=user_home,
                warnings=warnings,
            )
        )
    else:
        homes = _validated_homes
    files = _read_catalog_files(homes, warnings)
    threads = _read_catalog_threads(homes)
    previews = _read_history_previews(homes, warnings)
    groups, parents = _group_catalog_entries(files, threads, warnings)
    files_by_id: dict[str, list[CodexCatalogFile]] = {}
    for item in files:
        files_by_id.setdefault(item.session_id, []).append(item)
    threads_by_id: dict[str, list[CodexCatalogThread]] = {}
    for item in threads:
        threads_by_id.setdefault(item.session_id, []).append(item)

    sessions: list[CodexSessionSummary] = []
    for root_id, group_ids in groups.items():
        group_id_set = set(group_ids)
        group_files = tuple(
            sorted(
                (
                    item
                    for session_id in group_ids
                    for item in files_by_id.get(session_id, ())
                ),
                key=lambda item: str(item.path),
            )
        )
        group_threads = tuple(
            sorted(
                (
                    item
                    for session_id in group_ids
                    for item in threads_by_id.get(session_id, ())
                ),
                key=lambda item: (
                    str(item.codex_home),
                    str(item.state_db),
                    item.session_id,
                ),
            )
        )
        group_entries = (*group_files, *group_threads)
        if not group_entries:
            continue
        root_files = files_by_id.get(root_id, ())
        root_threads = threads_by_id.get(root_id, ())
        metadata_entry = (
            max(root_files, key=lambda item: item.updated_ns)
            if root_files
            else (
                max(root_threads, key=lambda item: item.updated_ns)
                if root_threads
                else max(group_entries, key=lambda item: item.updated_ns)
            )
        )
        preview = previews.get(root_id, "")
        if not preview:
            preview = next(
                (
                    previews[session_id]
                    for session_id in group_ids
                    if session_id in previews
                ),
                "",
            )
        if not preview and root_threads:
            preview = max(
                root_threads,
                key=lambda item: item.updated_ns,
            ).preview
        if not preview and group_threads:
            preview = max(
                group_threads,
                key=lambda item: item.updated_ns,
            ).preview
        owners: list[CodexCatalogOwner] = []
        for owner_home in homes:
            local_ids = {
                item.session_id
                for item in group_entries
                if item.codex_home == owner_home
            }
            if not local_ids:
                continue
            owners.append(
                CodexCatalogOwner(
                    codex_home=owner_home,
                    delete_ids=tuple(
                        sorted(
                            local_ids,
                            key=lambda session_id: (
                                -_depth(
                                    session_id,
                                    parents=parents,
                                    group_ids=group_id_set,
                                ),
                                session_id,
                            ),
                        )
                    ),
                )
            )
        sessions.append(
            CodexSessionSummary(
                root_id=root_id,
                cwd=metadata_entry.cwd,
                preview=preview,
                updated_ns=max(item.updated_ns for item in group_entries),
                archived=all(item.archived for item in group_entries),
                files=group_files,
                owners=tuple(owners),
            )
        )
    sessions.sort(key=lambda session: (-session.updated_ns, session.root_id))
    return CodexSessionCatalog(
        homes=homes,
        sessions=tuple(sessions),
        app_log_root=user_home / "Library/Logs/com.openai.codex",
        warnings=tuple(warnings),
    )


def _unsafe_broad_codex_home(
    path: Path,
    *,
    user_home: Path,
    warnings: list[str],
) -> bool:
    anchor = Path(path.anchor)
    if path == anchor or path == user_home or len(path.parts) < 3:
        warnings.append(f"refusing unsafe broad Codex home: {path}")
        return True
    return False


def _reset_root_validation_error(path: Path, *, label: str) -> str | None:
    """Reject roots whose existing path components could redirect deletion."""
    if not path.is_absolute():
        return f"unsafe relative {label}: {path}"

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"cannot inspect {label} component {current}: {exc}"
        if stat.S_ISLNK(current_stat.st_mode):
            return f"unsafe {label} symlink component: {current}"
        if current != path and not stat.S_ISDIR(current_stat.st_mode):
            return f"unsafe non-directory {label} component: {current}"
        if current == path and not stat.S_ISDIR(current_stat.st_mode):
            return f"unsafe non-directory {label}: {path}"
    return None


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _process_working_directory(pid: int) -> Path:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("lsof could not inspect the process cwd")
    for line in result.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            return Path(line[1:]).absolute()
    raise RuntimeError("lsof did not report the process cwd")


def _process_arguments_payload(pid: int) -> bytes:
    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)

    for _ in range(2):
        payload_size = ctypes.c_size_t()
        if sysctl(mib, 3, None, ctypes.byref(payload_size), None, 0) != 0:
            error_number = ctypes.get_errno()
            raise OSError(
                error_number,
                f"cannot size process {pid} arguments: "
                f"{os.strerror(error_number)}",
            )
        payload = ctypes.create_string_buffer(payload_size.value)
        if (
            sysctl(
                mib,
                3,
                payload,
                ctypes.byref(payload_size),
                None,
                0,
            )
            == 0
        ):
            return payload.raw[: payload_size.value]
        error_number = ctypes.get_errno()
        if error_number != errno.ENOMEM:
            raise OSError(
                error_number,
                f"cannot read process {pid} arguments: "
                f"{os.strerror(error_number)}",
            )
    raise RuntimeError(f"process {pid} arguments changed while reading")


def _parse_codex_process_context(
    payload: bytes,
) -> tuple[tuple[str, ...], dict[str, str]]:
    if len(payload) < struct.calcsize("=i"):
        raise RuntimeError("sysctl returned an invalid process environment")

    argument_count = struct.unpack_from("=i", payload)[0]
    offset = struct.calcsize("=i")

    def read_entry(position: int) -> tuple[bytes, int]:
        end = payload.find(b"\0", position)
        if end < 0:
            return payload[position:], len(payload)
        return payload[position:end], end + 1

    _, offset = read_entry(offset)
    while offset < len(payload) and payload[offset] == 0:
        offset += 1
    arguments: list[str] = []
    for _ in range(max(0, argument_count)):
        raw_argument, offset = read_entry(offset)
        arguments.append(os.fsdecode(raw_argument))
    while offset < len(payload) and payload[offset] == 0:
        offset += 1

    relevant = {"CODEX_HOME", "CODEX_SQLITE_HOME"}
    environment: dict[str, str] = {}
    while offset < len(payload):
        raw_entry, offset = read_entry(offset)
        if not raw_entry:
            continue
        entry = os.fsdecode(raw_entry)
        key, separator, value = entry.partition("=")
        if separator and key in relevant:
            environment[key] = value
    return tuple(arguments), environment


def _process_codex_context(
    pid: int,
) -> tuple[tuple[str, ...], dict[str, str]]:
    return _parse_codex_process_context(_process_arguments_payload(pid))


def _is_codex_desktop_main_process(process: ClientProcess) -> bool:
    identity = f"{process.executable} {process.command}".lower()
    return (
        "/codex.app/contents/macos/codex" in identity
        and " app-server" not in process.command.lower()
    )


def _describe_codex_processes(
    processes: tuple[ClientProcess, ...],
    errors: list[str],
) -> _RuntimeSnapshot:
    pinned: list[_PinnedRuntime] = []
    invocations: list[_RuntimeInvocation] = []
    desktop_was_running = False
    for process in processes:
        desktop_was_running = (
            desktop_was_running
            or _is_codex_desktop_main_process(process)
        )
        identity = process_identity(process.pid)
        if identity is None:
            errors.append(
                f"cannot pin Codex process identity for PID {process.pid}"
            )
        else:
            pinned.append(
                _PinnedRuntime(
                    process=process,
                    identity=identity,
                )
            )
        try:
            working_directory = _process_working_directory(process.pid)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(
                f"cannot inspect Codex process {process.pid} cwd: {exc}"
            )
            continue
        try:
            arguments, process_environment = _process_codex_context(
                process.pid
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(
                f"cannot inspect Codex process {process.pid} arguments and "
                "environment: "
                f"{exc}"
            )
            continue
        invocations.append(
            _RuntimeInvocation(
                working_directory=working_directory,
                arguments=arguments,
                environment=tuple(process_environment.items()),
            )
        )
    return _RuntimeSnapshot(
        pinned=tuple(pinned),
        invocations=tuple(invocations),
        desktop_was_running=desktop_was_running,
    )


def _snapshot_codex_runtimes(
    errors: list[str],
) -> _RuntimeSnapshot:
    try:
        processes = tuple(
            running_client_processes(
                "codex",
                current_pid=os.getpid(),
            )
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        errors.append(f"cannot inspect running Codex processes: {exc}")
        return _RuntimeSnapshot((), ())
    return _describe_codex_processes(processes, errors)


def _terminate_codex_runtimes(
    errors: list[str],
    *,
    initial_snapshot: _RuntimeSnapshot | None = None,
) -> _RuntimeTermination:
    terminated = 0
    invocations: list[_RuntimeInvocation] = []
    desktop_was_running = False
    pending_snapshot = initial_snapshot
    for _ in range(4):
        snapshot = (
            pending_snapshot
            if pending_snapshot is not None
            else _snapshot_codex_runtimes(errors)
        )
        pending_snapshot = None
        invocations.extend(snapshot.invocations)
        desktop_was_running = (
            desktop_was_running or snapshot.desktop_was_running
        )
        if not snapshot.pinned:
            return _RuntimeTermination(
                terminated=terminated,
                invocations=tuple(invocations),
                desktop_was_running=desktop_was_running,
            )

        try:
            current_processes = tuple(
                running_client_processes(
                    "codex",
                    current_pid=os.getpid(),
                )
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"cannot revalidate Codex processes: {exc}")
            return _RuntimeTermination(
                terminated=terminated,
                invocations=tuple(invocations),
                desktop_was_running=desktop_was_running,
            )
        current_by_pid = {
            process.pid: process for process in current_processes
        }
        for pinned_runtime in snapshot.pinned:
            process = pinned_runtime.process
            if process.pid not in current_by_pid:
                continue
            current_identity = process_identity(process.pid)
            if current_identity != pinned_runtime.identity:
                errors.append(
                    f"Codex process {process.pid} identity changed before "
                    "termination"
                )
                continue
            try:
                terminate_pid(
                    process.pid,
                    expected_identity=pinned_runtime.identity,
                )
            except OSError as exc:
                errors.append(
                    f"cannot terminate Codex process {process.pid}: {exc}"
                )
                continue
            if pid_is_alive(process.pid):
                errors.append(
                    f"Codex process {process.pid} is still running after "
                    "termination"
                )
                continue
            terminated += 1

    errors.append(
        "Codex processes kept respawning during reset quiescence check"
    )
    return _RuntimeTermination(
        terminated=terminated,
        invocations=tuple(invocations),
        desktop_was_running=desktop_was_running,
    )


def _reopen_codex_desktop() -> str | None:
    result = subprocess.run(
        ["/usr/bin/open", "-a", "Codex"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"exit {result.returncode}"
        return f"could not reopen Codex Desktop: {detail}"

    deadline = time.monotonic() + 5.0
    while True:
        try:
            processes = running_client_processes(
                "codex",
                current_pid=os.getpid(),
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return f"cannot verify reopened Codex Desktop: {exc}"
        if any(_is_codex_desktop_main_process(process) for process in processes):
            return None
        if time.monotonic() >= deadline:
            return "Codex Desktop did not reappear after restart"
        time.sleep(0.2)


def _configured_state_locations(
    *,
    homes: tuple[Path, ...],
    user_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    cli_arguments: tuple[str, ...],
    runtime_invocations: tuple[_RuntimeInvocation, ...],
    system_config_path: Path,
    errors: list[str],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    sqlite_roots: list[Path] = []
    log_roots: list[Path] = []

    def configured_path(
        value: object,
        *,
        source: Path,
        kind: str,
        base_directory: Path,
    ) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = base_directory / candidate
        candidate = candidate.absolute()
        if (
            candidate == Path(candidate.anchor)
            or candidate == user_home
            or len(candidate.parts) < 3
        ):
            errors.append(
                f"refusing unsafe broad Codex state location from "
                f"{source}: {candidate}"
            )
            return None
        validation_error = _reset_root_validation_error(
            candidate,
            label="configured Codex state root",
        )
        if validation_error is not None:
            errors.append(
                f"{validation_error} from {source}"
            )
            return None
        if kind == "log" and any(
            candidate == codex_home or _is_below(codex_home, candidate)
            for codex_home in homes
        ):
            errors.append(
                f"configured Codex log root overlaps protected Codex home "
                f"from {source}: {candidate}"
            )
            return None
        if kind == "sqlite" and any(
            _is_below(codex_home, candidate)
            for codex_home in homes
        ):
            errors.append(
                f"configured Codex SQLite root contains a Codex home "
                f"from {source}: {candidate}"
            )
            return None
        for codex_home in homes:
            for directory_name in _PRESERVED_CODEX_DIRECTORY_NAMES:
                preserved_path = codex_home / directory_name
                if (
                    candidate == preserved_path
                    or _is_below(candidate, preserved_path)
                    or _is_below(preserved_path, candidate)
                ):
                    errors.append(
                        "configured Codex state root overlaps preserved "
                        f"Codex path from {source}: {candidate}"
                    )
                    return None
        return candidate

    def read_config(config_path: Path) -> dict[str, object] | None:
        parent_error = _reset_root_validation_error(
            config_path.parent,
            label="Codex config parent",
        )
        if parent_error is not None:
            errors.append(parent_error)
            return None
        try:
            config_stat = config_path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            errors.append(
                f"cannot inspect Codex config {config_path}: {exc}"
            )
            return None
        if stat.S_ISLNK(config_stat.st_mode) or not stat.S_ISREG(
            config_stat.st_mode
        ):
            errors.append(f"unsafe Codex config path: {config_path}")
            return None
        try:
            with config_path.open("rb") as config_file:
                return tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"cannot read Codex config {config_path}: {exc}")
            return None

    def collect_locations(
        config_path: Path,
        config: Mapping[str, object],
        *,
        base_directory: Path,
    ) -> None:
        sqlite_root = configured_path(
            config.get("sqlite_home"),
            source=config_path,
            kind="sqlite",
            base_directory=base_directory,
        )
        if sqlite_root is not None:
            sqlite_roots.append(sqlite_root)
        log_root = configured_path(
            config.get("log_dir"),
            source=config_path,
            kind="log",
            base_directory=base_directory,
        )
        if log_root is not None:
            log_roots.append(log_root)

    def effective_working_directory(
        invocation: _RuntimeInvocation,
    ) -> Path:
        effective = invocation.working_directory
        index = 0
        while index < len(invocation.arguments):
            argument = invocation.arguments[index]
            if argument == "--":
                break
            value: str | None = None
            if argument in {"-C", "--cd"}:
                index += 1
                if index >= len(invocation.arguments):
                    errors.append(
                        f"Codex invocation has no value for {argument}"
                    )
                    break
                value = invocation.arguments[index]
            elif argument.startswith("--cd="):
                value = argument.removeprefix("--cd=")
            elif argument.startswith("-C="):
                value = argument.removeprefix("-C=")
            if value is not None:
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = effective / candidate
                effective = candidate.absolute()
            index += 1
        return effective

    def cli_overrides(
        arguments: tuple[str, ...],
    ) -> tuple[tuple[str, object], ...]:
        overrides: list[tuple[str, object]] = []
        index = 0
        while index < len(arguments):
            argument = arguments[index]
            if argument == "--":
                break
            raw_override: str | None = None
            if argument in {"-c", "--config"}:
                index += 1
                if index >= len(arguments):
                    errors.append(
                        f"Codex invocation has no value for {argument}"
                    )
                    break
                raw_override = arguments[index]
            elif argument.startswith("--config="):
                raw_override = argument.removeprefix("--config=")
            elif argument.startswith("-c="):
                raw_override = argument.removeprefix("-c=")
            if raw_override is not None:
                key, separator, raw_value = raw_override.partition("=")
                if separator and key in {"sqlite_home", "log_dir"}:
                    try:
                        parsed = tomllib.loads(
                            f"value = {raw_value}"
                        )["value"]
                    except tomllib.TOMLDecodeError:
                        parsed = raw_value
                    overrides.append((key, parsed))
            index += 1
        return tuple(overrides)

    def collect_invocation(invocation: _RuntimeInvocation) -> Path:
        effective_directory = effective_working_directory(invocation)
        directory_error = _reset_root_validation_error(
            effective_directory,
            label="Codex working directory",
        )
        if directory_error is not None:
            errors.append(directory_error)
            return effective_directory

        loaded_configs: list[tuple[Path, dict[str, object]]] = []
        config_paths: list[Path] = [system_config_path]
        for codex_home in homes:
            config_paths.extend(
                (
                    codex_home / "config.toml",
                    *sorted(codex_home.glob("*.config.toml")),
                )
            )
        for config_path in config_paths:
            config = read_config(config_path)
            if config is None:
                continue
            loaded_configs.append((config_path, config))
            collect_locations(
                config_path,
                config,
                base_directory=effective_directory,
            )

        project_root = find_project_root(effective_directory)
        trust_candidates: list[tuple[int, str]] = []
        for _, config in loaded_configs:
            projects = config.get("projects")
            if not isinstance(projects, Mapping):
                continue
            for project_value, project_config in projects.items():
                if not isinstance(project_value, str) or not isinstance(
                    project_config,
                    Mapping,
                ):
                    continue
                project_path = Path(project_value).expanduser()
                if not project_path.is_absolute():
                    continue
                project_path = project_path.absolute()
                if project_path != project_root and not _is_below(
                    project_root,
                    project_path,
                ):
                    continue
                trust_level = project_config.get("trust_level")
                if isinstance(trust_level, str):
                    trust_candidates.append(
                        (len(project_path.parts), trust_level)
                    )

        if (
            trust_candidates
            and max(
                trust_candidates,
                key=lambda item: item[0],
            )[1] == "trusted"
        ):
            try:
                relative_directory = effective_directory.relative_to(
                    project_root
                )
            except ValueError:
                errors.append(
                    "Codex project root does not contain working "
                    f"directory: {project_root}"
                )
            else:
                config_directories = [project_root]
                current_directory = project_root
                for part in relative_directory.parts:
                    current_directory /= part
                    config_directories.append(current_directory)
                for config_directory in config_directories:
                    config_path = config_directory / ".codex/config.toml"
                    config = read_config(config_path)
                    if config is not None:
                        collect_locations(
                            config_path,
                            config,
                            base_directory=effective_directory,
                        )

        cli_source = effective_directory / ".codex-cli-override"
        for key, value in cli_overrides(invocation.arguments):
            configured = configured_path(
                value,
                source=cli_source,
                kind="sqlite" if key == "sqlite_home" else "log",
                base_directory=effective_directory,
            )
            if configured is None:
                continue
            if key == "sqlite_home":
                sqlite_roots.append(configured)
            else:
                log_roots.append(configured)

        invocation_environment = dict(invocation.environment)
        environment_sqlite_home = invocation_environment.get(
            "CODEX_SQLITE_HOME",
            "",
        ).strip()
        if environment_sqlite_home:
            environment_source = (
                effective_directory / ".codex-process-environment"
            )
            sqlite_root = configured_path(
                environment_sqlite_home,
                source=environment_source,
                kind="sqlite",
                base_directory=effective_directory,
            )
            if sqlite_root is not None:
                sqlite_roots.append(sqlite_root)
        return effective_directory

    collect_invocation(
        _RuntimeInvocation(
            working_directory=working_directory,
            arguments=cli_arguments,
            environment=tuple(
                (key, environment[key])
                for key in ("CODEX_HOME", "CODEX_SQLITE_HOME")
                if environment.get(key)
            ),
        )
    )
    for invocation in runtime_invocations:
        collect_invocation(invocation)

    return (
        tuple(dict.fromkeys(sqlite_roots)),
        tuple(dict.fromkeys(log_roots)),
    )


def _remove_full_reset_target(target: _ResetTarget) -> str | None:
    path = target.path
    if not _is_below(path, target.allowed_root):
        return f"path escapes reset root: {path}"
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot inspect {path}: {exc}"
    try:
        if stat.S_ISLNK(path_stat.st_mode):
            path.unlink()
        elif target.kind == "file" and stat.S_ISREG(path_stat.st_mode):
            path.unlink()
        elif target.kind == "directory" and stat.S_ISDIR(path_stat.st_mode):
            shutil.rmtree(path)
        else:
            expected = (
                "regular file"
                if target.kind == "file"
                else "directory"
            )
            return f"expected {expected} at Codex reset path: {path}"
    except OSError as exc:
        return f"cannot delete {path}: {exc}"
    return None


def _global_state_has_traces(state: Mapping[str, object]) -> bool:
    if any(key in state for key in _GLOBAL_STATE_TOP_LEVEL_TRACE_KEYS):
        return True
    atom_state = state.get("electron-persisted-atom-state")
    if not isinstance(atom_state, Mapping):
        return False
    return any(
        key in _GLOBAL_STATE_ATOM_TRACE_KEYS
        or any(
            key.startswith(prefix)
            for prefix in _GLOBAL_STATE_ATOM_TRACE_PREFIXES
        )
        for key in atom_state
    )


def _read_global_state(path: Path) -> tuple[dict[str, object], os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise RuntimeError(
                f"expected regular file at Codex global state path: {path}"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as state_file:
            descriptor = -1
            state = json.load(state_file)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(state, dict):
        raise RuntimeError(f"Codex global state is not an object: {path}")
    return state, opened_stat


def _write_global_state_atomically(
    path: Path,
    state: Mapping[str, object],
    *,
    mode: int,
) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, stat.S_IMODE(mode))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
            descriptor = -1
            json.dump(state, state_file, separators=(",", ":"))
            state_file.write("\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _clear_global_state_traces(
    path: Path,
    *,
    allowed_root: Path,
) -> tuple[bool, str | None]:
    if not _is_below(path, allowed_root):
        return False, f"path escapes reset root: {path}"
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        return False, f"cannot inspect {path}: {exc}"
    if stat.S_ISLNK(path_stat.st_mode):
        try:
            path.unlink()
        except OSError as exc:
            return False, f"cannot delete {path}: {exc}"
        return True, None
    if not stat.S_ISREG(path_stat.st_mode):
        return (
            False,
            f"expected regular file at Codex global state path: {path}",
        )
    try:
        state, opened_stat = _read_global_state(path)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        return False, f"cannot read Codex global state {path}: {exc}"
    if not _global_state_has_traces(state):
        return False, None

    for key in _GLOBAL_STATE_TOP_LEVEL_TRACE_KEYS:
        state.pop(key, None)
    atom_state = state.get("electron-persisted-atom-state")
    if isinstance(atom_state, dict):
        for key in tuple(atom_state):
            if key in _GLOBAL_STATE_ATOM_TRACE_KEYS or any(
                key.startswith(prefix)
                for prefix in _GLOBAL_STATE_ATOM_TRACE_PREFIXES
            ):
                atom_state.pop(key, None)
    try:
        _write_global_state_atomically(
            path,
            state,
            mode=opened_stat.st_mode,
        )
    except OSError as exc:
        return False, f"cannot write Codex global state {path}: {exc}"
    return True, None


def _global_state_verification_error(path: Path) -> str | None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot inspect Codex global state {path}: {exc}"
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return f"unsafe Codex global state path remains: {path}"
    try:
        state, _ = _read_global_state(path)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        return f"cannot verify Codex global state {path}: {exc}"
    if _global_state_has_traces(state):
        return f"Codex global state still contains conversation traces: {path}"
    return None


def _matching_reset_databases(
    root: Path,
    *,
    errors: list[str],
) -> tuple[Path, ...]:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        errors.append(f"cannot inspect Codex SQLite root {root}: {exc}")
        return ()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        errors.append(f"unsafe Codex SQLite root: {root}")
        return ()
    try:
        entries = tuple(root.iterdir())
    except OSError as exc:
        errors.append(f"cannot list Codex SQLite root {root}: {exc}")
        return ()
    return tuple(
        sorted(
            (
                entry
                for entry in entries
                if _RESET_DB_RE.fullmatch(entry.name)
            ),
            key=lambda entry: (
                entry.name.endswith(".sqlite"),
                entry.name,
            ),
        )
    )


def _matching_configured_log_files(
    root: Path,
    *,
    errors: list[str],
) -> tuple[Path, ...]:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        errors.append(f"cannot inspect configured Codex log root {root}: {exc}")
        return ()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        errors.append(f"unsafe configured Codex log root: {root}")
        return ()

    try:
        entries = tuple(root.iterdir())
    except OSError as exc:
        errors.append(
            f"cannot enumerate configured Codex log root {root}: {exc}"
        )
        return ()
    return tuple(
        entry
        for entry in sorted(entries, key=lambda path: path.name)
        if _CONFIGURED_LOG_FILE_RE.fullmatch(entry.name)
    )


def _quoted_sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _desktop_reset_table_names(
    connection: sqlite3.Connection,
) -> tuple[str, ...]:
    return tuple(
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        if row[0] not in _DESKTOP_PRESERVED_TABLES
    )


def _desktop_session_row_count(path: Path) -> int:
    database_uri = path.resolve(strict=True).as_uri() + "?mode=rw"
    with sqlite3.connect(
        database_uri,
        uri=True,
        timeout=5,
    ) as connection:
        return sum(
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM "
                    + _quoted_sqlite_identifier(table_name)
                ).fetchone()[0]
            )
            for table_name in _desktop_reset_table_names(connection)
        )


def _desktop_sqlite_sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(
        Path(str(path) + suffix)
        for suffix in ("-wal", "-shm", "-journal")
        if _path_exists(Path(str(path) + suffix))
    )


def _clear_desktop_session_state(path: Path, *, allowed_root: Path) -> int:
    if not _is_below(path, allowed_root):
        raise RuntimeError(f"desktop state path escapes Codex home: {path}")
    path_stat = path.lstat()
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"unsafe Codex desktop state path: {path}")

    rows_before = _desktop_session_row_count(path)
    database_uri = path.resolve(strict=True).as_uri() + "?mode=rw"
    with sqlite3.connect(
        database_uri,
        uri=True,
        timeout=5,
    ) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA secure_delete = ON")
        reset_tables = _desktop_reset_table_names(connection)
        connection.execute("BEGIN IMMEDIATE")
        for table_name in reset_tables:
            connection.execute(
                "DELETE FROM " + _quoted_sqlite_identifier(table_name)
            )
        connection.commit()
        checkpoint = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
        if checkpoint is not None and int(checkpoint[0]) != 0:
            raise RuntimeError(
                f"Codex desktop state WAL checkpoint is busy: {path}"
            )
        connection.execute("VACUUM")
        checkpoint = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
        if checkpoint is not None and int(checkpoint[0]) != 0:
            raise RuntimeError(
                f"Codex desktop state WAL checkpoint is busy: {path}"
            )

    residual_sidecars = _desktop_sqlite_sidecars(path)
    if residual_sidecars:
        raise RuntimeError(
            "Codex desktop state still has SQLite sidecars: "
            + ", ".join(str(sidecar) for sidecar in residual_sidecars)
        )
    if _desktop_session_row_count(path) != 0:
        raise RuntimeError(
            f"Codex desktop state still contains session metadata: {path}"
        )
    return rows_before


def _full_reset_targets(
    *,
    homes: tuple[Path, ...],
    sqlite_roots: tuple[Path, ...],
    log_roots: tuple[Path, ...],
    app_log_root: Path,
    errors: list[str],
) -> tuple[_ResetTarget, ...]:
    targets: list[_ResetTarget] = []
    for codex_home in homes:
        targets.extend(
            _ResetTarget(
                path=codex_home / name,
                allowed_root=codex_home,
                kind="directory",
            )
            for name in _FULL_RESET_DIRECTORY_NAMES
        )
        targets.extend(
            _ResetTarget(
                path=codex_home / name,
                allowed_root=codex_home,
                kind="file",
            )
            for name in _FULL_RESET_FILE_NAMES
        )
        targets.extend(
            _ResetTarget(
                path=path,
                allowed_root=codex_home,
                kind="file",
            )
            for path in _matching_reset_databases(
                codex_home,
                errors=errors,
            )
        )
    for sqlite_root in sqlite_roots:
        targets.extend(
            _ResetTarget(
                path=path,
                allowed_root=sqlite_root,
                kind="file",
            )
            for path in _matching_reset_databases(
                sqlite_root,
                errors=errors,
            )
        )
    for log_root in log_roots:
        targets.extend(
            _ResetTarget(
                path=path,
                allowed_root=log_root,
                kind="file",
            )
            for path in _matching_configured_log_files(
                log_root,
                errors=errors,
            )
        )
    app_log_parent_error = _reset_root_validation_error(
        app_log_root.parent,
        label="Codex desktop log parent",
    )
    if app_log_parent_error is None:
        targets.append(
            _ResetTarget(
                path=app_log_root,
                allowed_root=app_log_root.parent,
                kind="directory",
            )
        )
    else:
        errors.append(app_log_parent_error)

    unique: dict[Path, _ResetTarget] = {}
    for target in targets:
        existing = unique.setdefault(target.path, target)
        if existing.kind != target.kind:
            errors.append(
                f"conflicting Codex reset target types: {target.path}"
            )
    return tuple(unique.values())


def reset_all_codex_data(
    *,
    home: Path,
    codex_home: Path,
    orca_codex_home: Path | None = None,
    working_directory: Path | None = None,
    environment: Mapping[str, str] | None = None,
    cli_arguments: tuple[str, ...] = (),
    system_config_path: Path = _DEFAULT_SYSTEM_CONFIG_PATH,
) -> CodexResetResult:
    """Delete every known local Codex session, memory, and conversation trace."""
    user_home = home.absolute()
    warnings: list[str] = []
    errors: list[str] = []
    runtime_snapshot = _snapshot_codex_runtimes(errors)
    runtime_termination = _terminate_codex_runtimes(
        errors,
        initial_snapshot=runtime_snapshot,
    )
    runtime_invocations = runtime_termination.invocations
    terminated_processes = runtime_termination.terminated
    desktop_was_running = runtime_termination.desktop_was_running

    base_homes, _, _ = lexical_codex_homes(
        home=home,
        codex_home=codex_home,
        orca_codex_home=orca_codex_home,
    )
    discovered_homes = list(base_homes)
    for invocation in runtime_invocations:
        runtime_codex_home = dict(invocation.environment).get(
            "CODEX_HOME",
            "",
        ).strip()
        if not runtime_codex_home:
            continue
        candidate = Path(runtime_codex_home).expanduser()
        if not candidate.is_absolute():
            candidate = invocation.working_directory / candidate
        discovered_homes.append(candidate.absolute())

    safe_homes: list[Path] = []
    for candidate in discovered_homes:
        if _unsafe_broad_codex_home(
            candidate,
            user_home=user_home,
            warnings=errors,
        ):
            continue
        validation_error = _reset_root_validation_error(
            candidate,
            label="Codex home",
        )
        if validation_error is not None:
            errors.append(validation_error)
            continue
        safe_homes.append(candidate)
    homes = tuple(dict.fromkeys(safe_homes))

    discovered_sessions = 0
    try:
        before_catalog = scan_codex_session_catalog(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
            _validated_homes=homes,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        warnings.append(f"cannot count Codex sessions before reset: {exc}")
    else:
        discovered_sessions = len(before_catalog.sessions)
        warnings.extend(before_catalog.warnings)

    sqlite_roots, log_roots = _configured_state_locations(
        homes=homes,
        user_home=user_home,
        working_directory=(
            working_directory or Path.cwd()
        ).resolve(strict=False),
        environment=environment if environment is not None else os.environ,
        cli_arguments=cli_arguments,
        runtime_invocations=runtime_invocations,
        system_config_path=system_config_path,
        errors=errors,
    )
    app_log_root = user_home / "Library/Logs/com.openai.codex"

    deleted_trace_targets = 0
    desktop_databases = tuple(
        codex_home_path / "sqlite/codex-dev.db"
        for codex_home_path in homes
    )
    for desktop_database in desktop_databases:
        try:
            desktop_database.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(
                f"cannot inspect Codex desktop state "
                f"{desktop_database}: {exc}"
            )
            continue
        try:
            deleted_rows = _clear_desktop_session_state(
                desktop_database,
                allowed_root=next(
                    home_path
                    for home_path in homes
                    if _is_below(desktop_database, home_path)
                ),
            )
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            errors.append(str(exc))
            continue
        if deleted_rows:
            deleted_trace_targets += 1

    for codex_home_path in homes:
        for file_name in _GLOBAL_STATE_FILE_NAMES:
            changed, global_state_error = _clear_global_state_traces(
                codex_home_path / file_name,
                allowed_root=codex_home_path,
            )
            if global_state_error is not None:
                errors.append(global_state_error)
            elif changed:
                deleted_trace_targets += 1

    targets = _full_reset_targets(
        homes=homes,
        sqlite_roots=sqlite_roots,
        log_roots=log_roots,
        app_log_root=app_log_root,
        errors=errors,
    )
    for target in targets:
        existed = _path_exists(target.path)
        error = _remove_full_reset_target(target)
        if error is not None:
            errors.append(error)
        elif existed:
            deleted_trace_targets += 1

    post_mutation = _terminate_codex_runtimes(errors)
    terminated_processes += post_mutation.terminated
    desktop_was_running = (
        desktop_was_running or post_mutation.desktop_was_running
    )
    if post_mutation.invocations or post_mutation.terminated:
        errors.append(
            f"{post_mutation.terminated} Codex runtime(s) appeared during "
            "Codex reset"
        )

    verification_targets = _full_reset_targets(
        homes=homes,
        sqlite_roots=sqlite_roots,
        log_roots=log_roots,
        app_log_root=app_log_root,
        errors=errors,
    )
    residual_paths = [
        target.path
        for target in verification_targets
        if _path_exists(target.path)
    ]
    if residual_paths:
        errors.append(
            "Codex full reset left residual paths: "
            + ", ".join(str(path) for path in residual_paths[:5])
            + (
                f", +{len(residual_paths) - 5} more"
                if len(residual_paths) > 5
                else ""
            )
        )

    for codex_home_path in homes:
        for file_name in _GLOBAL_STATE_FILE_NAMES:
            global_state_error = _global_state_verification_error(
                codex_home_path / file_name
            )
            if global_state_error is not None:
                errors.append(global_state_error)

    for desktop_database in desktop_databases:
        if not _path_exists(desktop_database):
            continue
        residual_sidecars = _desktop_sqlite_sidecars(desktop_database)
        if residual_sidecars:
            errors.append(
                "Codex desktop state still has SQLite sidecars: "
                + ", ".join(
                    str(sidecar) for sidecar in residual_sidecars
                )
            )
        try:
            remaining_rows = _desktop_session_row_count(desktop_database)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            errors.append(
                f"cannot verify Codex desktop state "
                f"{desktop_database}: {exc}"
            )
            continue
        if remaining_rows:
            errors.append(
                f"Codex desktop state still contains "
                f"{remaining_rows} session row(s): {desktop_database}"
            )

    remaining_sessions = 0
    try:
        after_catalog = scan_codex_session_catalog(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
            _validated_homes=homes,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"cannot verify Codex sessions after reset: {exc}")
    else:
        remaining_sessions = len(after_catalog.sessions)
        if remaining_sessions:
            errors.append(
                f"Codex full reset left "
                f"{remaining_sessions} persisted session(s)"
            )

    final_termination = _terminate_codex_runtimes(errors)
    terminated_processes += final_termination.terminated
    desktop_was_running = (
        desktop_was_running or final_termination.desktop_was_running
    )
    if final_termination.invocations or final_termination.terminated:
        errors.append(
            f"{final_termination.terminated} Codex runtime(s) appeared "
            "during final "
            "Codex reset verification"
        )

    desktop_restarted = False
    if desktop_was_running:
        reopen_error = _reopen_codex_desktop()
        if reopen_error is None:
            desktop_restarted = True
        else:
            errors.append(reopen_error)

    error = "; ".join(dict.fromkeys(errors)) or None
    return CodexResetResult(
        discovered_sessions=discovered_sessions,
        deleted_sessions=(
            discovered_sessions if remaining_sessions == 0 and error is None
            else max(0, discovered_sessions - remaining_sessions)
        ),
        deleted_trace_targets=deleted_trace_targets,
        terminated_processes=terminated_processes,
        desktop_restarted=desktop_restarted,
        warnings=tuple(dict.fromkeys(warnings)),
        error=error,
    )
