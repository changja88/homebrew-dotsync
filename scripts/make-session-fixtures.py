#!/usr/bin/env python3
"""Regenerates the JSON fixtures the Swift tests decode.

Runs the real `dotsync sessions ... --json` code against fake homes built by
the Python tests' own builders, with `claude agents`, `codex delete`, `lsof`
and `ps` faked the way those tests fake them. Nothing touches the real home:
HOME, the Claude temp tree and every other root point into a temp dir.

    PYTHONPATH=lib:. .venv/bin/python3 scripts/make-session-fixtures.py [OUT_DIR]

OUT_DIR defaults to macos/DotsyncKit/Tests/DotsyncKitTests/Fixtures. Each
file is the CLI's stdout (or the log file) verbatim.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from dotsync import cli
from dotsync.sessions import claude, grouping, procs, roots
from tests.sessions import test_claude_delete as cd
from tests.sessions import test_claude_list_cli as cl
from tests.sessions import test_codex_cleanup_run as cr
from tests.sessions import test_codex_delete as xd
from tests.sessions import test_codex_dry_run as dr
from tests.sessions import test_codex_list as xl
from tests.sessions.claude_fixtures import make_session, user
from tests.sessions.codex_fixtures import add_edge, add_thread, make_codex_home, write_rollout

DEFAULT_OUT = Path("macos/DotsyncKit/Tests/DotsyncKitTests/Fixtures")


@contextlib.contextmanager
def fake_world():
    """What tests/sessions/conftest.py does for every test."""
    with tempfile.TemporaryDirectory() as raw, pytest.MonkeyPatch.context() as mp:
        tmp = Path(raw).resolve()
        home = tmp / "home"
        home.mkdir()
        mp.setenv("HOME", str(home))
        mp.setattr(Path, "home", lambda: home)
        for name in ("CODEX_HOME", "CLAUDE_CODE_TMPDIR", "CLAUDE_CONFIG_DIR"):
            mp.delenv(name, raising=False)
        mp.setattr(roots, "claude_temp_root", lambda: tmp / "claude-tmp")
        yield mp


def run_cli(*argv: str, exits: int = 0) -> tuple[str, str]:
    """(stdout, stderr) of `dotsync <argv>`; exits `exits` or the fixture is wrong."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    assert code == exits, (argv, out.getvalue())
    assert out.getvalue().count("\n") == 1
    return out.getvalue(), err.getvalue()


def claude_list() -> str:
    with fake_world() as mp:
        repo = roots.claude_home().parent / "proj"
        (repo / "sub").mkdir(parents=True)
        home = roots.claude_home()
        idle = make_session(home, "p1", cl.IDLE, str(repo / "sub"), [user("a title")])
        make_session(home, "p1", cl.RUNNING, str(repo), [user("other")], subagents=1)
        make_session(home, "p2", cl.LOST, "/nonexistent/gone", [user("lost")])
        os.utime(idle, (cl.LONG_AGO, cl.LONG_AGO))
        live = claude.Liveness(agents=[{"sessionId": cl.RUNNING, "kind": "interactive", "pid": 7}])
        mp.setattr(claude, "gather_liveness", lambda now: live)
        mp.setattr(grouping.RepoResolver, "root_of", lambda self, cwd: str(repo))
        return run_cli("sessions", "list", "claude", "--json")[0]


def codex_list() -> str:
    with fake_world() as mp:
        mp.setattr(grouping.RepoResolver, "root_of", lambda self, cwd: None)
        mp.setattr(procs, "lsof_scan", lambda fds=None: {})
        db = make_codex_home(roots.codex_home())
        add_thread(db, xl.ROOT, name="a named root", cwd="/work/repo")
        add_thread(db, xl.CHILD, source=xl.SPAWN)
        add_thread(db, xl.GRANDCHILD, source=xl.SPAWN)
        add_edge(db, xl.ROOT, xl.CHILD)
        add_edge(db, xl.CHILD, xl.GRANDCHILD)
        add_thread(db, xl.OTHER, source="exec", archived=True, cwd="/work/other")
        for thread in (xl.ROOT, xl.CHILD, xl.OTHER):
            write_rollout(roots.codex_home(), thread)
        return run_cli("sessions", "list", "codex", "--json")[0]


def claude_delete() -> tuple[str, str]:
    """deleted (SID), skipped with `failed` (SID2: the rename back found the
    name taken), partial (SID3: a folder that cannot be emptied), skipped
    running (SID4). Returns (report, log file text)."""
    with fake_world() as mp:
        agents = cd.Agents(mp)
        home = roots.claude_home()
        home.mkdir(parents=True)
        make_session(home, cd.PROJECT, cd.SID, cd.CWD, [user("one")], extra=cd.OWNED)
        taken = make_session(home, "-b", cd.SID2, "/b", [user("two")])
        first = make_session(home, "-a", cd.SID2, "/a", [user("two")])
        cd.quarantined(taken).write_text("older copy")
        make_session(home, "-c", cd.SID3, "/c", [user("three")], extra=[f"file-history/{cd.SID3}/a"])
        sid4 = "44444444-4444-4444-8444-444444444444"
        make_session(home, "-d", sid4, "/d", [user("four")])
        agents.answers = [[{"sessionId": sid4, "kind": "interactive", "pid": 4242, "cwd": "/d"}]]
        cd.age()
        stuck = home / "file-history" / cd.SID3
        stuck.chmod(0o500)
        try:
            out, _ = run_cli("sessions", "delete", "claude", "--json", "--", cd.SID, cd.SID2, cd.SID3, sid4)
        finally:
            stuck.chmod(0o700)
        assert first.exists()
        return out, only_log()


