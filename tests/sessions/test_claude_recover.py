import errno
import json
import time

import pytest

from dotsync import cli
from dotsync.sessions import claude, claude_delete, roots
from tests.sessions.claude_fixtures import make_session, user
from tests.sessions.test_claude_delete import (  # noqa: F401  (fixtures)
    CWD, PROJECT, SID, SID2, TITLE, age, agents, home, quarantined, session, snapshot, temp_dir,
)


def interrupt(transcript):
    """What a delete that stopped halfway leaves: the transcript set aside."""
    transcript.rename(quarantined(transcript))


def test_restore_renames_back_and_lists_idle(agents, session):
    interrupt(session)
    [s] = claude.scan()
    assert s.quarantined

    report = claude_delete.restore(SID)

    assert report == {
        "tool": "claude", "stopped": False, "results": [{"id": SID, "outcome": "restored"}],
    }
    assert session.exists() and not quarantined(session).exists()
    [s] = claude.scan()
    assert not s.quarantined
    now = time.time()
    assert claude.state_of(s, claude.gather_liveness(now), now) == ("idle", "idle")
    [log_path] = roots.log_dir().iterdir()
    doc = json.loads(log_path.read_text())
    assert (doc["command"], doc["tool"]) == ("recover", "claude")
    assert TITLE not in log_path.read_text()


def test_restore_refuses_when_jsonl_exists(agents, session):
    interrupt(session)
    session.write_text("a newer transcript\n")
    aside = quarantined(session)
    before = snapshot(roots.claude_home())

    [result] = claude_delete.restore(SID)["results"]

    assert result["id"] == SID and result["outcome"] == "failed"
    assert [f["path"] for f in result["failed"]] == [str(aside)]
    assert snapshot(roots.claude_home()) == before  # both files untouched


def test_restore_without_a_quarantined_copy_is_not_found(agents, session):
    [result] = claude_delete.restore(SID)["results"]
    assert result == {"id": SID, "outcome": "skipped", "reason": "not_found"}
    assert session.exists()


def test_restore_brings_back_every_copy_and_reports_each_failure(home, agents, session):
    other = make_session(home, "-work-other", SID, CWD, [user("o")])
    third = make_session(home, "-work-third", SID, CWD, [user("t")])
    age()
    for t in (session, other, third):
        interrupt(t)
    third.write_text("taken\n")

    [result] = claude_delete.restore(SID)["results"]

    assert result["outcome"] == "failed"
    assert [f["path"] for f in result["failed"]] == [str(quarantined(third))]
    assert session.exists() and other.exists()  # the others still came back
    assert quarantined(third).exists()


def test_restore_rejects_a_bad_id_before_touching_anything(tmp_path):
    before = snapshot(tmp_path)
    with pytest.raises(Exception) as e:
        claude_delete.restore("../x")
    assert getattr(e.value, "code", "") == "invalid_session_id"
    assert snapshot(tmp_path) == before


def test_finish_deletes_an_interrupted_session(home, agents, session, capsys):
    interrupt(session)

    code = cli.main(["sessions", "recover", "claude", SID, "--finish", "--json"])

    out = capsys.readouterr()
    assert code == 0
    [result] = json.loads(out.out)["results"]
    assert (result["id"], result["outcome"]) == (SID, "deleted")
    assert not quarantined(session).exists()
    assert claude.scan() == []
    [log_path] = roots.log_dir().iterdir()
    assert json.loads(log_path.read_text())["command"] == "recover"
    assert out.err.splitlines() == ["dotsync-progress 1/1"]


def test_recover_restore_cli(agents, session, capsys):
    interrupt(session)
    code = cli.main(["sessions", "recover", "claude", SID, "--restore", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["results"] == [{"id": SID, "outcome": "restored"}]
    assert session.exists()


@pytest.mark.parametrize("flags", [[], ["--restore", "--finish"]])
def test_recover_needs_restore_or_finish(flags, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["sessions", "recover", "claude", SID, *flags])
    assert e.value.code == 2


def test_missing_renamex_np_is_a_per_path_failure(monkeypatch, tmp_path):
    def missing():
        raise AttributeError("renamex_np")

    monkeypatch.setattr(claude_delete, "_renamex_np", missing)
    with pytest.raises(OSError) as e:
        claude_delete._rename_no_replace(tmp_path / "a", tmp_path / "b")
    assert e.value.errno == errno.ENOTSUP
