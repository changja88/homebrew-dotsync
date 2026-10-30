"""Deleting Codex sessions through a fake `codex delete` that edits the fake
home the way Codex does. No real codex binary ever runs."""

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from dotsync import cli, locks
from dotsync.sessions import codex, procs, roots
from dotsync.sessions.stop import StopSignal
from tests.sessions.codex_fixtures import (
    HISTORY_TABLES,
    add_edge,
    add_history,
    add_thread,
    make_codex_home,
    write_rollout,
)

ROOT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CHILD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GRANDCHILD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
THIRD = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OTHER = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
REVERTED = "ffffffff-ffff-4fff-8fff-ffffffffffff"
FAMILY = (ROOT, CHILD, GRANDCHILD)
CODEX = "/fake/bin/codex"
GENERIC = "Error: failed to delete session\n"
TITLE = "secret-title-xyz"
SPAWN = json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": ROOT}}})
GUARDIAN_SOURCE = json.dumps({"subagent": {"other": "guardian"}})
_NAME = re.compile(r"^rollout-.{19}-([0-9a-f-]{36})(?:_([0-9a-f-]{36}))?\.jsonl(?:\.zst)?$")


@pytest.fixture
def db():
    return make_codex_home(roots.codex_home())


class Lsof:
    """lsof_scan answers `answers[n]` on call n (the last one repeats); an
    exception in the list is raised instead."""

    def __init__(self, monkeypatch):
        self.answers: list = [{}]
        self.calls = 0
        monkeypatch.setattr(procs, "lsof_scan", self._scan)

    def _scan(self, fds=None):
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def lsof(monkeypatch):
    return Lsof(monkeypatch)


def held(*thread_ids, pid=4242):
    lock_dir = roots.codex_home() / "thread-writer-locks"
    files = [str(lock_dir / f"{t}.lock") for t in thread_ids]
    return {pid: procs.OpenFiles(command="codex", files=files)}


class FakeCodex:
    """`codex --no-daemon delete <id> --force` as procs.run sees it. It
    removes what Codex removes for the thread and its spawn subtree, in
    Codex's order (history rows keyed by rollout id, rollout files, state
    rows children first), limited to `parts`; then runs `then`, which may
    raise; then exits with `answer` (exit code, stderr)."""

    def __init__(self, monkeypatch, db):
        self.db = db
        self.parts = {"history", "rollout", "state"}
        self.answer = (0, "")
        self.then = None
        self.calls: list[tuple[list[str], dict]] = []
        monkeypatch.setattr(codex, "codex_binary", lambda: CODEX)
        monkeypatch.setattr(procs, "run", self)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        ids = subtree(self.db, argv[3])
        files = [p for p in rollouts_on_disk() if _NAME.match(p.name).group(1) in ids]
        if "history" in self.parts:
            keys = set(ids) | {rollout_id_of(p) for p in files}
            con = sqlite3.connect(self.db / "thread_history_1.sqlite")
            for table in HISTORY_TABLES:
                con.executemany(f"DELETE FROM {table} WHERE thread_id = ?", [(k,) for k in keys])
            con.commit()
            con.close()
        if "rollout" in self.parts:
            for p in files:
                p.unlink()
        if "state" in self.parts:
            con = sqlite3.connect(self.db / "state_5.sqlite")
            for tid in reversed(ids):
                con.execute(
                    "DELETE FROM thread_spawn_edges WHERE child_thread_id = ? OR parent_thread_id = ?",
                    (tid, tid),
                )
                con.execute("DELETE FROM threads WHERE id = ?", (tid,))
            con.commit()
            con.close()
        if self.then:
            self.then()
        code, stderr = self.answer
        stdout = f"Deleted session {argv[3]}.\n" if code == 0 else ""
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    def deleted_ids(self) -> list[str]:
        return [argv[3] for argv, _ in self.calls]


@pytest.fixture
def fake(monkeypatch, db, lsof):
    return FakeCodex(monkeypatch, db)


