import json
import os
import subprocess
from pathlib import Path

import pytest

from dotsync.sessions import claude, procs, roots
from tests.sessions.claude_fixtures import make_session, user

SID = "11111111-1111-4111-8111-111111111111"
SID2 = "22222222-2222-4222-8222-222222222222"
NOW = 1_000_000.0
CWD = "/work/proj"


class Env:
    """Fakes for `claude agents`, `ps` and `lsof`."""

    def __init__(self, monkeypatch):
        self.agents: object = []
        self.agents_error: procs.ToolFailure | None = None
        self.table: dict[int, procs.Proc] = {}
        self.scan: dict[int, procs.OpenFiles] = {}
        self.scan_error: procs.ToolFailure | None = None
        self.calls: list[tuple[list[str], dict]] = []
        monkeypatch.setattr(claude, "claude_binary", lambda: "/bin/claude")
        monkeypatch.setattr(procs, "run", self._run)
        monkeypatch.setattr(procs, "ps_table", self._ps)
        monkeypatch.setattr(procs, "lsof_scan", self._lsof)

    def _run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.agents_error:
            raise self.agents_error
        return subprocess.CompletedProcess(argv, 0, json.dumps(self.agents), "")

    def _ps(self):
        if self.scan_error:
            raise self.scan_error
        return self.table

    def _lsof(self, fds=None):
        if self.scan_error:
            raise self.scan_error
        return self.scan

    def claude_proc(self, pid, *, cwd, elapsed):
        self.table[pid] = procs.Proc(pid, 1, elapsed, "claude")
        self.scan[pid] = procs.OpenFiles("claude", cwd=cwd, txt=["/opt/homebrew/bin/claude"])

    def pid_file(self, pid):
        sessions = roots.claude_home() / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f"{pid}.json").write_text("{}")


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


def session(**kw) -> claude.ClaudeSession:
    fields = dict(
        id=SID, transcripts=[], quarantined=[], project_dirs=[Path("/p/-work-proj")],
        cwd=CWD, title="", last_activity=NOW - 7200, bytes=0, subagents=0, paths=[],
    )
    fields.update(kw)
    return claude.ClaudeSession(**fields)


def state(env, s=None, now=NOW):
    return claude.state_of(s or session(), claude.gather_liveness(now), now)


def test_background_wins_over_running(env):
    for status in ("done", "failed", "working"):
        env.agents = [
            {"sessionId": SID, "kind": "interactive", "pid": 5, "cwd": CWD},
            {"sessionId": SID, "kind": "background", "state": status, "cwd": CWD},
        ]
        assert state(env) == ("background", "background")


def test_running_from_agents_pid(env):
    env.agents = [{"sessionId": SID, "kind": "interactive", "pid": 5, "cwd": CWD}]
    assert state(env) == ("running", "running")
    assert state(env, session(id=SID2)) == ("idle", "idle")


def test_live_pid_missing_from_agents_makes_all_unknown(env):
    env.claude_proc(123, cwd="/elsewhere", elapsed=10)
    env.pid_file(123)
    env.agents = []
    live = claude.gather_liveness(NOW)
    assert live.error == "agents_incomplete"
    assert claude.state_of(session(), live, NOW) == ("unknown", "agents_incomplete")
    assert claude.state_of(session(id=SID2), live, NOW) == ("unknown", "agents_incomplete")


def test_dead_pid_file_and_listed_pid_are_complete(env):
    env.claude_proc(123, cwd="/elsewhere", elapsed=10)
    env.pid_file(123)
    env.pid_file(999)  # not in ps: stale file
    (roots.claude_home() / "sessions" / "x.key").write_text("")
    env.agents = [{"sessionId": SID2, "kind": "interactive", "pid": 123, "cwd": "/e"}]
    live = claude.gather_liveness(NOW)
    assert live.error is None
    assert live.agent_pids == {123}


def test_unexplained_process_in_same_project_folder_is_unknown(env):
    env.claude_proc(77, cwd=CWD, elapsed=10000)  # started before last_activity
    assert state(env) == ("unknown", "unexplained_process")
    # matched through the encoded project folder name instead of s.cwd
    assert state(env, session(cwd="/gone")) == ("unknown", "unexplained_process")
    # a process in another folder does not count
    env.scan[77].cwd = "/other"
    assert state(env) == ("idle", "idle")


