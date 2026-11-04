"""The ChatGPT app's own files in the Codex home, as the Codex cleanup reads
and edits them.

The app lists the sidebar's threads in a catalog of its own,
`sqlite/codex.db` (`sqlite/codex-dev.db` in its non-production builds), and
keeps per-thread window state in `.codex-global-state.json` and its copy
`.bak`, and its daemon remembers the threads it had loaded. `codex delete`
touches none of them, so a deleted session stays in all.
The app checks its whole catalog again only when the local host's
`last_full_reconciled_at` is empty: its own migrations clear it to make it do
so. None of these is a Codex store with migrations dotsync knows: one of
another shape is left alone, never a refusal."""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

from dotsync.sessions import roots, safety

ACTIVE_CATALOG = "codex.db"
OLD_CATALOG = "codex-dev.db"
STATE_FILES = (".codex-global-state.json", ".codex-global-state.json.bak")
ATOMS = "electron-persisted-atom-state"  # the app's second level of state, keyed like the first
READ_STATE = "electron-thread-read-state-v1"  # unread threads, per identity and host
MIGRATION = "app-server-projects-migration-by-host"  # threads each host has yet to assign to a project
LOADED_THREADS = "app-server-daemon/loaded-threads.json"  # the threads the app's daemon had loaded
INTERRUPTED = "codex-interrupted-v1:"  # a loaded-threads entry: the turns it was interrupted in

_CATALOG_COLUMNS = {
    "local_thread_catalog": {"host_id", "thread_id", "missing_candidate"},
    "local_thread_catalog_hosts": {"host_id", "host_kind"},
    "local_thread_catalog_sync_state": {"host_id", "initial_build_complete", "last_full_reconciled_at"},
}
_LOCAL_HOSTS = "SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind = 'local'"


def catalog_paths() -> list[Path]:
    folder = roots.codex_home() / "sqlite"
    return [folder / ACTIVE_CATALOG, folder / OLD_CATALOG]


def is_thread_id(value: object) -> bool:
    """A canonical UUIDv7, the kind Codex makes thread ids with. The state
    file also holds UUIDs that are no thread (drafts, ChatGPT chats)."""
    return safety.is_valid_id(value) and value[14] == "7"  # type: ignore[index]


# The catalogs


def known_catalog(con: sqlite3.Connection) -> bool:
    """Whether the catalog has the tables and columns the cleanup uses."""
    for table, columns in _CATALOG_COLUMNS.items():
        have = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if not columns <= have:
            return False
    return True


def listed_threads(con: sqlite3.Connection, *, active: bool) -> set[str]:
    """The local threads the catalog still holds. In the old catalog, every
    row. In the active one, the rows the app marked missing (it counts them
    gone; `marked_threads`), and those it still shows unless a full check is
    due: the app then drops what is gone by itself."""
    if not active:
        rows = con.execute(f"SELECT thread_id FROM local_thread_catalog WHERE host_id IN ({_LOCAL_HOSTS})")
        return {thread_id for (thread_id,) in rows}
    listed = marked_threads(con)
    if not _check_due(con):
        rows = con.execute(
            f"SELECT thread_id FROM local_thread_catalog WHERE missing_candidate = 0 AND host_id IN ({_LOCAL_HOSTS})"
        )
        listed |= {thread_id for (thread_id,) in rows}
    return listed


def marked_threads(con: sqlite3.Connection) -> set[str]:
    """The local rows the app marked missing: hidden, and dropped at its next
    full check, which may come after the cleanup's vacuum and so leave their
    bytes in free pages."""
    rows = con.execute(
        f"SELECT thread_id FROM local_thread_catalog WHERE missing_candidate != 0 AND host_id IN ({_LOCAL_HOSTS})"
    )
    return {thread_id for (thread_id,) in rows}


def other_hosts_threads(con: sqlite3.Connection) -> set[str]:
    """The threads of every host but this machine (ChatGPT conversations,
    remote machines): never a deleted local session."""
    rows = con.execute(
        "SELECT thread_id FROM local_thread_catalog WHERE host_id IN"
        " (SELECT host_id FROM local_thread_catalog_hosts WHERE host_kind != 'local')"
    )
    return {thread_id for (thread_id,) in rows}


def has_remote_hosts(con: sqlite3.Connection) -> bool:
    """Whether the app knows a host that runs Codex threads elsewhere (SSH,
    WSL, remote control): their threads are UUIDv7 too."""
    return con.execute(
        "SELECT 1 FROM local_thread_catalog_hosts WHERE host_kind NOT IN ('local', 'chatgpt')"
    ).fetchone() is not None