def subtree(db, thread_id) -> list[str]:
    con = sqlite3.connect(db / "state_5.sqlite")
    ids, queue = [thread_id], [thread_id]
    while queue:
        rows = con.execute(
            "SELECT child_thread_id FROM thread_spawn_edges WHERE parent_thread_id = ?", (queue.pop(),)
        )
        for (child,) in rows:
            ids.append(child)
            queue.append(child)
    con.close()
    return ids


def rollouts_on_disk() -> list[Path]:
    return sorted(p for p in roots.codex_home().rglob("rollout-*") if _NAME.match(p.name))


def rollout_id_of(path: Path) -> str:
    m = _NAME.match(path.name)
    return m.group(2) or m.group(1)


def cache(thread_id) -> Path:
    return roots.codex_home() / "tui-thread-reference-capabilities" / thread_id


def touch_cache(thread_id) -> Path:
    path = cache(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def family(db) -> dict[str, Path]:
    """ROOT, its CHILD and its GRANDCHILD, each with a rollout file, history
    rows and a reference-cache file. Returns their rollout files."""
    add_thread(db, ROOT, name=TITLE)
    add_thread(db, CHILD, source=SPAWN)
    add_thread(db, GRANDCHILD, source=SPAWN)
    add_edge(db, ROOT, CHILD)
    add_edge(db, CHILD, GRANDCHILD)
    rollouts = {}
    for tid in FAMILY:
        rollouts[tid] = write_rollout(roots.codex_home(), tid)
        add_history(db, tid)
        touch_cache(tid)
    return rollouts


def single(db, thread_id) -> Path:
    add_thread(db, thread_id)
    add_history(db, thread_id)
    touch_cache(thread_id)
    return write_rollout(roots.codex_home(), thread_id)


def left(db) -> dict:
    """The fake home as a test sees it, read without dotsync's code."""
    state = sqlite3.connect(db / "state_5.sqlite")
    history = sqlite3.connect(db / "thread_history_1.sqlite")
    try:
        return {
            "rollouts": [str(p) for p in rollouts_on_disk()],
            "caches": sorted(p.name for p in cache(ROOT).parent.glob("*")),
            "threads": sorted(r[0] for r in state.execute("SELECT id FROM threads")),
            "edges": sorted(state.execute("SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges")),
            "history": {t: history.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in HISTORY_TABLES},
        }
    finally:
        state.close()
        history.close()


def snapshot(top: Path) -> dict[str, str]:
    """Every entry below `top`: a content hash for files, "dir" for folders."""
    out: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(top):
        for name in dirs:
            out[os.path.relpath(os.path.join(dirpath, name), top)] = "dir"
        for name in files:
            path = os.path.join(dirpath, name)
            out[os.path.relpath(path, top)] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return out


def timeout():
    raise procs.ToolFailure("timeout", f"{CODEX} timed out") from subprocess.TimeoutExpired(CODEX, 180)


def run_delete(*ids):
    return codex.delete(list(ids), stop=StopSignal(), progress=lambda done, total: None)


def sizes(paths) -> int:
    return sum(p.stat().st_size for p in paths)


# What Codex is asked to do


def test_argv_is_exact(fake, db):
    family(db)
    run_delete(ROOT)
    [(argv, kwargs)] = fake.calls
    assert argv == [CODEX, "--no-daemon", "delete", ROOT, "--force"]
    assert kwargs == {"timeout": 180, "cwd": Path.home()}


def test_codex_binary_prefers_homebrew_then_path(monkeypatch):
    homebrew = "/opt/homebrew/bin/codex"
    monkeypatch.setattr(shutil, "which", lambda name: f"/elsewhere/bin/{name}")
    monkeypatch.setattr(codex.os, "access", lambda path, mode: path == homebrew)
    assert codex.codex_binary() == homebrew
    monkeypatch.setattr(codex.os, "access", lambda path, mode: False)
    assert codex.codex_binary() == "/elsewhere/bin/codex"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert codex.codex_binary() is None


# Exit 0


def test_exit0_removes_reference_cache_for_root_and_children(fake, db):
    rollouts = family(db)
    images = roots.codex_home() / "generated_images" / ROOT
    images.mkdir(parents=True)
    (images / "a.png").write_bytes(b"png")
    touch_cache(OTHER)
    freed = sizes(rollouts.values())

    [result] = run_delete(ROOT)["results"]

    assert result == {"id": ROOT, "outcome": "deleted", "children": 2, "freed_bytes": freed}
    assert left(db)["caches"] == [OTHER]
    assert (images / "a.png").read_bytes() == b"png"


def test_exit0_with_state_left_is_partial_and_keeps_the_caches(fake, db):
    freed = sizes(family(db).values())
    fake.parts = {"history", "rollout"}

    [result] = run_delete(ROOT)["results"]

    assert result == {
        "id": ROOT, "outcome": "partial", "reason": "failed", "children": 2,
        "freed_bytes": freed, "remaining": ["state"],
    }
    assert left(db)["caches"] == sorted(FAMILY)


def test_repeated_id_runs_codex_once(fake, db):
    family(db)
    report = run_delete(ROOT, ROOT)
    assert [(r["id"], r["outcome"]) for r in report["results"]] == [(ROOT, "deleted")]
    assert fake.deleted_ids() == [ROOT]


# Refusals: Codex changed nothing


def test_refusal_with_lock_held_is_in_use(fake, db, lsof):
    family(db)
    fake.parts = set()
    fake.answer = (1, GENERIC)
    lsof.answers = [{}, held(CHILD)]  # opened after the batch scan, before Codex's own check
    before = left(db)

    [result] = run_delete(ROOT)["results"]

    assert result == {"id": ROOT, "outcome": "skipped", "reason": "in_use", "children": 2, "freed_bytes": 0}
    assert left(db) == before
    assert lsof.calls == 2


def test_refusal_with_fork_wording_is_referenced(fake, db):
    family(db)
    fake.parts = set()
    fake.answer = (1, f"Error: cannot delete thread {ROOT}: forked history still references it\n")
    before = left(db)

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"]) == ("skipped", "referenced")
    assert left(db) == before


@pytest.mark.parametrize("wording", ["already has an active writer", "already has a live local writer"])
def test_refusal_with_writer_wording_is_in_use(fake, db, wording):
    family(db)
    fake.parts = set()
    fake.answer = (1, f"Error: thread {ROOT} {wording}\n")

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"]) == ("skipped", "in_use")


def test_refusal_without_cause_is_refused(fake, db):
    family(db)
    fake.parts = set()
    fake.answer = (1, "WARN something else\n" + GENERIC + "\n")
    before = left(db)

    [result] = run_delete(ROOT)["results"]

    assert result == {
        "id": ROOT, "outcome": "skipped", "reason": "refused", "children": 2, "freed_bytes": 0,
        "message": "Error: failed to delete session",
    }
    assert left(db) == before


def test_timeout_with_nothing_changed_is_refused(fake, db):
    family(db)
    fake.parts = set()
    fake.then = timeout

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"]) == ("skipped", "refused")
    assert result["message"] == f"{CODEX} timed out"


# Partial: Codex stopped halfway


def test_timeout_after_partial_work_is_partial(fake, db):
    freed = sizes(family(db).values())
    fake.parts = {"rollout"}
    fake.then = timeout

    [result] = run_delete(ROOT)["results"]

    assert result == {
        "id": ROOT, "outcome": "partial", "reason": "timeout", "children": 2,
        "freed_bytes": freed, "remaining": ["state", "history"],
    }
    assert left(db)["caches"] == sorted(FAMILY)  # Codex still has the threads
    assert fake.deleted_ids() == [ROOT]  # it already took its 180 s: no second try


def test_only_history_left_is_no_rollout(fake, db):
    family(db)
    fake.parts = {"rollout", "state"}
    fake.answer = (1, GENERIC)

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"], result["remaining"]) == ("partial", "no_rollout", ["history"])
    # Codex no longer has the threads, and only this run still knows the children.
    assert left(db)["caches"] == []
    assert "failed" not in result


def test_retry_with_only_history_left_is_no_rollout_again(fake, db):
    family(db)
    fake.parts = {"rollout", "state"}
    fake.answer = (1, GENERIC)
    run_delete(ROOT)
    fake.parts = set()  # "no rollout found": Codex refuses and changes nothing

    [result] = run_delete(ROOT)["results"]

    assert result == {
        "id": ROOT, "outcome": "partial", "reason": "no_rollout", "children": 0,
        "freed_bytes": 0, "remaining": ["history"],
    }
    assert fake.deleted_ids() == [ROOT, ROOT]


def test_history_keyed_by_a_rollout_id_is_found(fake, db):
    add_thread(db, ROOT)
    write_rollout(roots.codex_home(), ROOT, rollout_id=REVERTED)
    add_history(db, REVERTED)  # keyed by the rollout id, not the thread id
    fake.parts = {"rollout", "state"}

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"], result["remaining"]) == ("partial", "no_rollout", ["history"])


def remove_one_of_two_rollouts(db):
    add_thread(db, ROOT)
    first = write_rollout(roots.codex_home(), ROOT)
    write_rollout(roots.codex_home(), ROOT, archived=True)
    return first.unlink, ["rollout", "state"]


def remove_the_child_row(db):
    family(db)

    def work():
        con = sqlite3.connect(db / "state_5.sqlite")
        con.execute("DELETE FROM threads WHERE id = ?", (GRANDCHILD,))
        con.commit()
        con.close()

    return work, ["rollout", "state", "history"]


@pytest.mark.parametrize("setup", [remove_one_of_two_rollouts, remove_the_child_row])
def test_a_change_inside_a_part_is_partial_not_unchanged(fake, db, setup):
    work, remaining = setup(db)
    fake.parts = set()
    fake.answer = (1, GENERIC)
    tried = []

    def first_call_only():
        if not tried:
            tried.append(True)
            work()

    fake.then = first_call_only

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"], result["remaining"]) == ("partial", "failed", remaining)


# What dotsync cannot check is never reported as done or unchanged


def test_unreadable_history_db_after_the_call_is_partial_failed(fake, db):
    family(db)
    fake.then = lambda: (db / "thread_history_1.sqlite").chmod(0)

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"]) == ("partial", "failed")
    assert (result["remaining"], result["unchecked"]) == (["history"], ["history"])
    assert fake.deleted_ids() == [ROOT]  # what is left is unknown: no second try


def test_unreadable_db_before_the_call_skips_without_calling_codex(fake, db):
    family(db)
    (db / "thread_history_1.sqlite").chmod(0)

    [result] = run_delete(ROOT)["results"]

    assert (result["outcome"], result["reason"]) == ("skipped", "inspect_failed")
    assert fake.calls == []


# Skipped before Codex runs


def test_lock_held_before_the_call_skips_without_calling_codex(fake, db, lsof):
    family(db)
    single(db, OTHER)
    lsof.answers = [held(GRANDCHILD)]

    report = run_delete(ROOT, OTHER)

    assert [(r["id"], r["outcome"], r.get("reason")) for r in report["results"]] == [
        (ROOT, "skipped", "in_use"), (OTHER, "deleted", None),
    ]
    assert fake.deleted_ids() == [OTHER]
    assert lsof.calls == 1  # one scan for the batch
    assert set(left(db)["caches"]) == set(FAMILY)


def test_own_lock_does_not_count_as_in_use(fake, db, lsof):
    family(db)
    lsof.answers = [held(ROOT, pid=os.getpid())]
    [result] = run_delete(ROOT)["results"]
    assert result["outcome"] == "deleted"


@pytest.mark.parametrize(
    "error", [procs.ToolFailure("timeout"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")]
)
def test_scan_failure_skips_every_root_without_calling_codex(fake, db, lsof, error):
    family(db)
    single(db, OTHER)
    lsof.answers = [error]

    report = run_delete(ROOT, OTHER)

    assert {(r["outcome"], r["reason"]) for r in report["results"]} == {("skipped", "scan_failed")}
    assert fake.calls == []


def test_codex_missing_skips_every_root(fake, db, monkeypatch):
    family(db)
    monkeypatch.setattr(codex, "codex_binary", lambda: None)

    [result] = run_delete(ROOT)["results"]

    assert result == {"id": ROOT, "outcome": "skipped", "reason": "codex_missing", "children": 2, "freed_bytes": 0}
    assert fake.calls == []


def test_nothing_left_removes_leftover_cache_without_calling_codex(fake, db):
    touch_cache(ROOT)
    touch_cache(OTHER)

    [result] = run_delete(ROOT)["results"]

    assert result == {"id": ROOT, "outcome": "deleted", "children": 0, "freed_bytes": 0}
    assert left(db)["caches"] == [OTHER]
    assert fake.calls == []


def test_unknown_id_is_not_found_without_calling_codex(fake, db):
    family(db)
    before = left(db)

    [result] = run_delete(OTHER)["results"]

    assert result == {"id": OTHER, "outcome": "skipped", "reason": "not_found", "children": 0, "freed_bytes": 0}
    assert fake.calls == []
    assert left(db) == before


def test_invalid_descendant_skips_without_calling_codex(fake, db):
    add_thread(db, ROOT)
    add_edge(db, ROOT, "../x")
    write_rollout(roots.codex_home(), ROOT)

    [result] = run_delete(ROOT)["results"]

    assert result == {"id": ROOT, "outcome": "skipped", "reason": "invalid_id", "children": 1, "freed_bytes": 0}
    assert fake.calls == []


def test_a_child_id_is_not_a_root_and_is_not_deleted(fake, db):
    family(db)
    before = left(db)

    [result] = run_delete(CHILD)["results"]

    assert result == {"id": CHILD, "outcome": "skipped", "reason": "not_root", "children": 0, "freed_bytes": 0}
    assert fake.calls == []
    assert left(db) == before


def test_a_sub_agent_that_no_thread_spawned_is_deleted(fake, db):
    single(db, THIRD)
    sqlite3.connect(db / "state_5.sqlite").execute(
        "UPDATE threads SET source = ? WHERE id = ?", (GUARDIAN_SOURCE, THIRD)
    ).connection.commit()

    [result] = run_delete(THIRD)["results"]

    assert result["outcome"] == "deleted"
    assert [argv for argv, _ in fake.calls] == [[CODEX, "--no-daemon", "delete", THIRD, "--force"]]
    assert left(db)["threads"] == []


def test_rollout_left_without_a_state_row_runs_codex_again(fake, db):
    rollout = write_rollout(roots.codex_home(), ROOT)  # a run that stopped after the state rows
    touch_cache(ROOT)
    size = rollout.stat().st_size

    [result] = run_delete(ROOT)["results"]

    assert result == {"id": ROOT, "outcome": "deleted", "children": 0, "freed_bytes": size}
    assert fake.deleted_ids() == [ROOT]
    assert left(db)["caches"] == []


# The reference-cache files


def test_cache_removal_failure_is_partial_and_a_retry_finishes(fake, db):
    family(db)
    folder = cache(ROOT).parent
    folder.chmod(0o500)
    try:
        [first] = run_delete(ROOT)["results"]
    finally:
        folder.chmod(0o700)

    assert first["outcome"] == "partial" and "reason" not in first
    assert sorted(f["path"] for f in first["failed"]) == sorted(str(cache(t)) for t in FAMILY)

    [second] = run_delete(ROOT)["results"]

    assert second == {"id": ROOT, "outcome": "deleted", "children": 0, "freed_bytes": 0}
    assert not cache(ROOT).exists()
    assert len(fake.calls) == 1


def test_a_folder_in_a_cache_files_place_is_kept(fake, db):
    family(db)
    cache(CHILD).unlink()
    cache(CHILD).mkdir()
    (cache(CHILD) / "keep").write_text("k")

    [result] = run_delete(ROOT)["results"]

    assert result["outcome"] == "partial"
    assert [f["path"] for f in result["failed"]] == [str(cache(CHILD))]
    assert (cache(CHILD) / "keep").read_text() == "k"


# inspect


def test_inspect_reads_rollouts_state_and_history(db):
    add_thread(db, ROOT)
    add_history(db, REVERTED)
    write_rollout(roots.codex_home(), OTHER)

    found = codex.inspect([ROOT], {REVERTED})
    assert (found.rollouts, found.state, found.history) == (False, True, True)
    assert codex.inspect([ROOT], set()).history is False
    assert codex.inspect([OTHER], set()).rollouts is True
    assert codex.inspect([THIRD], set()).parts() == []


def test_inspect_without_databases_finds_nothing():
    roots.codex_home().mkdir(parents=True)
    found = codex.inspect([ROOT], {REVERTED})
    assert found.parts() == [] and found.unchecked == frozenset()


def test_inspect_does_not_write(db):
    family(db)
    databases = sorted(db.glob("*.sqlite"))
    before = {p: p.read_bytes() for p in databases}
    codex.inspect(list(FAMILY), set())
    assert {p: p.read_bytes() for p in databases} == before


def test_inspect_unreadable_rollout_folder_is_unchecked(db):
    add_thread(db, ROOT)
    folder = write_rollout(roots.codex_home(), ROOT).parent
    folder.chmod(0)
    try:
        found = codex.inspect([ROOT], set())
    finally:
        folder.chmod(0o700)
    assert found.unchecked == frozenset({"rollout"})
    assert found.parts() == ["rollout", "state"]


# Stop, lock, log, CLI


def many(db, count) -> list[str]:
    ids = [f"{n:08x}-1111-4111-8111-111111111111" for n in range(1, count + 1)]
    for tid in ids:
        single(db, tid)
    return ids


def test_deletes_run_at_the_same_time(fake, db):
    ids = many(db, codex.DELETE_WORKERS)
    # Every call waits here for the others: one at a time would break the barrier.
    fake.then = threading.Barrier(codex.DELETE_WORKERS, timeout=10).wait
    report = run_delete(*ids)
    assert [r["outcome"] for r in report["results"]] == ["deleted"] * len(ids)
    assert left(db)["threads"] == []


def test_no_more_than_the_limit_run_at_once(fake, db):
    ids = many(db, codex.DELETE_WORKERS * 2 + 1)
    guard = threading.Lock()
    running, most = [0], [0]

    def count_one():
        with guard:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.05)
        with guard:
            running[0] -= 1

    fake.then = count_one
    report = run_delete(*ids)
    assert len(report["results"]) == len(ids)
    assert most[0] <= codex.DELETE_WORKERS


def test_results_keep_the_given_order_whichever_finishes_first(fake, db, monkeypatch):
    single(db, ROOT)
    single(db, OTHER)
    other_finished = threading.Event()

    def run(argv, **kwargs):
        if argv[3] == ROOT:
            assert other_finished.wait(10)
        done = fake(argv, **kwargs)
        if argv[3] == OTHER:
            other_finished.set()
        return done

    monkeypatch.setattr(procs, "run", run)
    seen = []
    report = codex.delete(
        [ROOT, OTHER], stop=StopSignal(), progress=lambda done, total: seen.append((done, total))
    )
    assert [r["id"] for r in report["results"]] == [ROOT, OTHER]
    assert seen == [(1, 2), (2, 2)]


def test_sigterm_finishes_the_running_ids_and_starts_no_more(fake, db, monkeypatch):
    monkeypatch.setattr(codex, "DELETE_WORKERS", 2)
    first, second, *rest = ids = many(db, 4)
    # Both calls are running when the signal comes; neither is cut short.
    fake.then = threading.Barrier(
        2, action=lambda: os.kill(os.getpid(), signal.SIGTERM), timeout=10
    ).wait

    with StopSignal() as stop:
        report = codex.delete(ids, stop=stop, progress=lambda done, total: None)

    assert report["stopped"] is True
    assert [(r["id"], r["outcome"], r.get("reason")) for r in report["results"]] == [
        (first, "deleted", None),
        (second, "deleted", None),
        *((tid, "skipped", "stopped") for tid in rest),
    ]
    assert sorted(fake.deleted_ids()) == sorted([first, second])
    assert set(left(db)["threads"]) == set(rest)


def stopping_short_once(fake, monkeypatch, thread_id):
    """The first `codex delete` of `thread_id` removes the rollout and the
    history but not the state rows, and exits 1: what a delete that loses the
    database to another one running does. Every other call goes through."""
    tried = []

    def run(argv, **kwargs):
        first = argv[3] == thread_id and not tried
        if first:
            tried.append(thread_id)
        fake.parts = {"history", "rollout"} if first else {"history", "rollout", "state"}
        fake.answer = (1, GENERIC) if first else (0, "")
        return fake(argv, **kwargs)

    monkeypatch.setattr(procs, "run", run)


def test_a_delete_that_stopped_halfway_is_tried_again_after_the_others(fake, db, monkeypatch):
    monkeypatch.setattr(codex, "DELETE_WORKERS", 1)
    rollout = single(db, ROOT)
    size = rollout.stat().st_size
    single(db, OTHER)
    stopping_short_once(fake, monkeypatch, ROOT)
    seen = []

    report = codex.delete([ROOT, OTHER], stop=StopSignal(), progress=lambda d, t: seen.append((d, t)))

    assert fake.deleted_ids() == [ROOT, OTHER, ROOT]
    assert [(r["id"], r["outcome"]) for r in report["results"]] == [(ROOT, "deleted"), (OTHER, "deleted")]
    assert report["results"][0]["freed_bytes"] == size  # what the first try freed still counts
    assert seen == [(1, 2), (2, 2)]
    assert left(db)["threads"] == [] and left(db)["caches"] == []


def test_a_retry_keeps_the_first_tries_rollouts_in_the_log(fake, db, monkeypatch):
    rollout = single(db, ROOT)
    stopping_short_once(fake, monkeypatch, ROOT)
    run_delete(ROOT)
    [log_path] = roots.log_dir().iterdir()
    doc = json.loads(log_path.read_text())
    assert doc["result"]["paths"][ROOT]["rollouts"] == [str(rollout)]
    assert doc["result"]["paths"][ROOT]["removed"] == [str(cache(ROOT))]


def test_a_delete_that_stops_halfway_twice_stays_partial(fake, db):
    rollout = single(db, ROOT)
    size = rollout.stat().st_size
    fake.parts = {"history", "rollout"}
    fake.answer = (1, GENERIC)

    [result] = run_delete(ROOT)["results"]

    assert fake.deleted_ids() == [ROOT, ROOT]
    assert result == {
        "id": ROOT, "outcome": "partial", "reason": "failed", "children": 0,
        "freed_bytes": size, "remaining": ["state"],
    }


def test_nothing_is_tried_again_after_a_stop(fake, db):
    single(db, ROOT)
    fake.parts = {"history", "rollout"}
    fake.answer = (1, GENERIC)
    stop = StopSignal()
    fake.then = lambda: setattr(stop, "requested", True)

    [result] = codex.delete([ROOT], stop=stop, progress=lambda done, total: None)["results"]

    assert fake.deleted_ids() == [ROOT]
    assert (result["outcome"], result["reason"]) == ("partial", "failed")


def test_an_unexpected_error_still_writes_the_log(fake, db):
    single(db, ROOT)

    def boom():
        raise RuntimeError("boom")

    fake.then = boom
    with pytest.raises(RuntimeError):
        run_delete(ROOT)
    [log_path] = roots.log_dir().iterdir()
    assert json.loads(log_path.read_text())["result"]["results"] == []


def test_sigterm_while_codex_runs_finishes_that_id(fake, db, monkeypatch):
    monkeypatch.setattr(codex, "DELETE_WORKERS", 1)
    rollouts = family(db)
    single(db, OTHER)
    single(db, THIRD)
    fake.then = lambda: os.kill(os.getpid(), signal.SIGTERM)

    with StopSignal() as stop:
        report = codex.delete([ROOT, OTHER, THIRD], stop=stop, progress=lambda done, total: None)

    assert report["stopped"] is True
    assert [(r["id"], r["outcome"], r.get("reason")) for r in report["results"]] == [
        (ROOT, "deleted", None), (OTHER, "skipped", "stopped"), (THIRD, "skipped", "stopped"),
    ]
    assert fake.deleted_ids() == [ROOT]
    assert set(left(db)["threads"]) == {OTHER, THIRD}
    [log_path] = roots.log_dir().iterdir()
    doc = json.loads(log_path.read_text())
    assert (doc["command"], doc["tool"]) == ("delete", "codex")
    assert doc["result"]["results"] == report["results"]
    assert doc["result"]["stopped"] is True
    assert doc["result"]["paths"][ROOT]["rollouts"] == sorted(str(p) for p in rollouts.values())
    assert sorted(doc["result"]["paths"][ROOT]["removed"]) == sorted(str(cache(t)) for t in FAMILY)
    assert TITLE not in log_path.read_text()


def test_log_command_names_the_log(fake, db):
    family(db)
    codex.delete([ROOT], stop=StopSignal(), progress=lambda done, total: None, log_command="recover")
    [log_path] = roots.log_dir().iterdir()
    assert json.loads(log_path.read_text())["command"] == "recover"


def test_busy_when_codex_locked(fake, db, monkeypatch, capsys):
    family(db)
    real = locks.tool_lock
    monkeypatch.setattr(
        locks, "tool_lock", lambda name, *, shared, wait=10.0: real(name, shared=shared, wait=0.2)
    )
    with real("codex", shared=False):
        code = cli.main(["sessions", "delete", "codex", ROOT, "--json"])
    error = json.loads(capsys.readouterr().out)["error"]
    assert code == 1 and error["code"] == "busy"
    assert fake.calls == []


def test_invalid_input_id_touches_nothing(tmp_path, fake, db, capsys):
    family(db)
    before = snapshot(tmp_path)
    code = cli.main(["sessions", "delete", "codex", ROOT, "../x", "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_session_id"
    assert snapshot(tmp_path) == before  # no lock, no log
    assert fake.calls == []


def test_unreadable_state_db_fails_the_command_and_logs(fake, db, capsys):
    family(db)
    (db / "state_5.sqlite").write_bytes(b"this is not a database" * 100)
    code = cli.main(["sessions", "delete", "codex", ROOT, "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "codex_db_failed"
    assert fake.calls == []
    assert len(list(roots.log_dir().iterdir())) == 1


def test_json_progress_lines_on_stderr(fake, db, capsys):
    family(db)
    single(db, OTHER)
    code = cli.main(["sessions", "delete", "codex", ROOT, OTHER, "--json"])
    out = capsys.readouterr()
    assert code == 0
    assert out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert (payload["tool"], payload["stopped"]) == ("codex", False)
    assert [r["outcome"] for r in payload["results"]] == ["deleted", "deleted"]
    assert out.err.splitlines() == ["dotsync-progress 1/2", "dotsync-progress 2/2"]


