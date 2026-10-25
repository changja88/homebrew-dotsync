import json
import os
from pathlib import Path

import pytest

from dotsync import cli
from dotsync.sessions import codex_cleanup, procs, roots

CHATGPT = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"
LOCK_A = "11111111-1111-4111-8111-111111111111"
LOCK_B = "22222222-2222-4222-8222-222222222222"
LOCK_C = "33333333-3333-4333-8333-333333333333"


def fake(monkeypatch, table, scan):
    """`table`: pid -> (ppid, comm); `scan`: pid -> OpenFiles."""
    monkeypatch.setattr(
        procs,
        "ps_table",
        lambda: {pid: procs.Proc(pid, ppid, 60, comm) for pid, (ppid, comm) in table.items()},
    )
    monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: scan)


def opened(command, txt=(), files=()):
    return procs.OpenFiles(command=command, txt=list(txt), files=list(files))


def by_pid(entries):
    return {e["pid"]: e for e in entries}


def test_codex_found_by_exe_path_when_comm_lacks_it(monkeypatch):
    fake(
        monkeypatch,
        {500: (1, "node")},
        {500: opened("node", txt=["/opt/homebrew/Caskroom/codex/0.164.0/bin/codex"])},
    )
    result = codex_cleanup.detect()
    assert result["blockers"] == [
        {"pid": 500, "kind": "terminal_codex", "name": "codex", "elapsed": 60}
    ]


def test_other_codex_binaries_are_blockers(monkeypatch):
    home = Path.home()
    sky = roots.codex_home() / "computer-use" / "SkyComputerUseService"
    codex_node = home / "Library/Application Support/Codex/bin/node"
    fake(
        monkeypatch,
        {
            1: (0, "launchd"),
            10: (1, "/x/codex-code-mode-host"),
            11: (1, "/x/SkyComputerUseService"),
            12: (1, "node"),
            13: (1, "node"),
            14: (1, "/x/SkyComputerUseService"),
        },
        {
            10: opened("h", txt=["/x/codex-code-mode-host"]),
            11: opened("s", txt=[str(sky)]),
            12: opened("n", txt=[str(codex_node)]),
            13: opened("n", txt=["/opt/homebrew/bin/node"]),
            14: opened("s", txt=["/elsewhere/SkyComputerUseService"]),
        },
    )
    blockers = by_pid(codex_cleanup.detect()["blockers"])
    assert set(blockers) == {10, 11, 12}
    assert {b["kind"] for b in blockers.values()} == {"other"}


def test_chatgpt_child_by_parent_chain(monkeypatch):
    fake(
        monkeypatch,
        {
            100: (1, CHATGPT),
            101: (100, "/x/codex-app-server"),
            102: (101, "/x/codex"),
        },
        {
            100: opened("ChatGPT", txt=[CHATGPT]),
            101: opened("srv", txt=["/x/codex-app-server"]),
            102: opened("codex", txt=["/x/codex"]),
        },
    )
    result = codex_cleanup.detect()
    kinds = {b["pid"]: b["kind"] for b in result["blockers"]}
    # ChatGPT is a blocker through its parent chain; the codex child is a child
    # of ChatGPT before it is a terminal codex. 101 runs no Codex binary.
    assert kinds == {100: "chatgpt_app", 102: "chatgpt_child"}
    assert [h["pid"] for h in result["chatgpt_helpers"]] == [101]


def test_chatgpt_main_without_lsof_record_is_found_by_comm(monkeypatch):
    fake(monkeypatch, {100: (1, CHATGPT)}, {})
    assert codex_cleanup.detect()["blockers"][0]["kind"] == "chatgpt_app"


def test_sparkle_and_crashpad_are_helpers_not_blockers(monkeypatch):
    fake(
        monkeypatch,
        {
            100: (1, CHATGPT),
            110: (100, "/x/Autoupdate"),
            111: (100, "/x/chatgpt_crashpad_handler"),
            120: (1, "/x/Autoupdate"),
        },
        {100: opened("ChatGPT", txt=[CHATGPT])},
    )
    result = codex_cleanup.detect()
    assert [b["pid"] for b in result["blockers"]] == [100]
    assert result["chatgpt_helpers"] == [
        {"pid": 110, "kind": "chatgpt_helper", "name": "Autoupdate", "elapsed": 60},
        {"pid": 111, "kind": "chatgpt_helper", "name": "chatgpt_crashpad_handler", "elapsed": 60},
    ]