def _check_due(con: sqlite3.Connection) -> bool:
    """The app's own rule (`isFullReconciliationDue`): no complete build yet,
    or no full check since the last request for one."""
    rows = con.execute(
        "SELECT initial_build_complete, last_full_reconciled_at FROM local_thread_catalog_sync_state"
        f" WHERE host_id IN ({_LOCAL_HOSTS})"
    ).fetchall()
    return not rows or any(not built or checked is None for built, checked in rows)


def request_full_check(con: sqlite3.Connection) -> None:
    con.execute(
        "UPDATE local_thread_catalog_sync_state SET last_full_reconciled_at = NULL"
        f" WHERE host_id IN ({_LOCAL_HOSTS})"
    )


def delete_threads(con: sqlite3.Connection, ids: set[str]) -> None:
    con.executemany(
        f"DELETE FROM local_thread_catalog WHERE thread_id = ? AND host_id IN ({_LOCAL_HOSTS})",
        [(i,) for i in sorted(ids)],
    )


# The state file


def parse_state(raw: bytes) -> dict | None:
    """The state, or None when it is not the app's shape: a JSON object whose
    atoms, when there are any, are an object too."""
    data = _json(raw)
    if not isinstance(data, dict) or not isinstance(data.get(ATOMS, {}), dict) or not _writable(data):
        return None
    return data


def dump_state(data: dict | list) -> bytes:
    """The app's own layout, for the state and the daemon's list alike:
    compact, and UTF-8 as it is."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def state_ids(data: dict, wanted: Callable[[str], bool]) -> set[str]:
    """The ids `wanted` accepts that the state keeps an entry for (see
    `_entries`)."""
    found: set[str] = set()
    for level in (data, data.get(ATOMS, {})):
        for key, value in level.items():
            if key != ATOMS:
                found |= {i for i in _entries(key, value) if wanted(i)}
    return found


def remote_ids(data: dict) -> set[str]:
    """The ids the state names with another host (a key's host part, a host's
    unread or pending list, an activity key): that host's threads, every
    entry of theirs in any state file."""
    remote: set[str] = set()
    for level in (data, data.get(ATOMS, {})):
        for key, value in level.items():
            if key == ATOMS:
                continue
            parts = unquote(key).split(":")
            if len(parts) > 2 and parts[1] != "local":
                remote.add(parts[-1])
            remote |= {i for owner, i in _host_lists_ids(key, value) if not _is_local(owner)}
            if isinstance(value, dict):
                remote |= {k[1] for k in map(_host_key, value) if k and not _is_local(k[0])}
    return remote


def without(data: dict, ids: set[str]) -> dict:
    """The state with no entry of `ids`; all else as it was, in its order."""
    kept = _without(data, ids)
    if ATOMS in data:
        kept[ATOMS] = _without(data[ATOMS], ids)
    return kept


def _without(level: dict, ids: set[str]) -> dict:
    kept: dict = {}
    for key, value in level.items():
        if key == ATOMS:
            kept[key] = value
            continue
        if _key_owner(key) in ids:
            continue
        if key in _HOST_LISTS:
            value = copy.deepcopy(value)
            for hosts, host, field in _HOST_LISTS[key](value):
                if _is_local(host):
                    hosts[field] = [v for v in hosts[field] if not (isinstance(v, str) and v in ids)]
        elif isinstance(value, dict):
            value = {k: v for k, v in value.items() if _dict_key_owner(k) not in ids and not (isinstance(v, str) and v in ids)}
        elif isinstance(value, list):
            value = [v for v in value if not (isinstance(v, str) and v in ids)]
        kept[key] = value
    return kept


def _entries(key: str, value: object) -> set[str]:
    """Whose entries one key of a level holds: a key that ends in a local
    thread id (`thread-tab-routes-v1:<id>`, `thread-client-id-v1:local%3A<id>`)
    is that thread's; a map holds its keys' (`<id>`, `["local","<id>"]`) and
    its string values' threads, a list its string elements', and the read
    state and the project migration their local hosts' lists'. Anything
    deeper, an id only mentioned in a text, or another host's, is no one's."""
    owner = _key_owner(key)
    owners = {owner} if owner else set()
    if key in _HOST_LISTS:
        owners |= {i for host, i in _host_lists_ids(key, value) if _is_local(host)}
    elif isinstance(value, dict):
        owners |= {o for o in map(_dict_key_owner, value) if o} | {v for v in value.values() if isinstance(v, str)}
    elif isinstance(value, list):
        owners |= {v for v in value if isinstance(v, str)}
    return owners


def _key_owner(key: str) -> str | None:
    """The last part of a key; with a host between the name and it, only
    when that host is this machine (`…:local%3A<id>`, not `…:ssh%3A…%3A<id>`)."""
    parts = unquote(key).split(":")
    if len(parts) > 2 and parts[1] != "local":
        return None
    return parts[-1]


def _dict_key_owner(key: str) -> str | None:
    """A map key's thread: the key itself, or the id of a `["<host>","<id>"]`
    key when the host is this machine."""
    pair = _host_key(key)
    if pair is None:
        return key
    return pair[1] if _is_local(pair[0]) else None


def _host_key(key: str) -> tuple[str, str] | None:
    """(host, id) of a `["<host>","<id>"]` key, as the app keys activity times."""
    if not key.startswith("["):
        return None
    pair = _json_text(key)
    if isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair):
        return pair[0], pair[1]
    return None


def _is_local(host: str) -> bool:
    return host == "local" or host.startswith("local:")


def _unread_lists(read_state: object) -> list[tuple[dict, str, str]]:
    """Where the read state keeps unread threads, as (map, host, key):
    `unreadByIdentity.<identity>.<host>` and
    `legacyMigration.unreadThreadIdsByHostId.<host>`."""
    if not isinstance(read_state, dict):
        return []
    maps: list[dict] = []
    by_identity = read_state.get("unreadByIdentity")
    if isinstance(by_identity, dict):
        maps += [hosts for hosts in by_identity.values() if isinstance(hosts, dict)]
    legacy = read_state.get("legacyMigration")
    if isinstance(legacy, dict) and isinstance(legacy.get("unreadThreadIdsByHostId"), dict):
        maps.append(legacy["unreadThreadIdsByHostId"])
    return [(hosts, host, host) for hosts in maps for host, ids in hosts.items() if isinstance(ids, list)]


def _pending_lists(migration: object) -> list[tuple[dict, str, str]]:
    """Where the project migration keeps the threads it has yet to assign, as
    (map, host, key): `<host>.pendingThreadAssignmentIds`."""
    if not isinstance(migration, dict):
        return []
    return [
        (state, host, "pendingThreadAssignmentIds")
        for host, state in migration.items()
        if isinstance(state, dict) and isinstance(state.get("pendingThreadAssignmentIds"), list)
    ]


_HOST_LISTS = {READ_STATE: _unread_lists, MIGRATION: _pending_lists}


def _host_lists_ids(key: str, value: object) -> list[tuple[str, str]]:
    """(host, id) for every string in the host lists of a known key."""
    if key not in _HOST_LISTS:
        return []
    return [(host, v) for hosts, host, field in _HOST_LISTS[key](value) for v in hosts[field] if isinstance(v, str)]


# The daemon's loaded threads (`app-server-daemon/loaded-threads.json`)


def parse_loaded(raw: bytes) -> list | None:
    """The daemon's list, or None when it is not a JSON list of strings."""
    data = _json(raw)
    if isinstance(data, list) and all(isinstance(entry, str) for entry in data) and _writable(data):
        return data
    return None


