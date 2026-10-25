"""`reclaim`: gives the history database's free pages back in batches, and
stops cleanly. The real-DB test builds its own small WAL database in tmp."""

import contextlib
import sqlite3

import pytest

from dotsync.sessions import codex_cleanup
from dotsync.sessions.stop import StopSignal

NO_BLOCKERS = {"blockers": []}


def fresh_db(path, rows=400):
    con = sqlite3.connect(path)
    con.execute("PRAGMA auto_vacuum = 2")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body BLOB)")
    con.executemany("INSERT INTO t (body) VALUES (?)", [(b"x" * 4000,)] * rows)
    con.commit()
    con.execute("DELETE FROM t")
    con.commit()
    con.close()


def reclaim(path, **kwargs):
    kwargs.setdefault("stop", StopSignal())
    kwargs.setdefault("detect", lambda: NO_BLOCKERS)
    return codex_cleanup.reclaim(path, **kwargs)


def test_vacuum_reclaims_until_freelist_empty(tmp_path):
    db = tmp_path / "h.sqlite"
    fresh_db(db)
    freed: list[tuple[int, int]] = []
    result = reclaim(db, batch_pages=100, progress=lambda a, b: freed.append((a, b)))
    assert result["stopped_because"] == "freelist_empty"
    assert result["bytes_after"] < result["bytes_before"]
    assert len(freed) > 1 and freed[-1][0] == freed[-1][1]
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class StubConnection:
    """Answers the PRAGMAs; `freelist` is read in order, the last one repeats."""

    def __init__(self, freelist, checkpoints=((0, 0, 0),)):
        self.freelist = list(freelist)
        self.checkpoints = list(checkpoints)
        self.calls: list[str] = []

    def execute(self, sql):
        self.calls.append(sql)
        if sql == "PRAGMA freelist_count":
            return Result([(self.freelist.pop(0) if len(self.freelist) > 1 else self.freelist[0],)])
        if sql.startswith("PRAGMA wal_checkpoint"):
            return Result([self.checkpoints.pop(0) if len(self.checkpoints) > 1 else self.checkpoints[0]])
        return Result([])


@pytest.fixture
def stub(monkeypatch, tmp_path):
    def use(con):
        @contextlib.contextmanager
        def writing(_db):
            yield con

        monkeypatch.setattr(codex_cleanup, "_writing", writing)
        return tmp_path / "missing.sqlite"

    return use


def test_stops_on_no_progress(stub):
    con = StubConnection([50, 50])
    assert reclaim(stub(con))["stopped_because"] == "no_progress"


def test_stops_on_busy_checkpoint_before_any_batch(stub):
    con = StubConnection([50], checkpoints=[(1, 0, 0)])
    assert reclaim(stub(con))["stopped_because"] == "busy"
    assert not any(c.startswith("PRAGMA incremental_vacuum") for c in con.calls)


def test_stops_on_busy_checkpoint_between_batches(stub):
    con = StubConnection([50, 40], checkpoints=[(0, 0, 0), (1, 0, 0)])
    assert reclaim(stub(con))["stopped_because"] == "busy"


def test_stops_at_time_cap(stub):
    clock = iter([0, 1000])
    con = StubConnection([50, 40, 30])
    result = reclaim(stub(con), now=lambda: next(clock), cap=600)
    assert result["stopped_because"] == "time_cap"
    assert sum(c.startswith("PRAGMA incremental_vacuum") for c in con.calls) == 1


def test_stops_when_a_blocker_appears(stub):
    con = StubConnection([50, 40, 30])
    blocked = {"blockers": [{"kind": "terminal_codex"}]}
    assert reclaim(stub(con), detect=lambda: blocked)["stopped_because"] == "blocker"


def test_stops_on_sigterm_between_batches(stub):
    con = StubConnection([50, 40, 30])
    stop = StopSignal()
    stop.requested = True
    assert reclaim(stub(con), stop=stop)["stopped_because"] == "stopped"


def test_only_the_allowed_pragmas_run_and_batch_size_is_passed(stub):
    con = StubConnection([50, 0])
    reclaim(stub(con), batch_pages=7)
    assert set(con.calls) <= {
        "PRAGMA freelist_count",
        "PRAGMA incremental_vacuum(7)",
        "PRAGMA wal_checkpoint(TRUNCATE)",
    }


def test_a_locked_database_is_busy(monkeypatch, tmp_path):
    @contextlib.contextmanager
    def writing(_db):
        raise sqlite3.OperationalError("database is locked")
        yield

    monkeypatch.setattr(codex_cleanup, "_writing", writing)
    assert reclaim(tmp_path / "x")["stopped_because"] == "busy"


def test_an_unopenable_database_is_an_error_not_a_raise(tmp_path):
    result = reclaim(tmp_path / "missing.sqlite")
    assert result["stopped_because"] == "error"
    assert "\n" not in result["message"] and result["message"]
    assert result["bytes_before"] == result["bytes_after"] == 0


def test_a_blocker_before_the_first_batch_runs_no_batch(stub):
    con = StubConnection([50, 40])
    blocked = {"blockers": [{"kind": "terminal_codex"}]}
    result = reclaim(stub(con), detect=lambda: blocked)
    assert result["stopped_because"] == "blocker"
    assert not any(c.startswith("PRAGMA incremental_vacuum") for c in con.calls)
