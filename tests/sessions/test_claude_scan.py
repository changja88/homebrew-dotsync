import json
import os
from pathlib import Path

import pytest

from dotsync.sessions import claude, roots, safety
from tests.sessions.claude_fixtures import make_session, user, write_jsonl

SID = "11111111-1111-4111-8111-111111111111"
SID2 = "22222222-2222-4222-8222-222222222222"
MIB = 1024 * 1024


@pytest.fixture
def home(tmp_path):
    h = roots.claude_home()
    h.mkdir(parents=True)
    return h


def titled(path: Path, records: list[dict]) -> Path:
    write_jsonl(path, records)
    return path


def test_encode_cwd():
    assert claude.encode_cwd("/Users/a b/x.y") == "-Users-a-b-x-y"


def test_title_prefers_custom_then_ai_then_first_user(tmp_path):
    p = tmp_path / "t.jsonl"
    meta = user("meta", isMeta=True)
    first = user("first   question\nsecond line")
    ai = {"type": "ai-title", "aiTitle": "AI one"}
    custom = {"type": "custom-title", "customTitle": "Mine"}

    titled(p, [meta, first, ai, {"type": "ai-title", "aiTitle": "AI two"}, custom])
    assert claude.read_title_and_cwd(p)[0] == "Mine"
    titled(p, [meta, first, ai, {"type": "ai-title", "aiTitle": "AI two"}])
    assert claude.read_title_and_cwd(p)[0] == "AI two"
    titled(p, [meta, first])
    assert claude.read_title_and_cwd(p)[0] == "first question"
    titled(p, [{"type": "user", "message": {"content": [
        {"type": "tool_result"}, {"type": "text", "text": "  from\tblock "}]}}])
    assert claude.read_title_and_cwd(p)[0] == "from block"


def test_title_is_first_nonempty_line_at_most_120_chars(tmp_path):
    p = titled(tmp_path / "t.jsonl", [user("\n\n" + "a" * 300 + "\nnext")])
    assert claude.read_title_and_cwd(p)[0] == "a" * 120


def test_cwd_is_first_record_cwd_in_head(tmp_path):
    p = titled(tmp_path / "t.jsonl", [
        {"type": "custom-title", "customTitle": "T"},
        user("hi", cwd="/work/a"), user("again", cwd="/work/b")])
    assert claude.read_title_and_cwd(p) == ("T", "/work/a")
    assert claude.read_title_and_cwd(titled(tmp_path / "e.jsonl", [user("x", cwd="")]))[1] == ""


