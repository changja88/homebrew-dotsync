"""`cleanup codex --yes`: the only place dotsync writes inside Codex's own
databases and history files. It deletes exactly what `orphans()` reports, only
while nothing uses ~/.codex and under Codex's coordination lock; kept rows and
lines must come out byte-for-byte as they went in, and a history file is never
left half-written."""

import errno
import fcntl
import json
import os
import signal
import sqlite3
import stat
from pathlib import Path

import pytest

from dotsync import cli, locks
from dotsync.sessions import codex, codex_blockers, codex_cleanup, codex_keepset, procs, roots
from dotsync.sessions.stop import StopSignal
from tests.sessions.codex_fixtures import (
    HISTORY_TABLES,
    add_history,
    add_thread,
    make_codex_home,
    write_rollout,
)

KEPT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
GONE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GONE2 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
GONE3 = "ffffffff-ffff-4fff-8fff-ffffffffffff"
BASE = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"  # kept only by FORK's history_base
FORK = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
SECRET = "secret-history-text-xyz"
ALL = ["history_rows", "history_lines", "session_index", "queue"]
ORDER = [*ALL, "cache_files"]
BLOCKER = {"pid": 9, "kind": "terminal_codex", "name": "codex", "elapsed": 1}


@pytest.fixture(autouse=True)
def nothing_runs(monkeypatch):
    monkeypatch.setattr(procs, "ps_table", lambda: {})
    monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: {})


@pytest.fixture
def db():
    db_dir = make_codex_home(roots.codex_home())
    add_thread(db_dir, KEPT)
    write_rollout(roots.codex_home(), KEPT)
    write_rollout(roots.codex_home(), FORK, meta={"id": FORK, "history_base": {"thread_id": BASE}})
    coordination_lock().parent.mkdir()
    coordination_lock().write_bytes(b"")  # as Codex leaves it
    return db_dir


def run(items=ALL, *, stop=None, progress=None):
    return codex_cleanup.run_cleanup(
        list(items), False, stop=stop or StopSignal(), progress=progress or (lambda done, total: None)
    )


def outcomes(report):
    return [(r["id"], r["outcome"], r.get("reason")) for r in report["items"]]


def by_id(report):
    return {r["id"]: r for r in report["items"]}


# Fake stores


def history_row(db_dir, table, key, n=0):
    """One row keyed by `key` in one history table only."""
    con = sqlite3.connect(db_dir / "thread_history_1.sqlite")
    sql = {
        "thread_turns": "INSERT INTO thread_turns (thread_id, turn_id, rollout_ordinal, status)"
        " VALUES (?, 't{n}', {n}, 'done')",
        "thread_items": "INSERT INTO thread_items (thread_id, turn_id, item_id, rollout_ordinal,"
        " created_at_ms, item_json) VALUES (?, 't', 'i{n}', {n}, 0, '{{}}')",
        "thread_realtime_items": "INSERT INTO thread_realtime_items (thread_id, item_id,"
        " rollout_ordinal, created_at_ms, item_type, item_json) VALUES (?, 'r{n}', {n}, 0, 'x', '{{}}')",
        "thread_history_projection_state": "INSERT INTO thread_history_projection_state"
        " (thread_id, next_rollout_byte_offset, next_rollout_ordinal) VALUES (?, {n}, {n})",
    }[table]
    con.execute(sql.format(n=n), (key,))
    con.commit()
    con.close()


def add_queue(db_dir, thread_id, n=1):
    """`n` queued items for `thread_id`; Codex's trigger upserts its revision."""
    con = sqlite3.connect(db_dir / "queue_1.sqlite")
    (start,) = con.execute("SELECT COUNT(*) FROM queued_items WHERE thread_id = ?", (thread_id,)).fetchone()
    for order in range(start, start + n):
        con.execute(
            "INSERT INTO queued_items (id, thread_id, payload_json, queue_order, created_at_ms,"
            " updated_at_ms) VALUES (?, ?, '{}', ?, 0, 0)",
            (f"{thread_id}-{order}", thread_id, order),
        )
    con.commit()
    con.close()


def queue_sql(db_dir, sql, args=()):
    con = sqlite3.connect(db_dir / "queue_1.sqlite")
    con.execute(sql, args)
    con.commit()
    con.close()


def rows(db_file, table):
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT rowid, * FROM {table} ORDER BY rowid").fetchall()
    finally:
        con.close()


def keyed(db_file, table, key):
    return [r for r in rows(db_file, table) if key in r]


def jsonl(name, data: bytes):
    path = roots.codex_home() / name
    path.write_bytes(data)
    return path


def line(key, value, **extra):
    return json.dumps({key: value, **extra}).encode() + b"\n"


def cache(thread_id):
    return roots.codex_home() / codex.CACHE_DIR / thread_id


