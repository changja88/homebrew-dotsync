import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from dotsync import cli
from dotsync.sessions import claude, codex, grouping, procs, roots, safety
from tests.sessions.claude_fixtures import make_session, user
from tests.sessions.codex_fixtures import (
    add_edge,
    add_thread,
    make_codex_home,
    write_rollout,
)
from tests.sessions.test_procs import KOREAN, KOREAN_LSOF

ROOT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CHILD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GRANDCHILD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
GUARDIAN = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OTHER = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
REVERTED = "ffffffff-ffff-4fff-8fff-ffffffffffff"
REAL_LSOF_SCAN = procs.lsof_scan  # before any test fakes it
SPAWN = json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": ROOT}}})
GUARDIAN_SOURCE = json.dumps({"subagent": {"other": "guardian"}})


@pytest.fixture
def home():
    return make_codex_home(roots.codex_home())


@pytest.fixture(autouse=True)
def no_git_no_lsof(monkeypatch):
    monkeypatch.setattr(grouping.RepoResolver, "root_of", lambda self, cwd: None)
    monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: {})


def items_of(payload):
    return {i["id"]: i for g in payload["groups"] for i in g["items"]}


def lock_path(thread_id):
    return roots.codex_home() / "thread-writer-locks" / f"{thread_id}.lock"


def tree(home):
    add_thread(home, ROOT)
    add_thread(home, CHILD, source=SPAWN)
    add_thread(home, GRANDCHILD, source=SPAWN)
    add_thread(home, GUARDIAN, source=GUARDIAN_SOURCE)
    add_edge(home, ROOT, CHILD)
    add_edge(home, CHILD, GRANDCHILD)


def test_roots_exclude_spawn_children(home):
    tree(home)
    add_thread(home, OTHER, source="exec")
    payload = codex.list_payload()
    items = items_of(payload)
    assert set(items) == {ROOT, OTHER, GUARDIAN}
    assert items[ROOT]["children"] == 2
    assert items[OTHER]["children"] == 0
    assert payload["tool"] == "codex"
    assert payload["totals"]["sessions"] == 3


def test_a_sub_agent_that_no_thread_spawned_is_a_root(home):
    # Codex's guardian reviews have a sub-agent source but no spawn edge, so
    # no root folds them in: listed on their own, they can be deleted.
    add_thread(home, GUARDIAN, source=GUARDIAN_SOURCE, name="Guardian review")
    item = items_of(codex.list_payload())[GUARDIAN]
    assert item["title"] == "Guardian review"
    assert item["children"] == 0


def test_title_prefers_name(home):
    add_thread(home, ROOT, name="named", title="raw title")
    add_thread(home, OTHER, title="raw title")
    items = items_of(codex.list_payload())
    assert items[ROOT]["title"] == "named"
    assert items[OTHER]["title"] == "raw title"


def test_bytes_sum_root_and_children_rollouts(home):
    tree(home)
    codex_home = roots.codex_home()
    sizes = [
        write_rollout(codex_home, ROOT),
        write_rollout(codex_home, CHILD, zst=True),
        write_rollout(codex_home, GRANDCHILD, archived=True),
        write_rollout(codex_home, ROOT, rollout_id=REVERTED),
    ]
    images = codex_home / "generated_images" / ROOT
    images.mkdir(parents=True)
    (images / "big.png").write_bytes(b"x" * 10_000)
    item = items_of(codex.list_payload())[ROOT]
    assert item["bytes"] == sum(p.stat().st_size for p in sizes)


def test_last_activity_is_newest_rollout_mtime_else_updated_at(home):
    add_thread(home, ROOT, updated_at_ms=1_700_000_000_000)
    add_thread(home, OTHER, updated_at_ms=1_700_000_000_000)
    rollout = write_rollout(roots.codex_home(), ROOT)
    os.utime(rollout, (1_800_000_000, 1_800_000_000))
    items = items_of(codex.list_payload())
    assert items[ROOT]["last_activity"] == "2027-01-15T08:00:00Z"
    assert items[OTHER]["last_activity"] == "2023-11-14T22:13:20Z"


def hold(monkeypatch, *paths, pid=4242):
    files = [str(p) for p in paths]
    scan = {pid: procs.OpenFiles(command="codex", files=files)}
    monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: scan)


def test_running_when_a_childs_lock_is_open(home, monkeypatch):
    tree(home)
    add_thread(home, OTHER)
    hold(monkeypatch, lock_path(GRANDCHILD))
    items = items_of(codex.list_payload())
    assert items[ROOT]["state"] == "running"
    assert items[OTHER]["state"] == "idle"
    assert "reason" not in items[ROOT] and "reason" not in items[OTHER]