def loaded_ids(entries: list[str], wanted: Callable[[str], bool]) -> set[str]:
    """The ids `wanted` accepts that the list names: a plain entry, or a key
    of an `codex-interrupted-v1:{"interrupted":{…}}` entry's map."""
    found = {e for e in entries if wanted(e)}
    for entry in entries:
        found |= {i for i in _interrupted(entry) or {} if wanted(i)}
    return found


def without_loaded(entries: list[str], ids: set[str]) -> list[str]:
    """The list with no entry of `ids`: an interrupted entry loses their
    threads, and goes once it has none left. An entry of another kind stays."""
    kept: list[str] = []
    for entry in entries:
        if entry in ids:
            continue
        threads = _interrupted(entry)
        if threads is not None and ids & set(threads):
            rest = {k: v for k, v in threads.items() if k not in ids}
            if not rest:
                continue
            entry = INTERRUPTED + dump_state({"interrupted": rest}).decode("utf-8")
        kept.append(entry)
    return kept


def _interrupted(entry: str) -> dict | None:
    """The thread map of an interrupted entry whose JSON is just
    `{"interrupted": {…}}` and can be written back; None for any other
    entry, which stays as it is."""
    if not entry.startswith(INTERRUPTED):
        return None
    data = _json_text(entry[len(INTERRUPTED):])
    if isinstance(data, dict) and set(data) == {"interrupted"} and isinstance(data["interrupted"], dict) \
            and _writable(data):
        return data["interrupted"]
    return None


def _json(raw: bytes) -> object:
    try:
        return _json_text(raw.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError
        return None


def _json_text(text: str) -> object:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _writable(data: object) -> bool:
    """Whether the file can be put back in the app's layout: a lone
    surrogate (`\\ud800`) has no UTF-8, so such a file is left alone."""
    try:
        dump_state(data)  # type: ignore[arg-type]
    except (ValueError, RecursionError):  # UnicodeEncodeError is a ValueError
        return False
    return True