def snapshot():
    """Everything under the Codex home: each database by its logical content
    (a write may sit in the -wal), every other file by its bytes."""
    found = {}
    for path in sorted(roots.codex_home().rglob("*")):
        name = path.name
        if name.endswith(("-wal", "-shm")) or path.is_dir():
            continue
        if name.endswith(".sqlite"):
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            found[str(path)] = list(con.iterdump())
            con.close()
        else:
            found[str(path)] = path.read_bytes()
    return found


def logs():
    folder = roots.log_dir()
    return [json.loads(p.read_text()) for p in sorted(folder.iterdir())] if folder.exists() else []


def coordination_lock():
    return roots.codex_home() / "thread-writer-locks" / ".coordination.lock"


def try_coordination_lock() -> bool:
    """Whether another holder could take the coordination lock right now."""
    with open(coordination_lock(), "a") as other:
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(other, fcntl.LOCK_UN)
        return True


def full_home(db_dir):
    """An orphan (GONE) in every store, next to a kept id in every store."""
    for key in (GONE, KEPT, BASE):
        add_history(db_dir, key)
    add_queue(db_dir, GONE, 2)
    add_queue(db_dir, KEPT)
    jsonl(
        "history.jsonl",
        line("session_id", GONE, ts=1, text=SECRET)
        + line("session_id", KEPT, ts=2, text="kept")
        + line("session_id", GONE, ts=3, text=SECRET),
    )
    jsonl("session_index.jsonl", line("id", GONE, thread_name=SECRET) + line("id", KEPT, thread_name="k"))
    cache(GONE).parent.mkdir()
    cache(GONE).write_text("")
    cache(KEPT).write_text("")


# Refusals: nothing changes


@pytest.mark.parametrize("failing", [None, "ps"])
def test_any_blocker_refuses_with_codex_running(db, monkeypatch, failing):
    full_home(db)
    if failing:
        monkeypatch.setattr(procs, "ps_table", lambda: (_ for _ in ()).throw(procs.ToolFailure("timeout")))
    else:
        monkeypatch.setattr(codex_blockers, "detect", lambda: {"blockers": [BLOCKER]})
    monkeypatch.setattr(codex_keepset, "support_check", lambda: pytest.fail("checked while Codex runs"))
    before = snapshot()
    with pytest.raises(codex_cleanup.JSONCommandError) as err:
        run()
    assert err.value.code == "codex_running"
    assert snapshot() == before
    [doc] = logs()  # the codex lock was held, so the run is logged
    assert doc["result"]["error"]["code"] == "codex_running"
    assert doc["result"]["items"] == []


def test_support_refusal_changes_nothing_and_is_logged(db):
    full_home(db)
    (roots.codex_home() / "rollout-migrations").mkdir()
    (roots.codex_home() / "rollout-migrations" / f"{GONE}.pending").write_text("")
    before = snapshot()
    with pytest.raises(codex_cleanup.JSONCommandError) as err:
        run()
    assert err.value.code == "codex_version"
    assert snapshot() == before
    [doc] = logs()
    assert (doc["command"], doc["tool"]) == ("cleanup", "codex")
    assert doc["result"]["error"]["code"] == "codex_version"


def test_scan_refusal_changes_nothing(db):
    full_home(db)
    folder = roots.codex_home() / "sessions" / "2026" / "01" / "02"
    (folder / f"rollout-2026-01-02T03-04-05-{GONE}.jsonl").write_text("")  # no SessionMeta
    before = snapshot()
    with pytest.raises(codex_cleanup.JSONCommandError) as err:
        run()
    assert err.value.code == "incomplete_scan"
    assert snapshot() == before
    assert logs()[0]["result"]["error"]["code"] == "incomplete_scan"