def test_lock_open_through_a_symlinked_path_counts(home, monkeypatch, tmp_path):
    add_thread(home, ROOT)
    real = tmp_path / "real-codex"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(roots.codex_home())
    hold(monkeypatch, link / "thread-writer-locks" / f"{ROOT}.lock")
    assert items_of(codex.list_payload())[ROOT]["state"] == "running"


def test_a_lock_under_a_korean_codex_home_counts(tmp_path, monkeypatch):
    # lsof runs under LC_ALL=C, so it prints the folder's UTF-8 bytes as \xNN.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / KOREAN / ".codex"))
    db = make_codex_home(roots.codex_home())
    add_thread(db, ROOT)
    add_thread(db, OTHER)
    lock = f"{tmp_path}/{KOREAN_LSOF}/.codex/thread-writer-locks/{ROOT}.lock"
    out = f"p4242\nccodex\nftxt\nn/opt/homebrew/bin/codex\nf12\nn{lock}\n"
    monkeypatch.setattr(procs, "lsof_scan", REAL_LSOF_SCAN)
    monkeypatch.setattr(procs, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, out, ""))
    items = items_of(codex.list_payload())
    assert (items[ROOT]["state"], items[OTHER]["state"]) == ("running", "idle")


def test_dotsync_itself_holding_a_lock_is_not_running(home, monkeypatch):
    add_thread(home, ROOT)
    hold(monkeypatch, lock_path(ROOT), pid=os.getpid())
    assert items_of(codex.list_payload())[ROOT]["state"] == "idle"


def test_lsof_failure_makes_every_item_unknown(home, monkeypatch):
    tree(home)
    add_thread(home, OTHER)

    def fail(fds=None):
        raise procs.ToolFailure("timeout")

    monkeypatch.setattr(procs, "lsof_scan", fail)
    payload = codex.list_payload()
    items = items_of(payload)
    assert {(i["state"], i["reason"]) for i in items.values()} == {("unknown", "scan_failed")}
    assert payload["totals"]["running"] == 0


def test_list_reads_while_a_writer_holds_a_transaction(home):
    add_thread(home, ROOT)
    writer = sqlite3.connect(home / "state_5.sqlite", isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider,"
            " cwd, title, sandbox_policy, approval_mode) VALUES (?, '', 0, 0, 'cli', 'p', '/x', 't', 's', 'a')",
            (OTHER,),
        )
        started = time.monotonic()
        items = items_of(codex.list_payload())
        assert time.monotonic() - started < 5
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert set(items) == {ROOT}


def test_open_ro_cannot_write(home):
    con = codex.open_ro(home / "state_5.sqlite")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM threads")
    con.close()


def test_invalid_ids_are_unknown_and_never_joined(home, monkeypatch):
    upper = "ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF"
    add_thread(home, upper)
    add_thread(home, "../x")
    add_thread(home, ROOT)
    add_thread(home, CHILD, source=SPAWN)
    add_edge(home, ROOT, CHILD)
    add_thread(home, OTHER)
    add_edge(home, OTHER, "../y")  # a child whose id is not canonical
    seen: list[str] = []
    real_child = safety.child
    monkeypatch.setattr(
        safety, "child", lambda root, name: (seen.append(name), real_child(root, name))[1]
    )
    items = items_of(codex.list_payload())
    for bad in (upper, "../x", OTHER):
        assert (items[bad]["state"], items[bad]["reason"]) == ("unknown", "invalid_id")
    assert items[ROOT]["state"] == "idle"
    assert seen == [f"{ROOT}.lock", f"{CHILD}.lock"]


def test_unsafe_lock_dir_is_unknown_not_a_crash(home):
    add_thread(home, ROOT)
    loop = roots.codex_home() / "thread-writer-locks"
    loop.symlink_to(loop)
    item = items_of(codex.list_payload())[ROOT]
    assert (item["state"], item["reason"]) == ("unknown", "unsafe_path")


def test_rollout_names_with_non_canonical_ids_are_ignored(home):
    upper = "ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF"
    write_rollout(roots.codex_home(), upper)
    write_rollout(roots.codex_home(), ROOT, rollout_id=upper)
    write_rollout(roots.codex_home(), OTHER)
    assert set(codex.rollout_files()) == {OTHER}


def test_rollout_walk_does_not_follow_symlinks(home, tmp_path):
    outside = tmp_path / "outside"
    write_rollout(outside, ROOT)
    (roots.codex_home() / "sessions").mkdir()
    (roots.codex_home() / "sessions" / "link").symlink_to(outside)
    assert codex.rollout_files() == {}


