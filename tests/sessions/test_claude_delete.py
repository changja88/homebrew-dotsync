import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from dotsync import cli, locks
from dotsync.sessions import claude, claude_delete, procs, roots
from dotsync.sessions.stop import StopSignal
from tests.sessions.claude_fixtures import make_session, user, write_jsonl

SID = "11111111-1111-4111-8111-111111111111"
SID2 = "22222222-2222-4222-8222-222222222222"
SID3 = "33333333-3333-4333-8333-333333333333"
CWD = "/work/proj"
PROJECT = "-work-proj"
LONG_AGO = 1_700_000_000
TITLE = "secret-title-xyz"
# Everything SID owns besides its transcript and its temp dir, relative to ~/.claude.
OWNED = [
    f"projects/{PROJECT}/{SID}/subagents/agent-0.jsonl",
    f"projects/{PROJECT}/{SID}/tool-results/r.txt",
    f"projects/{PROJECT}/{SID}/mcp-tasks/k",
    f"projects/{PROJECT}/{SID}.orphaned-1-abc.jsonl",
    f"projects/{PROJECT}/{SID}.jsonl.superseded-2",
    f"file-history/{SID}/a",
    f"session-env/{SID}/e",
    f"debug/{SID}.txt",
    f"uploads/{SID}/u",
    f"dev-mods/{SID}/m",
    f"image-cache/{SID}/i",
]


class Agents:
    """`claude agents` answers `answers[n]` on call n (the last one repeats),
    after running `before[n]` if there is one. No other claude process runs."""

    def __init__(self, monkeypatch):
        self.answers: list[list[dict]] = [[]]
        self.before: dict[int, object] = {}
        self.calls = 0
        monkeypatch.setattr(claude, "claude_binary", lambda: "/bin/claude")
        monkeypatch.setattr(procs, "run", self._run)
        monkeypatch.setattr(procs, "ps_table", lambda: {})
        monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: {})

    def _run(self, argv, **kwargs):
        self.calls += 1
        if self.calls in self.before:
            self.before[self.calls]()
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")


@pytest.fixture
def agents(monkeypatch):
    return Agents(monkeypatch)


@pytest.fixture
def home():
    h = roots.claude_home()
    h.mkdir(parents=True)
    return h


@pytest.fixture
def temp_dir():
    return roots.claude_temp_root() / PROJECT / SID


@pytest.fixture
def session(home, temp_dir):
    """SID with every kind of owned path, last written long ago."""
    transcript = make_session(home, PROJECT, SID, CWD, [user(TITLE)], extra=OWNED)
    temp_dir.mkdir(parents=True)
    (temp_dir / "out.png").write_bytes(b"p" * 7)
    age()
    return transcript


def age() -> None:
    """Every file in the fake home and temp tree was last written long ago."""
    for top in (roots.claude_home(), roots.claude_temp_root()):
        for dirpath, _dirs, files in os.walk(top):
            for name in files:
                os.utime(os.path.join(dirpath, name), (LONG_AGO, LONG_AGO))


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


def tree() -> tuple[dict[str, str], dict[str, str]]:
    return snapshot(roots.claude_home()), snapshot(roots.claude_temp_root())


def quarantined(transcript: Path) -> Path:
    return transcript.with_name(transcript.name + claude.QUARANTINE_SUFFIX)


def run_delete(*ids):
    return claude_delete.delete(list(ids), stop=StopSignal(), progress=lambda done, total: None)


def test_invalid_id_touches_nothing(tmp_path, agents, session, capsys):
    before = snapshot(tmp_path)
    code = cli.main(["sessions", "delete", "claude", SID, "../x", "--json"])
    out = capsys.readouterr()
    assert code == 1
    assert json.loads(out.out)["error"]["code"] == "invalid_session_id"
    assert snapshot(tmp_path) == before  # no lock, no log, no rename
    assert agents.calls == 0


def test_delete_removes_every_owned_path(home, temp_dir, agents, session):
    owned = [home / rel for rel in OWNED] + [session, temp_dir / "out.png"]
    total = sum(p.stat().st_size for p in owned)

    [result] = run_delete(SID)["results"]

    assert result == {"id": SID, "outcome": "deleted", "freed_bytes": total}
    assert not any(os.path.lexists(p) for p in owned + [quarantined(session), temp_dir])
    assert not (home / "projects" / PROJECT / SID).exists()
    for name in ("file-history", "session-env", "uploads", "dev-mods", "image-cache"):
        assert not (home / name / SID).exists()
    assert claude.scan() == []


