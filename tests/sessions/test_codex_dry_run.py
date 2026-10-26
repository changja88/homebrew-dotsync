"""`cleanup codex --dry-run`: what a Codex cleanup would remove, counted
read-only. An id counted here is an id the real cleanup deletes, so anything
unclear (a store that cannot be read, an id that is not canonical, a line that
only mentions an id) must not be counted."""

import json
import os
import sqlite3

import pytest

from dotsync import cli
from dotsync.sessions import codex, codex_cleanup, codex_keepset, procs, roots
from tests.sessions.codex_fixtures import (
    add_history,
    add_thread,
    make_codex_home,
    write_rollout,
)

KEPT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
GONE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GONE2 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BASE = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
UPPER = "EEEEEEEE-EEEE-4EEE-8EEE-EEEEEEEEEEEE"


@pytest.fixture(autouse=True)
def nothing_runs(monkeypatch):
    monkeypatch.setattr(procs, "ps_table", lambda: {})
    monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: {})


@pytest.fixture
def home():
    db_dir = make_codex_home(roots.codex_home())
    add_thread(db_dir, KEPT)
    write_rollout(roots.codex_home(), KEPT)
    return db_dir


def add_queue(db_dir, thread_id):
    con = sqlite3.connect(db_dir / "queue_1.sqlite")
    con.execute(
        "INSERT INTO queued_items (id, thread_id, payload_json, queue_order, created_at_ms,"
        " updated_at_ms) VALUES (?, ?, '{}', 0, 0, 0)",
        (os.urandom(4).hex(), thread_id),
    )
    con.commit()
    con.close()


def jsonl(name, lines):
    (roots.codex_home() / name).write_text(
        "".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines)
    )


def items(payload):
    return {i["id"]: i for i in payload["items"]}


def test_counts_each_store(home):
    add_history(home, GONE)
    add_history(home, GONE2)
    add_history(home, KEPT)
    jsonl("history.jsonl", [{"session_id": GONE, "ts": 1, "text": "a"}] * 3 + [{"session_id": KEPT}])
    jsonl("session_index.jsonl", [{"id": GONE, "thread_name": "n"}, {"id": KEPT}])
    add_queue(home, GONE)
    add_queue(home, GONE2)
    cache = roots.codex_home() / codex.CACHE_DIR
    cache.mkdir()
    (cache / GONE).write_text("")
    payload = codex_cleanup.dry_run_payload()
    assert payload["refusal"] is None
    assert [i["id"] for i in payload["items"]] == [
        "history_rows",
        "history_lines",
        "session_index",
        "queue",
    ]
    found = items(payload)
    assert found["history_rows"]["count"] == 2
    assert found["history_rows"]["label"] == "대화 저장소에 남은 내용"
    assert (found["history_lines"]["count"], found["history_lines"]["lines"]) == (1, 3)
    assert found["session_index"]["count"] == 1
    assert found["queue"]["count"] == 4  # two items and their two revisions
    assert all(i["checked"] for i in payload["items"])
    assert payload["vacuum_available"] is True
    assert payload["history_db_bytes"] > 0
    assert codex_cleanup.orphans(codex_keepset.keep_set()).cache_ids == {GONE}


def test_empty_stores_give_unchecked_items(home):
    payload = codex_cleanup.dry_run_payload()
    assert [(i["count"], i["checked"]) for i in payload["items"]] == [(0, False)] * 4


def test_history_line_matches_session_id_exactly(home):
    jsonl(
        "history.jsonl",
        [
            {"session_id": GONE, "text": "x"},
            {"session_id": KEPT, "text": f"mentions {GONE}"},
            {"session_id": GONE + "x", "text": "x"},
            {"session_id": [GONE]},
            {"other": GONE},
            f'"{GONE}"',
        ],
    )
    found = codex_cleanup.orphans(codex_keepset.keep_set())
    assert found.history_lines == {GONE: 1}


def test_unparsable_lines_are_not_counted(home):
    jsonl(
        "history.jsonl",
        ['{"session_id": "' + GONE + '", "text": ', "not json " + GONE, "", "[1,2"],
    )
    (roots.codex_home() / "session_index.jsonl").write_bytes(b'\xff\xfe{"id":"' + GONE.encode() + b'"}\n')
    found = codex_cleanup.orphans(codex_keepset.keep_set())
    assert found.history_lines == {} and found.index_ids == set()


def test_kept_ids_are_not_orphans(home):
    # BASE is named only by a rollout's history_base.
    write_rollout(roots.codex_home(), GONE2, meta={"id": GONE2, "history_base": {"thread_id": BASE}})
    add_history(home, BASE)
    add_history(home, GONE2)
    jsonl("history.jsonl", [{"session_id": BASE}])
    jsonl("session_index.jsonl", [{"id": BASE}])
    add_queue(home, BASE)
    payload = codex_cleanup.dry_run_payload()
    assert payload["refusal"] is None
    assert [i["count"] for i in payload["items"]] == [0, 0, 0, 0]


def test_ids_that_are_not_canonical_are_ignored(home):
    add_history(home, UPPER)
    add_history(home, "not-an-id")
    add_queue(home, UPPER)
    jsonl("history.jsonl", [{"session_id": UPPER}])
    jsonl("session_index.jsonl", [{"id": UPPER}])
    cache = roots.codex_home() / codex.CACHE_DIR
    cache.mkdir()
    (cache / UPPER).write_text("")
    (cache / (GONE + ".lock")).write_text("")
    (cache / GONE2).mkdir()
    (cache / BASE).symlink_to(cache / UPPER)
    found = codex_cleanup.orphans(codex_keepset.keep_set())
    assert found == codex_cleanup.Orphans(set(), {}, set(), 0, set(), set())