def test_any_process_with_codex_home_file_open_blocks(tmp_path, monkeypatch):
    real_home = tmp_path / "real-codex"
    real_home.mkdir()
    link = tmp_path / "link-codex"
    link.symlink_to(real_home)
    monkeypatch.setenv("CODEX_HOME", str(link))
    sqlite = tmp_path / "sqlite"
    sqlite.mkdir()
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(sqlite))
    fake(
        monkeypatch,
        {
            1: (0, "launchd"),
            200: (1, "/usr/bin/vim"),
            201: (1, "/usr/bin/less"),
            202: (1, "/usr/bin/cat"),
            203: (1, "/usr/bin/tail"),
        },
        {
            200: opened("vim", files=[str(real_home / "x.sqlite")]),  # real path, linked home
            201: opened("less", files=[str(sqlite / "state.db")]),
            202: opened("cat", files=[str(tmp_path / "elsewhere" / "f")]),
            203: opened("tail", files=[str(real_home) + "-sibling/f"]),  # prefix, not a child
        },
    )
    result = codex_cleanup.detect()
    assert result["blockers"] == [
        {"pid": 200, "kind": "other", "name": "vim", "elapsed": 60},
        {"pid": 201, "kind": "other", "name": "less", "elapsed": 60},
    ]


def test_open_threads_counts_only_chatgpt_tree_locks(monkeypatch):
    lock_dir = roots.codex_home() / "thread-writer-locks"
    lock_dir.mkdir(parents=True)
    path = lambda i: str(lock_dir / f"{i}.lock")
    fake(
        monkeypatch,
        {
            100: (1, CHATGPT),
            101: (100, "/x/helper"),
            300: (1, "/x/codex"),
        },
        {
            100: opened("ChatGPT", txt=[CHATGPT], files=[path(LOCK_A), str(lock_dir / ".coordination.lock")]),
            101: opened("helper", files=[path(LOCK_A), path(LOCK_B)]),  # A counted once
            300: opened("codex", txt=["/x/codex"], files=[path(LOCK_C)]),  # terminal, not counted
        },
    )
    assert codex_cleanup.detect()["chatgpt_open_threads"] == 2


def test_own_pid_is_not_a_blocker(monkeypatch):
    me = os.getpid()
    fake(
        monkeypatch,
        {me: (1, "/x/python")},
        {me: opened("python", files=[str(roots.codex_home() / "state_5.sqlite")])},
    )
    result = codex_cleanup.detect()
    assert result == {"blockers": [], "chatgpt_helpers": [], "chatgpt_open_threads": 0}


@pytest.mark.parametrize("broken", ["ps", "lsof"])
@pytest.mark.parametrize("error", [procs.ToolFailure("timeout"), RuntimeError("boom")])
def test_tool_failure_is_detection_failed(monkeypatch, broken, error):
    fake(monkeypatch, {}, {})

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(procs, "ps_table" if broken == "ps" else "lsof_scan", fail)
    assert codex_cleanup.detect() == {
        "blockers": [{"pid": 0, "kind": "detection_failed", "name": broken, "elapsed": 0}],
        "chatgpt_helpers": [],
        "chatgpt_open_threads": 0,
    }


def test_output_has_no_arguments_or_paths(monkeypatch):
    secret = "/Users/someone/secret-project/file"
    fake(
        monkeypatch,
        {1: (0, "launchd"), 400: (1, "/usr/bin/vim")},
        {400: opened("vim", files=[str(roots.codex_home() / "f"), secret])},
    )
    text = json.dumps(codex_cleanup.detect())
    assert "secret" not in text and "/Users" not in text


def test_cli_blockers_json_and_text(monkeypatch, capsys):
    fake(monkeypatch, {500: (1, "/x/codex")}, {500: opened("codex", txt=["/x/codex"])})
    assert cli.main(["sessions", "cleanup", "codex", "--blockers", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["blockers"][0]["name"] == "codex"
    assert cli.main(["sessions", "cleanup", "codex", "--blockers"]) == 0
    assert capsys.readouterr().out.strip() == "500  terminal_codex  codex  60s"


def test_cli_blockers_text_when_nothing_runs(monkeypatch, capsys):
    fake(monkeypatch, {}, {})
    assert cli.main(["sessions", "cleanup", "codex", "--blockers"]) == 0
    assert "nothing" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["--dry-run", "--yes"])
def test_other_modes_are_unsupported_until_later_tasks(mode, capsys):
    # Tasks 12-14 replace this on purpose.
    assert cli.main(["sessions", "cleanup", "codex", mode, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported"


def test_cleanup_requires_exactly_one_mode(capsys):
    with pytest.raises(SystemExit):
        cli.main(["sessions", "cleanup", "codex"])
    with pytest.raises(SystemExit):
        cli.main(["sessions", "cleanup", "codex", "--blockers", "--yes"])