NEVER_TOUCHED = [
    "history.jsonl",
    "tasks/t.json",
    "plans/p.md",
    "paste-cache/c.txt",
    "shell-snapshots/s.sh",
    f"projects/{PROJECT}/memory/MEMORY.md",
    "jobs/j.json",
    "daemon/d.sock",
    # names that only look like SID's
    f"projects/{PROJECT}/{SID}x.jsonl",
    f"file-history/{SID}x/n",
    f"debug/{SID}.log",
]


def test_never_touched_paths_stay_byte_identical(home, agents, session):
    for rel in NEVER_TOUCHED:
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(f"keep {rel}")
    make_session(home, PROJECT, SID2, CWD, [user("other")], extra=[f"file-history/{SID2}/b"])
    age()
    keep = [*NEVER_TOUCHED, f"projects/{PROJECT}/{SID2}.jsonl", f"file-history/{SID2}/b"]
    # nothing is added to or removed from these either
    dirs = ["tasks", "plans", "paste-cache", "shell-snapshots", f"projects/{PROJECT}/memory",
            "jobs", "daemon", f"file-history/{SID}x"]
    before = {rel: (home / rel).read_bytes() for rel in keep}
    dirs_before = {d: snapshot(home / d) for d in dirs}

    [result] = run_delete(SID)["results"]

    assert result["outcome"] == "deleted"
    assert {rel: (home / rel).read_bytes() for rel in keep} == before
    assert {d: snapshot(home / d) for d in dirs} == dirs_before


def test_session_that_turns_running_after_quarantine_is_restored(agents, session):
    agents.answers = [[], [{"sessionId": SID, "kind": "interactive", "pid": 4242, "cwd": CWD}]]
    seen = []
    agents.before[2] = lambda: seen.append((session.exists(), quarantined(session).exists()))
    before = tree()

    [result] = run_delete(SID)["results"]

    assert seen == [(False, True)]  # the recheck ran while the transcript was set aside
    assert result == {"id": SID, "outcome": "skipped", "reason": "running", "freed_bytes": 0}
    assert session.exists() and not quarantined(session).exists()
    assert tree() == before


def test_write_to_the_quarantined_file_before_the_recheck_skips(home, temp_dir, agents, session):
    # A process that has the transcript open keeps writing to the renamed inode.
    writer = session.open("ab")
    line = b'{"type":"assistant"}\n'

    def write():
        writer.write(line)
        writer.flush()

    agents.before[2] = write
    try:
        [result] = run_delete(SID)["results"]
    finally:
        writer.close()

    assert result == {
        "id": SID, "outcome": "skipped", "reason": "unknown:recent", "freed_bytes": 0,
    }
    assert session.read_bytes().endswith(line)
    assert not quarantined(session).exists()
    assert all((home / rel).exists() for rel in OWNED)
    assert (temp_dir / "out.png").exists()


@pytest.mark.parametrize("mtime, reason", [(None, "unknown:recent"), (LONG_AGO, "unknown:new_transcript")])
def test_transcript_created_after_quarantine_is_never_replaced(agents, session, mtime, reason):
    # Claude appending by name after the rename creates a new <id>.jsonl.
    original = session.read_bytes()

    def recreate():
        session.write_bytes(b'{"type":"user"}\n')
        if mtime:
            os.utime(session, (mtime, mtime))

    agents.before[2] = recreate

    [result] = run_delete(SID)["results"]

    assert (result["outcome"], result["reason"], result["freed_bytes"]) == ("skipped", reason, 0)
    [failure] = result["failed"]  # the rename back found the name taken
    assert failure["path"] == str(quarantined(session))
    assert session.read_bytes() == b'{"type":"user"}\n'
    assert quarantined(session).read_bytes() == original


def set_aside_count(monkeypatch) -> list[Path]:
    """Records every rename to a quarantine name; the renames still happen."""
    asides: list[Path] = []
    real = claude_delete._rename_no_replace

    def rename(src, dst):
        if dst.name.endswith(claude.QUARANTINE_SUFFIX):
            asides.append(dst)
        real(src, dst)

    monkeypatch.setattr(claude_delete, "_rename_no_replace", rename)
    return asides