def test_cleanup_busy_while_a_codex_delete_holds_the_lock(db, monkeypatch, capsys):
    full_home(db)
    real = locks.tool_lock
    monkeypatch.setattr(
        locks, "tool_lock", lambda name, *, shared, wait=10.0: real(name, shared=shared, wait=0.2)
    )
    before = snapshot()
    with real("codex", shared=False):
        code = cli.main(["sessions", "cleanup", "codex", "--yes", "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "busy"
    assert snapshot() == before
    assert logs() == []  # it never held the lock


def test_coordination_lock_held_by_a_codex_is_codex_running(db, monkeypatch):
    full_home(db)
    monkeypatch.setattr(codex_cleanup, "COORDINATION_WAIT", 0.2)
    before = snapshot()
    with open(coordination_lock(), "a") as codex_holds:
        fcntl.flock(codex_holds, fcntl.LOCK_EX)
        with pytest.raises(codex_cleanup.JSONCommandError) as err:
            run()
    assert err.value.code == "codex_running"
    assert snapshot() == before
    assert logs()[0]["result"]["error"]["code"] == "codex_running"


@pytest.mark.parametrize("items", ["history_rows,nope", "", "queue,,history_rows"])
def test_unknown_item_is_bad_items_before_any_lock(db, capsys, items):
    full_home(db)
    before = snapshot()
    code = cli.main(["sessions", "cleanup", "codex", "--yes", "--items", items, "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_items"
    assert snapshot() == before
    assert logs() == []


def run_vacuum(items=ALL, *, stop=None):
    return codex_cleanup.run_cleanup(
        list(items), True, stop=stop or StopSignal(), progress=lambda done, total: None
    )


def test_vacuum_runs_after_the_items_under_both_locks(db, monkeypatch):
    full_home(db)
    held: list[bool] = []
    real = codex_cleanup.reclaim

    def checking(path, **kwargs):
        held.append(not try_coordination_lock())
        return real(path, **kwargs)

    monkeypatch.setattr(codex_cleanup, "reclaim", checking)
    report = run_vacuum()
    assert held == [True]
    assert report["vacuum"]["stopped_because"] in ("freelist_empty", "no_progress")
    assert outcomes(report) == [(i, "done", None) for i in ORDER]


def test_vacuum_is_unavailable_without_incremental_auto_vacuum(db):
    full_home(db)
    con = sqlite3.connect(db / "thread_history_1.sqlite")
    con.execute("PRAGMA auto_vacuum = 0")
    con.execute("VACUUM")
    con.close()
    vacuum = run_vacuum()["vacuum"]
    assert vacuum["stopped_because"] == "unavailable"
    assert vacuum["bytes_before"] == vacuum["bytes_after"] == codex.history_bytes()


def test_vacuum_does_not_start_after_a_stop(db, monkeypatch):
    full_home(db)
    monkeypatch.setattr(codex_cleanup, "reclaim", lambda *a, **k: pytest.fail("reclaim ran"))
    stop = StopSignal()
    stop.requested = True
    vacuum = run_vacuum(stop=stop)["vacuum"]
    assert vacuum["stopped_because"] == "stopped"
    assert vacuum["bytes_before"] == vacuum["bytes_after"] == codex.history_bytes()


def test_vacuum_does_not_start_after_a_blocker(db, monkeypatch):
    full_home(db)
    monkeypatch.setattr(codex_cleanup, "reclaim", lambda *a, **k: pytest.fail("reclaim ran"))
    answers = iter([[], [BLOCKER]])  # clear at the start, then Codex starts
    monkeypatch.setattr(codex_blockers, "detect", lambda: {"blockers": next(answers, [BLOCKER])})
    report = run_vacuum()
    assert report["vacuum"]["stopped_because"] == "blocker"
    assert {r.get("reason") for r in report["items"]} == {"codex_started"}


def test_a_vacuum_that_cannot_open_keeps_the_item_results(db, monkeypatch, capsys):
    full_home(db)
    monkeypatch.setattr(codex_keepset, "support_check", lambda: (None, True))
    monkeypatch.setattr(codex_cleanup, "reclaim", lambda path, **kw: codex_cleanup._failed_vacuum(0, "unable to open"))
    argv = ["sessions", "cleanup", "codex", "--yes", "--items", "queue", "--vacuum", "--json"]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["vacuum"]["stopped_because"] == "error"
    assert [(r["id"], r["outcome"]) for r in out["items"]][0] == ("queue", "done")


def test_an_unopenable_history_db_in_the_run_is_an_error_result(db, monkeypatch):
    full_home(db)
    monkeypatch.setattr(codex_keepset, "support_check", lambda: (None, True))
    real = codex_cleanup._writing

    def writing(path):
        if path.name == "thread_history_1.sqlite":
            raise sqlite3.OperationalError("unable to open database file")
        return real(path)

    monkeypatch.setattr(codex_cleanup, "_writing", writing)
    report = run_vacuum(["queue"])
    assert report["vacuum"]["stopped_because"] == "error"
    assert ("queue", "done", None) in outcomes(report)


def test_a_support_check_failure_in_the_vacuum_keeps_the_items(db, monkeypatch):
    full_home(db)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) > 1:  # the first call is the run's own check
            raise sqlite3.OperationalError("disk I/O error")
        return None, True

    monkeypatch.setattr(codex_keepset, "support_check", flaky)
    report = run_vacuum()
    assert report["vacuum"]["stopped_because"] == "error"
    assert "disk I/O error" in report["vacuum"]["message"]
    assert outcomes(report) == [(i, "done", None) for i in ORDER]


# The coordination lock


def test_coordination_lock_is_held_during_the_run(db, monkeypatch):
    full_home(db)
    free_inside: list[bool] = []
    real = codex_cleanup.rewrite_jsonl

    def checking(path, drop):
        free_inside.append(try_coordination_lock())
        return real(path, drop)

    monkeypatch.setattr(codex_cleanup, "rewrite_jsonl", checking)
    run()
    assert free_inside == [False, False]  # history.jsonl and session_index.jsonl
    assert try_coordination_lock()  # released at the end


def test_coordination_lock_is_taken_before_the_checks(db, monkeypatch):
    full_home(db)
    free_at: dict[str, bool] = {}
    real_support, real_keep = codex_keepset.support_check, codex_keepset.keep_set

    def support():
        free_at["support_check"] = try_coordination_lock()
        return real_support()

    def keep(progress=None):
        free_at["keep_set"] = try_coordination_lock()
        return real_keep(progress)

    monkeypatch.setattr(codex_keepset, "support_check", support)
    monkeypatch.setattr(codex_keepset, "keep_set", keep)
    run()
    assert free_at == {"support_check": False, "keep_set": False}


def test_a_missing_codex_home_is_never_made(monkeypatch, tmp_path):
    home = tmp_path / "no-codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    with pytest.raises(codex_cleanup.JSONCommandError) as err:
        run()
    assert err.value.code == "codex_version"
    assert not home.exists()


def test_coordination_lock_folder_is_made_when_missing(db):
    coordination_lock().unlink()
    coordination_lock().parent.rmdir()
    run()
    assert coordination_lock().is_file()


# History rows


def test_history_rows_deleted_per_id_only_for_orphans(db):
    for key in (GONE, GONE2, KEPT, BASE):
        add_history(db, key)
    history_row(db, "thread_items", KEPT, n=1)
    history = db / "thread_history_1.sqlite"
    kept_before = {t: [r for r in rows(history, t) if GONE not in r and GONE2 not in r] for t in HISTORY_TABLES}
    report = run(["history_rows"])
    assert by_id(report)["history_rows"] == {"id": "history_rows", "outcome": "done", "count": 2}
    for table in HISTORY_TABLES:
        assert keyed(history, table, GONE) == [] and keyed(history, table, GONE2) == []
        assert rows(history, table) == kept_before[table]  # rowids and values as they were
    assert logs()[0]["result"]["ids"]["history_rows"] == [GONE, GONE2]


@pytest.mark.parametrize("table", HISTORY_TABLES)
def test_an_orphan_in_one_history_table_only_is_deleted(db, table):
    history_row(db, table, GONE)
    for t in HISTORY_TABLES:
        history_row(db, t, KEPT, n=5)
    history = db / "thread_history_1.sqlite"
    kept_before = {t: rows(history, t) for t in HISTORY_TABLES if t != table}
    kept_before[table] = keyed(history, table, KEPT)
    assert codex_cleanup.dry_run_payload()["items"][0]["count"] == 1
    report = run(["history_rows"])
    assert by_id(report)["history_rows"]["count"] == 1
    assert keyed(history, table, GONE) == []
    assert {t: rows(history, t) for t in HISTORY_TABLES} == kept_before


def test_writes_are_one_begin_immediate_per_id_items_first_and_no_pragma(db, monkeypatch):
    for key in (GONE, GONE2, KEPT):
        add_history(db, key)
    add_queue(db, GONE, 2)
    statements: dict[str, list[str]] = {}
    real = sqlite3.connect

    def tracing(target, *args, **kwargs):
        con = real(target, *args, **kwargs)
        if "mode=rw" in str(target):
            name = Path(str(target).removeprefix("file:").split("?")[0]).name
            con.set_trace_callback(lambda sql: statements.setdefault(name, []).append(sql))
        return con

    monkeypatch.setattr(sqlite3, "connect", tracing)
    run(["history_rows", "queue"])
    # A trigger's statements show up as repeats of the statement that fired it.
    statements = {
        name: [s for i, s in enumerate(seq) if i == 0 or s != seq[i - 1]]
        for name, seq in statements.items()
    }

    def per_id(tid):
        return ["BEGIN IMMEDIATE", *(f"DELETE FROM {t} WHERE thread_id = '{tid}'" for t in HISTORY_TABLES), "COMMIT"]

    history = [s for s in statements["thread_history_1.sqlite"] if not s.startswith("--")]
    assert history == per_id(GONE) + per_id(GONE2)
    queue = [s for s in statements["queue_1.sqlite"] if not s.startswith("--")]
    assert queue[0] == "BEGIN IMMEDIATE" and queue[-1] == "COMMIT"
    deletes = [s for s in queue if s.startswith("DELETE")]
    assert deletes == [
        f"DELETE FROM queued_items WHERE thread_id = '{GONE}'",
        f"DELETE FROM queued_thread_revisions WHERE thread_id = '{GONE}'",
    ]
    assert not any("PRAGMA" in s.upper() for s in history + queue)


@pytest.mark.parametrize(
    "db_name, item, table",
    [("thread_history_1.sqlite", "history_rows", "thread_turns"), ("queue_1.sqlite", "queue", "queued_items")],
)
def test_a_busy_database_fails_that_item_and_the_rest_still_run(db, monkeypatch, db_name, item, table):
    full_home(db)
    monkeypatch.setattr(codex_cleanup, "WRITE_TIMEOUT", 0.1)
    other = sqlite3.connect(db / db_name, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        report = run()
    finally:
        other.execute("ROLLBACK")
        other.close()
    failed = by_id(report)[item]
    assert (failed["outcome"], failed["count"]) == ("failed", 0)
    assert "locked" in failed["message"]
    assert [r["outcome"] for r in report["items"] if r["id"] != item] == ["done"] * 4
    assert keyed(db / db_name, table, GONE) != []


def test_sigterm_during_history_rows_finishes_that_id_then_stops(db):
    for key in (GONE, GONE2, GONE3):
        add_history(db, key)
    jsonl("history.jsonl", line("session_id", GONE))

    def progress(done, total):
        if (done, total) == (1, 3):  # the history ids; the walk has 2 rollouts
            os.kill(os.getpid(), signal.SIGTERM)

    with StopSignal() as stop:
        report = run(stop=stop, progress=progress)
    assert report["stopped"] is True
    assert by_id(report)["history_rows"] == {
        "id": "history_rows", "outcome": "skipped", "reason": "stopped", "count": 1,
    }
    assert outcomes(report)[1:] == [(i, "skipped", "stopped") for i in ORDER[1:]]
    history = db / "thread_history_1.sqlite"
    for table in HISTORY_TABLES:
        assert keyed(history, table, GONE) == []  # its transaction finished
        assert len(keyed(history, table, GONE2)) == len(keyed(history, table, GONE3)) == 1
    assert (roots.codex_home() / "history.jsonl").read_bytes() == line("session_id", GONE)
    doc = logs()[0]["result"]
    assert doc["stopped"] is True and doc["ids"]["history_rows"] == [GONE]


def test_progress_follows_the_walk_then_the_history_ids(db):
    for key in (GONE, GONE2, GONE3):
        add_history(db, key)
    seen = []
    run(progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2), (1, 3), (2, 3), (3, 3)]


# history.jsonl and session_index.jsonl


@pytest.fixture
def folder(tmp_path):
    """An empty folder of its own (tmp_path also holds the fake home)."""
    path = tmp_path / "files"
    path.mkdir()
    return path


def test_rewrite_keeps_unrelated_bytes(folder):
    path = folder / "history.jsonl"
    gone = b'{"session_id":"' + GONE.encode() + b'"'
    kept = (
        b"not json " + GONE.encode() + b"\n",
        b'{"session_id":"' + KEPT.encode() + b'","text":"crlf"}\r\n',
        b"\xc3\x28 not utf-8\n",
        b"\n",
        b"   \r\n",
    )
    data = (
        gone + b',"ts":1}\n' + kept[0] + kept[1] + gone + b',"ts":2}\r\n' + kept[2] + kept[3] + kept[4]
        + b'{"session_id":"' + KEPT.encode() + b'"}'  # the last line has no newline
    )
    path.write_bytes(data)
    path.chmod(0o640)
    dropped = codex_cleanup.rewrite_jsonl(path, lambda raw: raw.startswith(gone))
    assert dropped == 2
    assert path.read_bytes() == b"".join(kept) + b'{"session_id":"' + KEPT.encode() + b'"}'
    assert path.stat().st_mode & 0o777 == 0o640
    assert os.listdir(folder) == ["history.jsonl"]  # no backup, no temp file


def test_rewrite_dropping_the_last_line_keeps_the_rest_whole(folder):
    path = folder / "session_index.jsonl"
    path.write_bytes(b"a\nb\nlast")
    assert codex_cleanup.rewrite_jsonl(path, lambda raw: raw == b"last") == 1
    assert path.read_bytes() == b"a\nb\n"


def test_rewrite_with_nothing_to_drop_leaves_the_file_alone(folder, monkeypatch):
    path = folder / "history.jsonl"
    path.write_bytes(b"a\nb")
    before = os.stat(path)
    for name in ("link", "replace", "unlink"):
        monkeypatch.setattr(os, name, lambda *a, **k: pytest.fail("wrote"))
    assert codex_cleanup.rewrite_jsonl(path, lambda raw: False) == 0
    after = os.stat(path)
    assert (after.st_ino, after.st_mtime_ns, after.st_nlink) == (before.st_ino, before.st_mtime_ns, 1)
    assert os.listdir(folder) == ["history.jsonl"]


def test_rewrite_of_a_missing_file_drops_nothing(folder):
    assert codex_cleanup.rewrite_jsonl(folder / "history.jsonl", lambda raw: True) == 0
    assert os.listdir(folder) == []


def test_crash_after_backup_link_leaves_old_file_whole(folder, monkeypatch):
    path = folder / "history.jsonl"
    data = b"drop\nkeep\n"
    path.write_bytes(data)

    def crash(src, dst):
        raise OSError("power cut")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="power cut"):
        codex_cleanup.rewrite_jsonl(path, lambda raw: raw == b"drop\n")
    assert path.read_bytes() == data
    [backup] = [p for p in folder.iterdir() if p != path]  # the temp file is gone
    assert backup.name.startswith("history.jsonl.dotsync-backup-")
    assert backup.read_bytes() == data


def test_rewrite_holds_the_files_lock(folder):
    path = folder / "history.jsonl"
    path.write_bytes(b"drop\n")
    free = []

    def drop(raw):
        with open(path, "rb") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                free.append(True)
            except BlockingIOError:
                free.append(False)
        return True

    codex_cleanup.rewrite_jsonl(path, drop)
    assert free == [False]


class Syncs:
    """Records the F_FULLFSYNC calls on `fcntl.fcntl` (raising `error` when
    set) and the plain `os.fsync` calls, by the kind of file each fd is."""

    def __init__(self, monkeypatch, error: OSError | None = None):
        self.full: list[str] = []
        self.plain: list[str] = []
        self.error = error
        real = fcntl.fcntl

        def fake_fcntl(fd, cmd, *args):
            if cmd != fcntl.F_FULLFSYNC:
                return real(fd, cmd, *args)
            self.full.append(self.kind(fd))
            if self.error:
                raise self.error
            return 0

        monkeypatch.setattr(fcntl, "fcntl", fake_fcntl)
        monkeypatch.setattr(os, "fsync", lambda fd: self.plain.append(self.kind(fd)))

    @staticmethod
    def kind(fd):
        mode = os.fstat(fd).st_mode
        return "folder" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"


def test_full_fsync_asks_the_drive_with_f_fullfsync(folder, monkeypatch):
    syncs = Syncs(monkeypatch)
    fd = os.open(folder, os.O_RDONLY)
    try:
        codex_cleanup._full_fsync(fd)
    finally:
        os.close(fd)
    assert (syncs.full, syncs.plain) == (["folder"], [])


@pytest.mark.parametrize("code", [errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL])
def test_full_fsync_falls_back_to_fsync_where_unsupported(folder, monkeypatch, code):
    syncs = Syncs(monkeypatch, OSError(code, os.strerror(code)))
    fd = os.open(folder, os.O_RDONLY)
    try:
        codex_cleanup._full_fsync(fd)
    finally:
        os.close(fd)
    assert (syncs.full, syncs.plain) == (["folder"], ["folder"])


def test_full_fsync_other_errors_still_fail(folder, monkeypatch):
    syncs = Syncs(monkeypatch, OSError(errno.EIO, "I/O error"))
    fd = os.open(folder, os.O_RDONLY)
    try:
        with pytest.raises(OSError, match="I/O error"):
            codex_cleanup._full_fsync(fd)
    finally:
        os.close(fd)
    assert syncs.plain == []


def test_full_fsync_without_the_constant_is_fsync(folder, monkeypatch):
    syncs = Syncs(monkeypatch)
    monkeypatch.delattr(fcntl, "F_FULLFSYNC")
    fd = os.open(folder, os.O_RDONLY)
    try:
        codex_cleanup._full_fsync(fd)
    finally:
        os.close(fd)
    assert (syncs.full, syncs.plain) == ([], ["folder"])


def test_rewrite_full_fsyncs_the_new_file_then_the_folder(folder, monkeypatch):
    path = folder / "history.jsonl"
    path.write_bytes(b"drop\nkeep\n")
    syncs = Syncs(monkeypatch)
    assert codex_cleanup.rewrite_jsonl(path, lambda raw: raw == b"drop\n") == 1
    assert (syncs.full, syncs.plain) == (["file", "folder"], [])
    assert path.read_bytes() == b"keep\n"


def test_a_failed_full_fsync_of_the_new_file_keeps_the_old_file(folder, monkeypatch):
    path = folder / "history.jsonl"
    path.write_bytes(b"drop\nkeep\n")
    Syncs(monkeypatch, OSError(errno.EIO, "I/O error"))
    with pytest.raises(OSError, match="I/O error"):
        codex_cleanup.rewrite_jsonl(path, lambda raw: raw == b"drop\n")
    assert path.read_bytes() == b"drop\nkeep\n"
    assert [p.name.startswith("history.jsonl.dotsync-backup-") for p in folder.iterdir() if p != path] == [True]


def test_an_older_backup_is_left_alone(db):
    full_home(db)
    old = roots.codex_home() / "history.jsonl.dotsync-backup-20260101T000000Z"
    old.write_bytes(b"from a crashed run\n")
    report = run()
    assert by_id(report)["history_lines"]["outcome"] == "done"
    assert old.read_bytes() == b"from a crashed run\n"


def test_history_and_index_lines_of_orphans_are_dropped(db):
    full_home(db)
    report = run(["history_lines", "session_index"])
    assert by_id(report)["history_lines"] == {"id": "history_lines", "outcome": "done", "count": 1, "lines": 2}
    assert by_id(report)["session_index"] == {"id": "session_index", "outcome": "done", "count": 1}
    assert (roots.codex_home() / "history.jsonl").read_bytes() == line("session_id", KEPT, ts=2, text="kept")
    assert (roots.codex_home() / "session_index.jsonl").read_bytes() == line("id", KEPT, thread_name="k")
    doc = logs()[0]
    assert doc["result"]["ids"]["history_lines"] == {GONE: 2}
    assert doc["result"]["ids"]["session_index"] == {GONE: 1}
    assert SECRET not in json.dumps(doc)


# One definition of "a line of an orphan": what the dry run counts is what the
# cleanup drops, and every other line stays.
ODD_LINES = [
    pytest.param(b"\xef\xbb\xbf" + line("session_id", GONE), id="utf8_bom"),
    pytest.param(b'{"session_id": "' + GONE.encode() + b'", "text": "\xc3\x28"}\n', id="not_utf8"),
    pytest.param(b'{"session_id": "' + GONE.encode() + b'", "text": "\xed\xa0\x80"}\n', id="encoded_surrogate"),
    pytest.param(line("session_id", GONE.upper()), id="upper_case_id"),
    pytest.param(line("session_id", GONE + "\n"), id="id_with_newline"),
    pytest.param(line("session_id", [GONE]), id="id_in_a_list"),
    pytest.param(line("other", GONE), id="other_key"),
    pytest.param(line("session_id", KEPT, text=GONE), id="id_only_mentioned"),
    pytest.param(json.dumps([{"session_id": GONE}]).encode() + b"\n", id="not_an_object"),
    pytest.param(b'{"session_id": "' + GONE.encode() + b'", ', id="cut_short"),
    pytest.param(json.dumps({"session_id": GONE}).encode("utf-16") + b"\n", id="utf16"),
]


@pytest.mark.parametrize("odd", ODD_LINES)
def test_a_line_the_dry_run_does_not_count_is_never_dropped(db, odd):
    data = line("session_id", GONE2) + odd + line("session_id", BASE)
    jsonl("history.jsonl", data)
    found = codex_cleanup.orphans(codex_keepset.keep_set())
    assert found.history_lines == {GONE2: 1}
    report = run(["history_lines"])
    assert by_id(report)["history_lines"]["lines"] == 1
    assert (roots.codex_home() / "history.jsonl").read_bytes() == odd + line("session_id", BASE)


def test_a_symlinked_history_file_fails_that_item_and_is_kept(db, tmp_path):
    full_home(db)
    target = tmp_path / "elsewhere.jsonl"
    target.write_bytes(line("session_id", GONE))
    (roots.codex_home() / "history.jsonl").unlink()
    (roots.codex_home() / "history.jsonl").symlink_to(target)
    report = run()
    failed = by_id(report)["history_lines"]
    assert (failed["outcome"], failed["count"]) == ("failed", 0) and failed["message"]
    assert target.read_bytes() == line("session_id", GONE)
    assert (roots.codex_home() / "history.jsonl").is_symlink()
    assert by_id(report)["session_index"]["outcome"] == "done"


# The queue


def test_queue_items_first_then_revisions(db):
    add_queue(db, GONE, 3)  # several items under one id
    add_queue(db, GONE2)
    add_queue(db, KEPT, 2)
    queue = db / "queue_1.sqlite"
    kept = {t: keyed(queue, t, KEPT) for t in ("queued_items", "queued_thread_revisions")}
    assert codex_cleanup.dry_run_payload()["items"][3]["count"] == 6
    report = run(["queue"])
    assert by_id(report)["queue"] == {"id": "queue", "outcome": "done", "count": 6}
    for table in ("queued_items", "queued_thread_revisions"):
        assert rows(queue, table) == kept[table]  # no revision left for an orphan
    assert sorted(logs()[0]["result"]["ids"]["queue"]) == [GONE, GONE2]


@pytest.mark.parametrize("table", ["queued_items", "queued_thread_revisions"])
def test_an_orphan_in_one_queue_table_only_is_deleted(db, table):
    add_queue(db, KEPT)
    if table == "queued_items":
        add_queue(db, GONE)
        queue_sql(db, "DELETE FROM queued_thread_revisions WHERE thread_id = ?", (GONE,))
    else:
        queue_sql(db, "INSERT INTO queued_thread_revisions (thread_id) VALUES (?)", (GONE,))
    queue = db / "queue_1.sqlite"
    kept = {t: rows(queue, t) for t in ("queued_items", "queued_thread_revisions")}
    kept[table] = keyed(queue, table, KEPT)
    assert codex_cleanup.dry_run_payload()["items"][3]["count"] == 1
    report = run(["queue"])
    assert by_id(report)["queue"]["count"] == 1
    assert {t: rows(queue, t) for t in ("queued_items", "queued_thread_revisions")} == kept


# Cache files, items, blockers mid-run, the whole run


def test_cache_files_of_orphans_go_and_kept_ones_stay(db):
    full_home(db)
    cache(GONE2).mkdir()  # a folder is not a cache file
    report = run()
    assert by_id(report)["cache_files"] == {"id": "cache_files", "outcome": "done", "count": 1}
    assert not cache(GONE).exists()
    assert cache(KEPT).exists() and cache(GONE2).is_dir()


def test_a_full_run_cleans_every_store_and_reports_in_order(db):
    full_home(db)
    report = run()
    assert outcomes(report) == [(i, "done", None) for i in ORDER]
    assert [r["count"] for r in report["items"]] == [1, 1, 1, 3, 1]
    assert (report["vacuum"], report["stopped"]) == (None, False)
    assert codex_cleanup.dry_run_payload()["items"] == [
        {"id": i, "label": label, "count": 0, **extra, "checked": False}
        for i, label, extra in [
            ("history_rows", "대화 저장소에 남은 내용", {}),
            ("history_lines", "입력 기록에서 지운 세션의 줄", {"lines": 0}),
            ("session_index", "이름 목록에서 지운 세션", {}),
            ("queue", "큐 기록", {}),
        ]
    ]
    assert keyed(db / "thread_history_1.sqlite", "thread_turns", BASE) != []  # kept by history_base


def test_items_flag_limits_the_run(db, capsys):
    full_home(db)
    code = cli.main(["sessions", "cleanup", "codex", "--yes", "--items", "queue,history_lines", "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert outcomes(report) == [
        ("history_lines", "done", None), ("queue", "done", None), ("cache_files", "done", None),
    ]
    assert keyed(db / "thread_history_1.sqlite", "thread_turns", GONE) != []
    assert (roots.codex_home() / "session_index.jsonl").read_bytes().count(GONE.encode()) == 1
    assert not cache(GONE).exists()  # the cache files always run


def test_blocker_appearing_mid_run_stops_the_rest(db, monkeypatch):
    full_home(db)
    calls = []

    def detect():
        calls.append(1)
        return {"blockers": [BLOCKER] if len(calls) >= 3 else []}  # start, item 1, item 2

    monkeypatch.setattr(codex_blockers, "detect", detect)
    before = (roots.codex_home() / "history.jsonl").read_bytes()
    report = run()
    assert outcomes(report) == [("history_rows", "done", None)] + [
        (i, "skipped", "codex_started") for i in ORDER[1:]
    ]
    assert report["stopped"] is False
    assert len(calls) == 3
    assert (roots.codex_home() / "history.jsonl").read_bytes() == before
    assert keyed(db / "queue_1.sqlite", "queued_items", GONE) != [] and cache(GONE).exists()


def test_sigterm_between_items_stops_and_logs(db, monkeypatch):
    full_home(db)
    real = codex_cleanup.rewrite_jsonl

    def then_sigterm(path, drop):
        dropped = real(path, drop)
        os.kill(os.getpid(), signal.SIGTERM)
        return dropped

    monkeypatch.setattr(codex_cleanup, "rewrite_jsonl", then_sigterm)
    with StopSignal() as stop:
        report = run(stop=stop)
    assert report["stopped"] is True
    assert outcomes(report) == [
        ("history_rows", "done", None), ("history_lines", "done", None),
    ] + [(i, "skipped", "stopped") for i in ORDER[2:]]
    assert (roots.codex_home() / "session_index.jsonl").read_bytes().count(GONE.encode()) == 1
    [doc] = logs()
    assert doc["result"]["stopped"] is True
    assert doc["result"]["items"] == report["items"]
    assert "error" not in doc["result"]


def test_an_item_that_raises_is_failed_and_the_next_runs(db, monkeypatch):
    full_home(db)

    def broken(path, drop):
        raise OSError(f"cannot write {path.name}")

    monkeypatch.setattr(codex_cleanup, "rewrite_jsonl", broken)
    report = run()
    assert by_id(report)["history_lines"] == {
        "id": "history_lines", "outcome": "failed", "count": 0, "message": "cannot write history.jsonl",
    }
    assert [by_id(report)[i]["outcome"] for i in ("queue", "cache_files")] == ["done", "done"]


def test_yes_json_is_one_object_with_progress_on_stderr(db, capsys):
    full_home(db)
    assert cli.main(["sessions", "cleanup", "codex", "--yes", "--json"]) == 0
    out, err = capsys.readouterr()
    assert out.count("\n") == 1
    report = json.loads(out)
    assert [r["id"] for r in report["items"]] == ORDER
    assert err.splitlines() == ["dotsync-progress 1/2", "dotsync-progress 2/2", "dotsync-progress 1/1"]


def test_yes_text_prints_one_line_per_item(db, capsys):
    full_home(db)
    assert cli.main(["sessions", "cleanup", "codex", "--yes"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "history_rows  done  1",
        "history_lines  done  1",
        "session_index  done  1",
        "queue  done  3",
        "cache_files  done  1",
    ]
