"""`reclaim`: rewrites each database whole (`VACUUM`) so all its free pages
go back to the disk, and stops cleanly. The real-DB tests build their own
small WAL databases in tmp."""

import contextlib
import sqlite3

import pytest

from dotsync.sessions import codex_cleanup
from dotsync.sessions.stop import StopSignal

NO_BLOCKERS = {"blockers": []}
BLOCKED = {"blockers": [{"kind": "terminal_codex"}]}


def fresh_db(path, rows=400):
    """A WAL database with incremental auto_vacuum, as Codex makes them,
    whose rows were written and deleted again: their pages are free."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA auto_vacuum = 2")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body BLOB)")
    con.executemany("INSERT INTO t (body) VALUES (?)", [(b"x" * 4000,)] * rows)
    con.commit()
    con.execute("DELETE FROM t WHERE id > 10")
    con.commit()
    con.close()


def pragma(path, name):
    con = sqlite3.connect(path)
    try:
        return con.execute(f"PRAGMA {name}").fetchone()[0]
    finally:
        con.close()


def reclaim(paths, **kwargs):
    kwargs.setdefault("stop", StopSignal())
    kwargs.setdefault("detect", lambda: NO_BLOCKERS)
    return codex_cleanup.reclaim(paths, **kwargs)


@pytest.fixture
def two(tmp_path):
    paths = [tmp_path / "h.sqlite", tmp_path / "s.sqlite"]
    for p in paths:
        fresh_db(p)
    return paths


@pytest.fixture
def statements(monkeypatch):
    """Every statement the reclaim's connections run."""
    seen: list[str] = []
    real = codex_cleanup._writing

    @contextlib.contextmanager
    def spying(path):
        with real(path) as con:
            con.set_trace_callback(seen.append)
            yield con

    monkeypatch.setattr(codex_cleanup, "_writing", spying)
    return seen


def test_vacuum_gives_every_free_page_of_every_database_back(two):
    freed: list[tuple[int, int]] = []
    result = reclaim(two, progress=lambda a, b: freed.append((a, b)))
    assert result["stopped_because"] == "freelist_empty"
    assert result["bytes_after"] < result["bytes_before"]
    assert freed[-1][0] == freed[-1][1] > 0
    for p in two:
        assert pragma(p, "freelist_count") == 0
        assert pragma(p, "integrity_check") == "ok"
        assert pragma(p, "auto_vacuum") == 2  # Codex's own reclaimer needs it


def test_the_rows_survive(two):
    reclaim(two)
    con = sqlite3.connect(two[0])
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 10


def test_bytes_are_the_databases_and_their_wal_summed(two):
    def weight(p):
        wal = p.with_name(p.name + "-wal")
        return p.stat().st_size + (wal.stat().st_size if wal.exists() else 0)

    before = sum(weight(p) for p in two)
    result = reclaim(two)
    assert (result["bytes_before"], result["bytes_after"]) == (before, sum(weight(p) for p in two))


def test_a_database_with_nothing_free_is_not_rewritten(tmp_path, statements):
    db = tmp_path / "h.sqlite"
    fresh_db(db, rows=0)
    assert reclaim([db])["stopped_because"] == "freelist_empty"
    assert "VACUUM" not in statements


def test_only_the_allowed_statements_run(two, statements):
    reclaim(two)
    assert set(statements) == {"PRAGMA wal_checkpoint(TRUNCATE)", "PRAGMA freelist_count", "VACUUM"}


class Requested:
    """A StopSignal whose `requested` turns true after `after` reads: SIGTERM
    arriving while the vacuum runs."""

    def __init__(self, after):
        self.reads = 0
        self.after = after

    @property
    def requested(self):
        self.reads += 1
        return self.reads > self.after


def test_sigterm_during_the_vacuum_interrupts_it_and_leaves_the_database_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_cleanup, "VACUUM_STOP_CHECK", 1)  # this small VACUUM takes few steps
    db = tmp_path / "h.sqlite"
    fresh_db(db, rows=4000)
    free = pragma(db, "freelist_count")
    result = reclaim([db], stop=Requested(after=1))  # the check before it passes
    assert result["stopped_because"] == "stopped"
    assert pragma(db, "freelist_count") == free
    assert pragma(db, "integrity_check") == "ok"


def test_sigterm_before_the_vacuum_runs_none(two):
    stop = StopSignal()
    stop.requested = True
    result = reclaim(two, stop=stop)
    assert result["stopped_because"] == "stopped"
    assert all(pragma(p, "freelist_count") > 0 for p in two)


def test_a_blocker_before_the_first_database_runs_none(two):
    result = reclaim(two, detect=lambda: BLOCKED)
    assert result["stopped_because"] == "blocker"
    assert all(pragma(p, "freelist_count") > 0 for p in two)


def test_a_blocker_that_appears_after_the_first_database_stops_there(two):
    answers = iter([NO_BLOCKERS, BLOCKED])
    result = reclaim(two, detect=lambda: next(answers, BLOCKED))
    assert result["stopped_because"] == "blocker"
    assert pragma(two[0], "freelist_count") == 0
    assert pragma(two[1], "freelist_count") > 0


def test_a_busy_checkpoint_runs_no_vacuum(two, monkeypatch):
    monkeypatch.setattr(codex_cleanup, "_checkpointed", lambda con: False)
    result = reclaim(two)
    assert result["stopped_because"] == "busy"
    assert all(pragma(p, "freelist_count") > 0 for p in two)


def test_a_locked_database_is_busy(monkeypatch, tmp_path):
    @contextlib.contextmanager
    def writing(_db):
        raise sqlite3.OperationalError("database is locked")
        yield

    monkeypatch.setattr(codex_cleanup, "_writing", writing)
    assert reclaim([tmp_path / "x"])["stopped_because"] == "busy"


def test_an_unopenable_database_is_an_error_not_a_raise(tmp_path):
    result = reclaim([tmp_path / "missing.sqlite"])
    assert result["stopped_because"] == "error"
    assert "\n" not in result["message"] and result["message"]
    assert result["bytes_before"] == result["bytes_after"] == 0