def test_refusal_empties_items(home):
    add_history(home, GONE)
    (roots.codex_home() / "rollout-migrations").mkdir()
    (roots.codex_home() / "rollout-migrations" / f"{GONE}.pending").write_text("")
    payload = codex_cleanup.dry_run_payload()
    assert payload["items"] == []
    assert payload["refusal"]["code"] == "codex_version"
    assert payload["vacuum_available"] is False
    assert "blockers" in payload


def test_support_refusal_skips_the_keep_set_walk(home, monkeypatch):
    run = sqlite3.connect(home / "queue_1.sqlite")
    run.execute("DROP TABLE _sqlx_migrations")
    run.commit()
    run.close()
    monkeypatch.setattr(codex_keepset, "keep_set", lambda progress=None: pytest.fail("walked"))
    assert codex_cleanup.dry_run_payload()["refusal"]["code"] == "codex_version"


def test_unreadable_rollout_refuses_the_scan(home):
    (roots.codex_home() / "sessions" / "2026" / "01" / "02" / f"rollout-2026-01-02T03-04-05-{GONE}.jsonl").write_text("")
    payload = codex_cleanup.dry_run_payload()
    assert payload["items"] == [] and payload["refusal"]["code"] == "incomplete_scan"


@pytest.mark.parametrize("name", ["history.jsonl", "session_index.jsonl"])
def test_unreadable_jsonl_refuses(home, name):
    (roots.codex_home() / name).mkdir()  # a folder in the file's place
    payload = codex_cleanup.dry_run_payload()
    assert payload["items"] == []
    assert payload["refusal"]["code"] == "codex_db_failed"
    assert name in payload["refusal"]["message"]


def test_unreadable_history_db_refuses_instead_of_counting_zero(home, monkeypatch):
    add_history(home, GONE)
    real = codex_cleanup.orphans

    def broken(keep):
        monkeypatch.setattr(
            codex, "open_ro", lambda p: (_ for _ in ()).throw(sqlite3.DatabaseError("disk I/O"))
        )
        return real(keep)

    monkeypatch.setattr(codex_cleanup, "orphans", broken)
    payload = codex_cleanup.dry_run_payload()
    assert payload["items"] == []
    assert payload["refusal"]["code"] == "codex_db_failed"


def free_pages(db_dir):
    """Rows written and deleted again: with incremental auto_vacuum their
    pages stay on the freelist until a vacuum gives them back."""
    con = sqlite3.connect(db_dir / "thread_history_1.sqlite")
    con.executemany(
        "INSERT INTO thread_items (thread_id, turn_id, item_id, rollout_ordinal, created_at_ms, item_json)"
        " VALUES (?, 't', ?, ?, 0, ?)",
        [(GONE, f"i{n}", n, "x" * 4000) for n in range(40)],
    )
    con.commit()
    con.execute("DELETE FROM thread_items")
    con.commit()
    (pages,) = con.execute("PRAGMA freelist_count").fetchone()
    (size,) = con.execute("PRAGMA page_size").fetchone()
    con.close()
    return pages * size


def test_vacuum_free_bytes_is_what_the_history_db_could_give_back(home):
    freed = free_pages(home)
    assert freed > 0
    assert codex_cleanup.dry_run_payload()["vacuum_free_bytes"] == freed


def test_vacuum_free_bytes_reads_two_pragmas_read_only(home, monkeypatch):
    free_pages(home)
    real = codex.open_ro
    opened, statements = [], []

    def spying(path):
        con = real(path)
        opened.append(path)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(codex, "open_ro", spying)
    assert codex_cleanup.vacuum_free_bytes() > 0
    assert opened == [home / "thread_history_1.sqlite"]
    assert statements == ["PRAGMA freelist_count", "PRAGMA page_size"]


def test_vacuum_free_bytes_is_null_when_the_history_db_cannot_be_read(home):
    (home / "thread_history_1.sqlite").unlink()
    payload = codex_cleanup.dry_run_payload()
    assert payload["vacuum_free_bytes"] is None
    assert not (home / "thread_history_1.sqlite").exists()  # never made


def test_blockers_are_reported_but_do_not_refuse(home, monkeypatch):
    monkeypatch.setattr(procs, "ps_table", lambda: {500: procs.Proc(500, 1, 60, "/x/codex")})
    monkeypatch.setattr(
        procs, "lsof_scan", lambda fds=None: {500: procs.OpenFiles("codex", ["/x/codex"], [])}
    )
    add_history(home, GONE)
    payload = codex_cleanup.dry_run_payload()
    assert payload["refusal"] is None
    assert payload["blockers"][0]["pid"] == 500
    assert items(payload)["history_rows"]["count"] == 1


def test_dry_run_json_is_one_object_with_progress_on_stderr(home, capsys):
    add_history(home, GONE)
    assert cli.main(["sessions", "cleanup", "codex", "--dry-run", "--json"]) == 0
    out, err = capsys.readouterr()
    assert items(json.loads(out))["history_rows"]["count"] == 1
    assert err.splitlines() == ["dotsync-progress 1/1 read"]


def test_dry_run_refusal_json(home, capsys):
    (roots.codex_home() / "history.jsonl").mkdir()
    assert cli.main(["sessions", "cleanup", "codex", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"] == [] and payload["refusal"]["code"] == "codex_db_failed"


def test_dry_run_text_prints_counts_only(home, capsys):
    add_history(home, GONE)
    assert cli.main(["sessions", "cleanup", "codex", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "대화 저장소에 남은 내용  1" in out and GONE not in out
