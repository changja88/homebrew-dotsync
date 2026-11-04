"""The ChatGPT app's own files in the Codex home, as the Codex cleanup reads
and edits them.

The app lists the sidebar's threads in a catalog of its own,
`sqlite/codex.db` (`sqlite/codex-dev.db` in its non-production builds), and
keeps per-thread window state in `.codex-global-state.json` and its copy
`.bak`. `codex delete` touches neither, so a deleted session stays in both.
The app checks its whole catalog again only when the local host's
`last_full_reconciled_at` is empty: its own migrations clear it to make it do
so. Neither file is a Codex store with migrations dotsync knows: one of
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
    """The local threads the catalog still lists. In the active catalog, none
    once a full check is due: the app will drop what is gone by itself. In
    the old one every local row counts, marked missing or not, since no app
    here will ever check it."""
    if active:
        if _check_due(con):
            return set()
        rows = con.execute(
            f"SELECT thread_id FROM local_thread_catalog WHERE missing_candidate = 0 AND host_id IN ({_LOCAL_HOSTS})"
        )
    else:
        rows = con.execute(f"SELECT thread_id FROM local_thread_catalog WHERE host_id IN ({_LOCAL_HOSTS})")
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
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return None
    if not isinstance(data, dict) or not isinstance(data.get(ATOMS, {}), dict):
        return None
    return data


def dump_state(data: dict) -> bytes:
    """The app's own layout: compact, and UTF-8 as it is."""
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
    """The ids a key names with another host before them
    (`thread-client-id-v1:ssh%3Aserver%3A<id>`): that host's threads, every
    entry of theirs in any state file."""
    remote: set[str] = set()
    for level in (data, data.get(ATOMS, {})):
        for key in level:
            parts = unquote(key).split(":")
            if len(parts) > 2 and parts[1] != "local":
                remote.add(parts[-1])
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
        if key == READ_STATE:
            value = copy.deepcopy(value)
            for hosts, host in _unread_lists(value):
                hosts[host] = [v for v in hosts[host] if not (isinstance(v, str) and v in ids)]
        elif isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in ids and not (isinstance(v, str) and v in ids)}
        elif isinstance(value, list):
            value = [v for v in value if not (isinstance(v, str) and v in ids)]
        kept[key] = value
    return kept


def _entries(key: str, value: object) -> set[str]:
    """Whose entries one key of a level holds: a key that ends in a local
    thread id (`thread-tab-routes-v1:<id>`, `thread-client-id-v1:local%3A<id>`)
    is that thread's; a map holds its keys' and its string values' threads, a
    list its string elements', and the read state its local unread lists'.
    Anything deeper, an id only mentioned in a text, or another host's, is no
    one's."""
    owner = _key_owner(key)
    owners = {owner} if owner else set()
    if key == READ_STATE:
        owners |= {v for hosts, host in _unread_lists(value) for v in hosts[host] if isinstance(v, str)}
    elif isinstance(value, dict):
        owners |= set(value) | {v for v in value.values() if isinstance(v, str)}
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


def _unread_lists(read_state: object) -> list[tuple[dict, str]]:
    """Where the read state keeps this machine's unread threads, as (map,
    host key): `unreadByIdentity.<identity>.local:<…>` and
    `legacyMigration.unreadThreadIdsByHostId.local`."""
    if not isinstance(read_state, dict):
        return []
    maps: list[dict] = []
    by_identity = read_state.get("unreadByIdentity")
    if isinstance(by_identity, dict):
        maps += [hosts for hosts in by_identity.values() if isinstance(hosts, dict)]
    legacy = read_state.get("legacyMigration")
    if isinstance(legacy, dict) and isinstance(legacy.get("unreadThreadIdsByHostId"), dict):
        maps.append(legacy["unreadThreadIdsByHostId"])
    return [
        (hosts, host)
        for hosts in maps
        for host, ids in hosts.items()
        if (host == "local" or host.startswith("local:")) and isinstance(ids, list)
    ]