def test_sqlite_home_from_config_then_env(tmp_path, monkeypatch):
    codex_home = roots.codex_home()
    codex_home.mkdir()
    assert roots.codex_sqlite_home() == codex_home
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path / "from-env"))
    assert roots.codex_sqlite_home() == tmp_path / "from-env"
    (codex_home / "config.toml").write_text(f'sqlite_home = "{tmp_path / "from-config"}"\n')
    assert roots.codex_sqlite_home() == tmp_path / "from-config"
    (codex_home / "config.toml").write_text("[table]\nsqlite_home = '/ignored'\n")
    assert roots.codex_sqlite_home() == tmp_path / "from-env"
    (codex_home / "config.toml").write_text("this is = = not toml")
    assert roots.codex_sqlite_home() == tmp_path / "from-env"


def test_list_follows_sqlite_home(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(elsewhere))
    db_dir = make_codex_home(roots.codex_home(), elsewhere)
    add_thread(db_dir, ROOT)
    assert set(items_of(codex.list_payload())) == {ROOT}


def test_history_db_bytes_counts_the_wal(home):
    (home / "thread_history_1.sqlite").write_bytes(b"x" * 100)
    (home / "thread_history_1.sqlite-wal").write_bytes(b"y" * 23)
    assert codex.list_payload()["totals"]["history_db_bytes"] == 123


def test_no_state_db_is_an_empty_list(capsys):
    code = cli.main(["sessions", "list", "codex", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["groups"] == []
    assert payload["totals"] == {"sessions": 0, "bytes": 0, "running": 0, "history_db_bytes": 0}
    assert codex.cwds() == []


def test_unreadable_state_db_is_codex_db_failed(capsys):
    roots.codex_home().mkdir(parents=True)
    (roots.codex_home() / "state_5.sqlite").write_bytes(b"this is not a database" * 100)
    code = cli.main(["sessions", "list", "codex", "--json"])
    out = capsys.readouterr().out
    assert code == 1
    assert json.loads(out)["error"]["code"] == "codex_db_failed"


def test_db_without_the_tables_is_codex_db_failed(capsys):
    roots.codex_home().mkdir(parents=True)
    sqlite3.connect(roots.codex_home() / "state_5.sqlite").close()
    code = cli.main(["sessions", "list", "codex", "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "codex_db_failed"


def test_list_codex_json_shape(home, capsys):
    tree(home)
    write_rollout(roots.codex_home(), ROOT)
    code = cli.main(["sessions", "list", "codex", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("\n") == 1
    payload = json.loads(out)
    assert payload["tool"] == "codex"
    datetime.strptime(payload["checked_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert set(payload["totals"]) == {"sessions", "bytes", "running", "history_db_bytes"}
    (group,) = payload["groups"]
    assert {i["id"] for i in group["items"]} == {ROOT, GUARDIAN}
    item = next(i for i in group["items"] if i["id"] == ROOT)
    assert item["folder"] == "repo" and item["cwd"] == "/work/repo"
    assert item["children"] == 2 and item["archived"] is False
    assert item["state"] == "idle" and "reason" not in item
    assert item["last_activity"].endswith("Z")
    assert group["key"] == grouping.UNKNOWN


def test_list_codex_passes_claude_cwds_and_survives_claude_failing(home, monkeypatch, capsys):
    add_thread(home, ROOT)
    given: list[list[str]] = []
    real = codex.list_payload
    monkeypatch.setattr(codex, "list_payload", lambda extra=(): (given.append(list(extra)), real(extra))[1])
    make_session(roots.claude_home(), "p", CHILD, "/claude/cwd", [user("hi")])
    assert cli.main(["sessions", "list", "codex", "--json"]) == 0
    assert given == [["/claude/cwd"]]

    def boom():
        raise RuntimeError("claude unreadable")

    monkeypatch.setattr(claude, "scan", boom)
    assert cli.main(["sessions", "list", "codex", "--json"]) == 0
    assert given[-1] == []
    capsys.readouterr()


def test_claude_list_finds_repo_known_only_from_codex(home, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "proj"
    (repo / "sub").mkdir(parents=True)
    lane = roots.claude_home().parent / ".herdr" / "worktrees" / "proj" / "lane1"
    make_session(roots.claude_home(), "p", CHILD, str(lane), [user("hi")])
    add_thread(home, ROOT, cwd=str(repo / "sub"))
    monkeypatch.setattr(claude, "gather_liveness", lambda now: claude.Liveness(agents=[]))
    monkeypatch.setattr(
        grouping.RepoResolver, "root_of", lambda self, cwd: str(repo) if os.path.isdir(cwd) else None
    )
    # pytest's tmp dir is under /var/folders, which the default Locator treats as scratch.
    monkeypatch.setattr(
        grouping.Locator,
        "default",
        staticmethod(lambda resolver=None: grouping.Locator(grouping.RepoResolver(), Path.home(), [], [])),
    )
    assert cli.main(["sessions", "list", "claude", "--json"]) == 0
    (group,) = json.loads(capsys.readouterr().out)["groups"]
    assert group["key"] == str(repo)
    assert [i["id"] for i in group["items"]] == [CHILD]