def test_repeated_id_found_running_is_not_set_aside_again(agents, session, monkeypatch):
    agents.answers = [[], [{"sessionId": SID, "kind": "interactive", "pid": 4242, "cwd": CWD}]]
    asides = set_aside_count(monkeypatch)

    report = run_delete(SID, SID)

    assert report["results"] == [
        {"id": SID, "outcome": "skipped", "reason": "running", "freed_bytes": 0},
    ]
    assert asides == [quarantined(session)]
    assert session.exists() and not quarantined(session).exists()


def test_repeated_id_is_deleted_once_without_a_second_result(agents, session):
    report = run_delete(SID, SID)
    assert [(r["id"], r["outcome"]) for r in report["results"]] == [(SID, "deleted")]
    assert not session.exists() and not quarantined(session).exists()


def test_session_running_at_the_start_is_never_renamed(agents, session, monkeypatch):
    agents.answers = [[{"sessionId": SID, "kind": "interactive", "pid": 4242, "cwd": CWD}]]
    renames = []
    monkeypatch.setattr(claude_delete, "_rename_no_replace", lambda *a: renames.append(a))
    before = tree()

    [result] = run_delete(SID)["results"]

    assert result == {"id": SID, "outcome": "skipped", "reason": "running", "freed_bytes": 0}
    assert renames == [] and agents.calls == 1
    assert tree() == before


def test_rename_failure_puts_the_others_back(home, agents):
    first = make_session(home, "-a", SID, "/a", [user("one")])
    second = make_session(home, "-b", SID, "/b", [user("two")])
    # left over from a restore that found <id>.jsonl taken: never replaced
    quarantined(second).write_text("older copy")
    age()
    before = tree()

    [result] = run_delete(SID)["results"]

    assert (result["outcome"], result["reason"], result["freed_bytes"]) == (
        "skipped", "rename_failed", 0,
    )
    assert [f["path"] for f in result["failed"]] == [str(second)]
    assert tree() == before
    assert first.exists() and quarantined(second).read_text() == "older copy"


def test_unknown_id_is_not_found(agents, session):
    [result] = run_delete(SID2)["results"]
    assert result == {"id": SID2, "outcome": "skipped", "reason": "not_found", "freed_bytes": 0}


@pytest.mark.parametrize("where", ["transcript", "subagent"])
def test_tool_results_kept_when_another_transcript_references_them(home, agents, session, where):
    results_dir = home / "projects" / PROJECT / SID / "tool-results"
    mention = user(f"see {results_dir}/r.txt")
    if where == "transcript":
        make_session(home, "-other", SID2, "/other", [user("x"), mention])
    else:
        make_session(home, "-other", SID2, "/other", [user("x")])
        write_jsonl(home / "projects" / "-other" / SID2 / "subagents" / "agent-1.jsonl", [mention])
    age()

    [result] = run_delete(SID)["results"]

    assert result["outcome"] == "deleted"
    assert result["kept"] == ["tool_results"]
    assert (results_dir / "r.txt").exists()
    assert not (home / "projects" / PROJECT / SID / "subagents").exists()
    assert not session.exists() and not quarantined(session).exists()
    assert "failed" not in result


def test_own_reference_does_not_keep_tool_results(home, agents):
    results_dir = home / "projects" / PROJECT / SID / "tool-results"
    make_session(home, PROJECT, SID, CWD, [user("x"), user(f"see {results_dir}/r.txt")],
                 extra=[f"projects/{PROJECT}/{SID}/tool-results/r.txt"])
    age()

    [result] = run_delete(SID)["results"]

    assert result["outcome"] == "deleted" and "kept" not in result
    assert not results_dir.exists()


def test_unreadable_transcript_keeps_tool_results(home, agents, session):
    other = make_session(home, "-other", SID2, "/other", [user("x")])
    age()
    other.chmod(0)
    try:
        [result] = run_delete(SID)["results"]
    finally:
        other.chmod(0o644)
    assert result["kept"] == ["tool_results"]
    assert (home / "projects" / PROJECT / SID / "tool-results" / "r.txt").exists()


def test_tool_result_refs_finds_a_reference_across_a_chunk_boundary(home):
    folder = home / "projects" / "-p"
    folder.mkdir(parents=True)
    big = folder / f"{SID2}.jsonl"
    ref = f"/{SID}/tool-results/".encode()
    big.write_bytes(b"x" * (8 * 1024 * 1024 - 20) + ref + b"\n")
    sub = folder / SID3 / "subagents" / "agent-1.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_bytes(b"/" + SID.upper().encode() + b"/tool-results/ /not-an-id/tool-results/ " + ref)

    assert claude_delete.tool_result_refs([big, sub]) == {SID: {SID2, SID3}}


