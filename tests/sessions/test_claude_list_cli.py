import json
import os
from datetime import datetime, timezone

import pytest

from dotsync import cli
from dotsync.sessions import claude, grouping, roots
from tests.sessions.claude_fixtures import make_session, user

IDLE = "11111111-1111-4111-8111-111111111111"
RUNNING = "22222222-2222-4222-8222-222222222222"
LOST = "33333333-3333-4333-8333-333333333333"
TITLE = "secret-title-xyz"
LONG_AGO = 1_700_000_000


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two sessions in one repository, one whose folder is gone, and fakes for
    `claude agents` and git."""
    repo = tmp_path / "proj"
    (repo / "sub").mkdir(parents=True)
    home = roots.claude_home()
    idle = make_session(home, "p1", IDLE, str(repo / "sub"), [user(TITLE)])
    make_session(home, "p1", RUNNING, str(repo), [user("other")], subagents=1)
    make_session(home, "p2", LOST, "/nonexistent/gone", [user("lost")])
    os.utime(idle, (LONG_AGO, LONG_AGO))

    live = claude.Liveness(agents=[{"sessionId": RUNNING, "kind": "interactive", "pid": 7}])
    monkeypatch.setattr(claude, "gather_liveness", lambda now: live)
    monkeypatch.setattr(grouping.RepoResolver, "root_of", lambda self, cwd: str(repo))
    return live


def run_list(capsys, *extra):
    code = cli.main(["sessions", "list", "claude", *extra])
    return code, capsys.readouterr()


def test_list_json_shape(world, capsys):
    code, out = run_list(capsys, "--json")
    assert code == 0
    assert out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert payload["tool"] == "claude"
    assert "agents_error" not in payload
    datetime.strptime(payload["checked_at"], "%Y-%m-%dT%H:%M:%SZ")
    by_name = {g["name"]: g for g in payload["groups"]}
    assert set(by_name) == {"proj", grouping.UNKNOWN_NAME}
    items = {i["id"]: i for g in payload["groups"] for i in g["items"]}
    assert {i: x["state"] for i, x in items.items()} == {
        IDLE: "idle", RUNNING: "running", LOST: "unknown",
    }
    assert items[LOST]["reason"] == "recent"
    assert items[LOST]["place_reason"] == "not_found"
    assert "reason" not in items[IDLE] and "reason" not in items[RUNNING]
    assert items[IDLE]["title"] == TITLE
    assert items[IDLE]["folder"] == "sub"
    assert items[RUNNING]["subagents"] == 1
    assert items[IDLE]["last_activity"] == (
        datetime.fromtimestamp(LONG_AGO, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    assert all(i["last_activity"].endswith("Z") for i in items.values())
    assert payload["totals"] == {
        "sessions": 3,
        "bytes": sum(i["bytes"] for i in items.values()),
        "running": 1,
    }
    assert by_name["proj"]["running"] == 1


def test_list_json_reports_agents_error(world, capsys):
    world.error = "process_scan_failed"
    code, out = run_list(capsys, "--json")
    assert code == 0
    assert json.loads(out.out)["agents_error"] == "process_scan_failed"


def test_list_text_has_no_titles(world, capsys):
    code, out = run_list(capsys)
    assert code == 0
    assert TITLE not in out.out
    assert "proj" in out.out and grouping.UNKNOWN_NAME in out.out


def test_list_codex_is_unsupported_for_now(world, capsys):
    code = cli.main(["sessions", "list", "codex", "--json"])
    error = json.loads(capsys.readouterr().out)["error"]
    assert code == 1 and error["code"] == "unsupported"
