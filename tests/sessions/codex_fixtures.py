"""Builds fake Codex homes for the Codex session tests: the three SQLite
databases from Codex 0.164.0's own migrations, and rollout files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "codex-0.164.0"
DATABASES = {
    "state_5.sqlite": "state",
    "queue_1.sqlite": "queue",
    "thread_history_1.sqlite": "thread_history",
}


def make_codex_home(home: Path, sqlite_home: Path | None = None) -> Path:
    """Create `home` and the three databases (in `sqlite_home`, default `home`),
    migrated the way sqlx does. Returns the directory that holds them."""
    home.mkdir(parents=True, exist_ok=True)
    db_dir = sqlite_home or home
    db_dir.mkdir(parents=True, exist_ok=True)
    for db_name, folder in DATABASES.items():
        _migrate(db_dir / db_name, FIXTURES / folder)
    return db_dir


def _migrate(db: Path, migrations: Path) -> None:
    con = sqlite3.connect(db)
    con.execute("PRAGMA auto_vacuum = 2")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute(
        "CREATE TABLE _sqlx_migrations (version BIGINT PRIMARY KEY, description TEXT NOT NULL, "
        "installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, success BOOLEAN NOT NULL, "
        "checksum BLOB NOT NULL, execution_time BIGINT NOT NULL)"
    )
    for sql_file in sorted(migrations.glob("*.sql")):
        sql = sql_file.read_bytes()
        con.executescript(sql.decode())
        version, _, description = sql_file.stem.partition("_")
        con.execute(
            "INSERT INTO _sqlx_migrations (version, description, success, checksum, execution_time)"
            " VALUES (?, ?, 1, ?, 0)",
            (int(version), description, hashlib.sha384(sql).digest()),
        )
    con.commit()
    con.close()


def add_thread(
    db_dir: Path,
    thread_id: str,
    *,
    name: str | None = None,
    title: str = "a title",
    cwd: str = "/work/repo",
    archived: bool = False,
    source: str = "cli",
    updated_at_ms: int = 1_700_000_000_000,
    rollout_path: str = "",
) -> None:
    con = sqlite3.connect(db_dir / "state_5.sqlite")
    con.execute(
        "INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider,"
        " cwd, title, sandbox_policy, approval_mode, archived, name, updated_at_ms)"
        " VALUES (?, ?, 0, ?, ?, 'p', ?, ?, 's', 'a', ?, ?, ?)",
        (
            thread_id,
            rollout_path,
            updated_at_ms // 1000,
            source,
            cwd,
            title,
            int(archived),
            name,
            updated_at_ms,
        ),
    )
    con.commit()
    con.close()


def add_edge(db_dir: Path, parent: str, child: str) -> None:
    con = sqlite3.connect(db_dir / "state_5.sqlite")
    con.execute(
        "INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status)"
        " VALUES (?, ?, 'open')",
        (parent, child),
    )
    con.commit()
    con.close()


HISTORY_TABLES = (
    "thread_items",
    "thread_realtime_items",
    "thread_turns",
    "thread_history_projection_state",
)


def add_history(db_dir: Path, key: str) -> None:
    """One row in each history table, keyed by `key` (a rollout id, which is
    the thread id unless the rollout was reverted)."""
    con = sqlite3.connect(db_dir / "thread_history_1.sqlite")
    con.execute(
        "INSERT INTO thread_turns (thread_id, turn_id, rollout_ordinal, status) VALUES (?, 't', 0, 'done')",
        (key,),
    )
    con.execute(
        "INSERT INTO thread_items (thread_id, turn_id, item_id, rollout_ordinal, created_at_ms, item_json)"
        " VALUES (?, 't', 'i', 0, 0, '{}')",
        (key,),
    )
    con.execute(
        "INSERT INTO thread_realtime_items (thread_id, item_id, rollout_ordinal, created_at_ms, item_type,"
        " item_json) VALUES (?, 'r', 0, 0, 'x', '{}')",
        (key,),
    )
    con.execute(
        "INSERT INTO thread_history_projection_state (thread_id, next_rollout_byte_offset,"
        " next_rollout_ordinal) VALUES (?, 0, 0)",
        (key,),
    )
    con.commit()
    con.close()


def write_rollout(
    home: Path,
    thread_id: str,
    *,
    rollout_id: str | None = None,
    archived: bool = False,
    zst: bool = False,
    before_meta: Iterable[dict] = (),
    meta: dict | None = None,
) -> Path:
    """A rollout file for `thread_id` under `home`. `rollout_id` makes it a
    reverted-style `<thread>_<rollout>` name; `zst` only changes the name (the
    list never reads contents)."""
    stamp = "2026-01-02T03-04-05"
    folder = home / ("archived_sessions" if archived else "sessions/2026/01/02")
    folder.mkdir(parents=True, exist_ok=True)
    suffix = f"_{rollout_id}" if rollout_id else ""
    path = folder / f"rollout-{stamp}-{thread_id}{suffix}.jsonl{'.zst' if zst else ''}"
    records = [*before_meta, {"type": "session_meta", "payload": meta or {"id": thread_id}}]
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path