@pytest.mark.parametrize("source", ["session", "agent"])
def test_temp_dir_kept_when_a_known_cwd_is_inside_it(home, temp_dir, agents, session, source):
    inside = str(temp_dir / "scratchpad" / "job")
    if source == "session":
        make_session(home, claude.encode_cwd(inside), SID2, inside, [user("headless")])
        age()
    else:
        agents.answers = [[{"sessionId": SID3, "kind": "interactive", "pid": 99, "cwd": inside}]]

    [result] = run_delete(SID)["results"]

    assert result["outcome"] == "deleted"
    assert result["kept"] == ["temp_dir"]
    assert (temp_dir / "out.png").exists()
    assert not (home / "file-history" / SID).exists()


def test_partial_keeps_the_quarantined_transcript_and_retry_finishes(home, temp_dir, agents, session):
    owned = [home / rel for rel in OWNED] + [session, temp_dir / "out.png"]
    total = sum(p.stat().st_size for p in owned)
    stuck = home / "file-history" / SID
    remaining = session.stat().st_size + (stuck / "a").stat().st_size
    stuck.chmod(0o500)
    try:
        [first] = run_delete(SID)["results"]
        assert first["outcome"] == "partial"
        assert [f["path"] for f in first["failed"]] == [str(stuck)]
        assert str(stuck) in first["failed"][0]["error"]
        assert first["freed_bytes"] == total - remaining
        assert quarantined(session).exists() and not session.exists()
        [s] = claude.scan()
        now = time.time()
        assert claude.state_of(s, claude.gather_liveness(now), now) == ("interrupted", "interrupted")
    finally:
        stuck.chmod(0o700)

    [second] = run_delete(SID)["results"]

    assert second == {"id": SID, "outcome": "deleted", "freed_bytes": remaining}
    assert claude.scan() == [] and not stuck.exists()


def test_sigterm_between_ids_skips_the_rest(home, agents, session):
    others = [make_session(home, PROJECT, sid, CWD, [user("o")]) for sid in (SID2, SID3)]
    age()

    def progress(done, total):
        if done == 1:
            os.kill(os.getpid(), signal.SIGTERM)

    with StopSignal() as stop:
        report = claude_delete.delete([SID, SID2, SID3], stop=stop, progress=progress)

    assert report["stopped"] is True
    assert [(r["id"], r["outcome"], r.get("reason")) for r in report["results"]] == [
        (SID, "deleted", None), (SID2, "skipped", "stopped"), (SID3, "skipped", "stopped"),
    ]
    assert all(t.exists() for t in others)
    [log_path] = roots.log_dir().iterdir()
    doc = json.loads(log_path.read_text())
    assert (doc["command"], doc["tool"]) == ("delete", "claude")
    assert doc["result"]["results"] == report["results"]
    assert doc["result"]["stopped"] is True
    assert str(session) + claude.QUARANTINE_SUFFIX in doc["result"]["removed"][SID]
    assert TITLE not in log_path.read_text()


def test_delete_busy_when_claude_sessions_locked(agents, session, monkeypatch, capsys):
    real = locks.tool_lock
    monkeypatch.setattr(
        locks, "tool_lock", lambda name, *, shared, wait=10.0: real(name, shared=shared, wait=0.2)
    )
    with real("claude-sessions", shared=False):
        code = cli.main(["sessions", "delete", "claude", SID, "--json"])
    error = json.loads(capsys.readouterr().out)["error"]
    assert code == 1 and error["code"] == "busy"
    assert session.exists() and agents.calls == 0


def test_json_progress_lines_on_stderr(home, agents, session, capsys):
    make_session(home, PROJECT, SID2, CWD, [user("o")])
    age()
    code = cli.main(["sessions", "delete", "claude", SID, SID2, "--json"])
    out = capsys.readouterr()
    assert code == 0
    assert out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert (payload["tool"], payload["stopped"]) == ("claude", False)
    assert [r["outcome"] for r in payload["results"]] == ["deleted", "deleted"]
    assert out.err.splitlines() == ["dotsync-progress 1/2", "dotsync-progress 2/2"]


def test_text_mode_prints_one_line_per_id(agents, session, capsys):
    code = cli.main(["sessions", "delete", "claude", SID, SID2])
    out = capsys.readouterr().out
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith(f"{SID}  deleted")
    assert lines[1] == f"{SID2}  skipped  not_found"
    assert TITLE not in out