def test_process_started_after_last_activity_does_not_count(env):
    env.claude_proc(77, cwd=CWD, elapsed=100)  # started NOW-100, last_activity NOW-7200
    assert state(env) == ("idle", "idle")


def test_agent_pid_process_is_explained(env):
    env.claude_proc(77, cwd=CWD, elapsed=10000)
    env.agents = [{"sessionId": SID2, "kind": "interactive", "pid": 77, "cwd": CWD}]
    assert state(env) == ("idle", "idle")


def test_thirty_minute_rule_counts_every_owned_path(tmp_path, env):
    home = roots.claude_home()
    home.mkdir(parents=True)
    make_session(home, "-work-proj", SID, CWD, [user("hi")], extra=[])
    old = NOW - 7200
    transcript = home / "projects" / "-work-proj" / f"{SID}.jsonl"
    os.utime(transcript, (old, old))
    temp = roots.claude_temp_root() / "-work-proj" / SID
    temp.mkdir(parents=True)
    f = temp / "out.txt"
    f.write_text("x")
    os.utime(f, (NOW - 600, NOW - 600))
    [s] = claude.scan()
    assert s.last_activity == NOW - 600
    assert state(env, s) == ("unknown", "recent")


def test_interrupted_and_idle(env):
    q = Path("/p/-work-proj/x.jsonl.dotsync-deleting")
    assert state(env, session(quarantined=[q])) == ("interrupted", "interrupted")
    assert state(env) == ("idle", "idle")


def test_unsafe_session_is_unknown(env):
    assert state(env, session(unsafe=True)) == ("unknown", "unsafe_path")
    # running still wins over unsafe
    env.agents = [{"sessionId": SID, "kind": "interactive", "pid": 5, "cwd": CWD}]
    assert state(env, session(unsafe=True)) == ("running", "running")


def test_desktop_cwd_is_desktop(env):
    cwd = str(Path.home() / "Library/Application Support/Claude/local-agent-mode/x")
    assert state(env, session(cwd=cwd)) == ("desktop", "desktop")


def test_agents_call_drops_claude_config_dir(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
    monkeypatch.setenv("KEEP_ME", "1")
    claude.gather_liveness(NOW)
    argv, kwargs = env.calls[0]
    assert argv == ["/bin/claude", "agents", "--json", "--all"]
    assert "CLAUDE_CONFIG_DIR" not in kwargs["env"]
    assert kwargs["env"]["KEEP_ME"] == "1"
    assert kwargs["cwd"] == Path.home()
    assert kwargs["timeout"] == 30


def test_agents_timeout_is_agents_failed(env):
    env.agents_error = procs.ToolFailure("timeout")
    live = claude.gather_liveness(NOW)
    assert (live.agents, live.error) == (None, "agents_failed")
    assert claude.state_of(session(), live, NOW) == ("unknown", "agents_failed")


def test_agents_missing_binary_and_bad_output(env, monkeypatch):
    env.agents_error = procs.ToolFailure("missing")
    assert claude.gather_liveness(NOW).error == "agents_missing"
    env.agents_error = None
    env.agents = {"not": "a list"}
    assert claude.gather_liveness(NOW).error == "agents_failed"
    monkeypatch.setattr(claude, "claude_binary", lambda: None)
    assert claude.gather_liveness(NOW).error == "agents_missing"


def test_process_scan_failure(env):
    env.scan_error = procs.ToolFailure("timeout")
    live = claude.gather_liveness(NOW)
    assert live.error == "process_scan_failed"
    assert claude.state_of(session(), live, NOW) == ("unknown", "process_scan_failed")


def test_known_cwds_and_procs(env):
    env.claude_proc(5, cwd="/a", elapsed=60)
    env.table[6] = procs.Proc(6, 1, 5, "node")
    env.scan[6] = procs.OpenFiles("node", cwd="/b", txt=["/usr/bin/node"])
    env.agents = [{"sessionId": SID, "kind": "background", "cwd": "/c"}]
    live = claude.gather_liveness(NOW)
    assert live.procs == [claude.ClaudeProc(5, NOW - 60, "/a")]
    assert live.known_cwds == {"/a", "/c"}