class PerIdCodex(xd.FakeCodex):
    """The real fake `codex delete`, answering by thread: a refusal for
    THIRD, and for OTHER a history database that cannot be read afterwards."""

    def __call__(self, argv, **kwargs):
        self.parts, self.answer, self.then = {"history", "rollout", "state"}, (0, ""), None
        if argv[3] == xd.THIRD:
            self.parts, self.answer = set(), (1, "WARN something else\n" + xd.GENERIC)
        if argv[3] == xd.OTHER:
            self.then = lambda: (self.db / "thread_history_1.sqlite").chmod(0)
        return super().__call__(argv, **kwargs)


def codex_delete() -> str:
    with fake_world() as mp:
        db = make_codex_home(roots.codex_home())
        mp.setattr(procs, "lsof_scan", lambda fds=None: {})
        PerIdCodex(mp, db)
        xd.family(db)
        xd.single(db, xd.THIRD)
        xd.single(db, xd.OTHER)
        try:
            return run_cli("sessions", "delete", "codex", "--json", "--", xd.ROOT, xd.THIRD, xd.OTHER)[0]
        finally:
            (db / "thread_history_1.sqlite").chmod(0o644)


@contextlib.contextmanager
def nothing_runs():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(procs, "ps_table", lambda: {})
        mp.setattr(procs, "lsof_scan", lambda fds=None: {})
        yield mp


def dry_run_items() -> str:
    with fake_world(), nothing_runs():
        db = make_codex_home(roots.codex_home())
        add_thread(db, dr.KEPT)
        write_rollout(roots.codex_home(), dr.KEPT)
        for key in (dr.GONE, dr.GONE2, dr.KEPT):
            dr.add_history(db, key)
        dr.jsonl("history.jsonl", [{"session_id": dr.GONE, "ts": 1, "text": "a"}] * 3)
        dr.jsonl("session_index.jsonl", [{"id": dr.GONE, "thread_name": "n"}])
        dr.add_queue(db, dr.GONE)
        cache = roots.codex_home() / dr.codex.CACHE_DIR
        cache.mkdir()
        (cache / dr.GONE).write_text("")
        return run_cli("sessions", "cleanup", "codex", "--dry-run", "--json")[0]


def dry_run_refusal() -> str:
    with fake_world(), nothing_runs():
        db = make_codex_home(roots.codex_home())
        add_thread(db, dr.KEPT)
        write_rollout(roots.codex_home(), dr.KEPT)
        dr.add_history(db, dr.GONE)
        (roots.codex_home() / "rollout-migrations").mkdir()
        (roots.codex_home() / "rollout-migrations" / f"{dr.GONE}.pending").write_text("")
        return run_cli("sessions", "cleanup", "codex", "--dry-run", "--json")[0]


def cleanup_yes() -> tuple[str, str]:
    """All four items and the cache files, then a vacuum. Returns (report, log)."""
    with fake_world(), nothing_runs():
        db = make_codex_home(roots.codex_home())
        add_thread(db, cr.KEPT)
        write_rollout(roots.codex_home(), cr.KEPT)
        write_rollout(roots.codex_home(), cr.FORK, meta={"id": cr.FORK, "history_base": {"thread_id": cr.BASE}})
        cr.coordination_lock().parent.mkdir()
        cr.coordination_lock().write_bytes(b"")
        cr.full_home(db)
        out, _ = run_cli(
            "sessions", "cleanup", "codex", "--yes", "--json", "--vacuum",
            "--items", ",".join(cr.ALL),
        )
        return out, only_log()


def cleanup_refused() -> str:
    """A cleanup refused once it held the lock: a terminal Codex started
    after the app's own check. Returns the log, which holds the error."""
    with fake_world(), nothing_runs() as mp:
        mp.setattr(procs, "ps_table", lambda: {500: procs.Proc(500, 1, 60, "/x/codex")})
        mp.setattr(procs, "lsof_scan", lambda fds=None: {500: procs.OpenFiles("codex", txt=["/x/codex"])})
        db = make_codex_home(roots.codex_home())
        add_thread(db, cr.KEPT)
        write_rollout(roots.codex_home(), cr.KEPT)
        out, _ = run_cli("sessions", "cleanup", "codex", "--yes", "--json", "--items", ",".join(cr.ALL), exits=1)
        assert json.loads(out)["error"]["code"] == "codex_running"
        return only_log()


def only_log() -> str:
    [path] = sorted(roots.log_dir().iterdir())
    return path.read_text(encoding="utf-8")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    claude_report, claude_log = claude_delete()
    cleanup_report, cleanup_log = cleanup_yes()
    files = {
        "claude-list.json": claude_list(),
        "codex-list.json": codex_list(),
        "claude-delete-report.json": claude_report,
        "codex-delete-report.json": codex_delete(),
        "cleanup-dry-run.json": dry_run_items(),
        "cleanup-dry-run-refusal.json": dry_run_refusal(),
        "cleanup-yes-report.json": cleanup_report,
        "session-log-delete.json": claude_log,
        "session-log-cleanup.json": cleanup_log,
        "session-log-cleanup-refused.json": cleanup_refused(),
    }
    for name, text in files.items():
        json.loads(text)
        (out / name).write_text(text, encoding="utf-8")
        print(name, len(text))


if __name__ == "__main__":
    main()