def _filler(n_bytes: int) -> list[dict]:
    line = user("y" * 1000)
    return [line] * (n_bytes // 1020)


def test_title_found_in_tail_window(tmp_path):
    records = [user("opening")] + _filler(5 * MIB) + [
        {"type": "custom-title", "customTitle": "Late"}] + _filler(MIB // 2)
    p = titled(tmp_path / "t.jsonl", records)
    assert p.stat().st_size > 5 * MIB
    assert claude.read_title_and_cwd(p)[0] == "Late"


def test_title_in_the_middle_is_not_read(tmp_path, monkeypatch):
    records = [user("opening")] + _filler(int(2.5 * MIB)) + [
        {"type": "custom-title", "customTitle": "Middle"}] + _filler(int(2.5 * MIB))
    p = titled(tmp_path / "t.jsonl", records)
    assert p.stat().st_size > 5 * MIB

    read_bytes: list[int] = []
    real_open = Path.open

    class Spy:
        def __init__(self, f):
            self._f = f

        def read(self, *args):
            data = self._f.read(*args)
            read_bytes.append(len(data))
            return data

        def __getattr__(self, name):
            return getattr(self._f, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._f.__exit__(*exc)

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: Spy(real_open(self, *a, **k)))
    assert claude.read_title_and_cwd(p)[0] == "opening"
    assert 0 < sum(read_bytes) <= 4 * MIB


def test_half_written_last_line_is_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps({"type": "custom-title", "customTitle": "Earlier"}) + "\n"
        + '{"type":"custom-ti'
    )
    assert claude.read_title_and_cwd(p)[0] == "Earlier"


def test_one_id_in_two_project_folders_is_one_session(home):
    old = make_session(home, "-a", SID, "/old", [user("old one")])
    new = make_session(home, "-b", SID, "/new", [user("new one")])
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    [s] = claude.scan()
    assert s.id == SID
    assert s.cwd == "/new" and s.title == "new one"
    assert sorted(s.project_dirs) == [home / "projects/-a", home / "projects/-b"]
    assert s.bytes == old.stat().st_size + new.stat().st_size
    assert s.transcripts and len(s.transcripts) == 2


def test_owned_paths_and_sizes(home, tmp_path):
    t = make_session(
        home, "-p", SID, "/work/p", [user("hello")],
        extra=[
            f"file-history/{SID}/a", f"session-env/{SID}/e", f"debug/{SID}.txt",
            f"uploads/{SID}/u", f"dev-mods/{SID}/m", f"image-cache/{SID}/i",
            f"projects/-p/{SID}/tool-results/r", f"projects/-p/{SID}/mcp-tasks/k",
            f"projects/-p/{SID}.orphaned-1.jsonl", f"projects/-p/{SID}.jsonl.superseded-2",
            f"projects/-p/{SID}.jsonl.dotsync-deleting",
            # not owned:
            "history.jsonl", "tasks/x", "plans/y", "projects/-p/memory/z",
            f"projects/-p/{SID}x.jsonl", f"file-history/{SID}x/n",
        ],
    )
    tmp = tmp_path / "claude-tmp" / "-work-p" / SID
    tmp.mkdir(parents=True)
    (tmp / "out").write_text("x" * 10)
    [s] = claude.scan()
    # transcript + 11 owned extras + the temp file, 10 bytes each
    assert s.bytes == t.stat().st_size + 10 * 11 + 10
    assert len(s.transcripts) == 1 and len(s.quarantined) == 1
    assert s.subagents == 0
    rel = {p.name for p in s.paths}
    assert f"{SID}x.jsonl" not in rel and "memory" not in rel
    assert tmp in s.paths and home / f"debug/{SID}.txt" in s.paths


def test_subagent_transcripts_are_not_sessions(home):
    make_session(home, "-p", SID, "/w", [user("hi")], subagents=3)
    sessions = claude.scan()
    assert [s.id for s in sessions] == [SID]
    assert sessions[0].subagents == 3


def test_last_activity_is_newest_mtime_over_owned_files(home):
    t = make_session(home, "-p", SID, "/w", [user("hi")], extra=[f"file-history/{SID}/a"])
    os.utime(t, (1000, 1000))
    os.utime(home / f"file-history/{SID}/a", (5000, 5000))
    [s] = claude.scan()
    assert s.last_activity == 5000


def test_quarantined_only_session_is_listed(home):
    t = make_session(home, "-p", SID, "/set/aside", [user("kept title")])
    t.rename(t.with_name(t.name + ".dotsync-deleting"))
    [s] = claude.scan()
    assert s.transcripts == [] and len(s.quarantined) == 1
    assert (s.title, s.cwd) == ("kept title", "/set/aside")


def test_no_temp_paths_when_temp_root_is_none(home, tmp_path, monkeypatch):
    make_session(home, "-p", SID, "/work/p", [user("hi")])
    (tmp_path / "claude-tmp" / "-work-p" / SID).mkdir(parents=True)
    monkeypatch.setattr(roots, "claude_temp_root", lambda: None)
    [s] = claude.scan()
    assert not any("claude-tmp" in str(p) for p in s.paths)
    assert not any("claude-tmp" in str(p) for p in claude.owned_paths(SID, s.project_dirs, ["/work/p"]))


def test_symlinks_and_invalid_names_are_ignored(home, tmp_path):
    make_session(home, "-p", SID, "/w", [user("hi")])
    write_jsonl(home / "projects/-p/not-a-uuid.jsonl", [user("x")])
    (home / "projects/-link").symlink_to(home / "projects/-p")
    assert [s.id for s in claude.scan()] == [SID]


def test_symlinked_session_dir_marks_unsafe(home, tmp_path):
    make_session(home, "-p", SID, "/w", [user("hi")])
    outside = tmp_path / "elsewhere"
    (outside / "subagents").mkdir(parents=True)
    (outside / "subagents" / "agent-0.jsonl").write_text("{}\n")
    (home / "projects/-p" / SID).symlink_to(outside)
    [s] = claude.scan()
    assert s.unsafe is True
    assert not any(p.name in ("subagents", "tool-results", "mcp-tasks") for p in s.paths)
    assert s.subagents == 0
    with pytest.raises(safety.UnsafePath):
        claude.owned_paths(SID, [home / "projects/-p"], [])


def test_safe_session_is_not_unsafe(home):
    make_session(home, "-p", SID, "/w", [user("hi")])
    assert claude.scan()[0].unsafe is False
