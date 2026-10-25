import os
import subprocess
from pathlib import Path

import pytest

from dotsync.sessions import grouping
from dotsync.sessions.grouping import Locator, Place, RepoResolver


def real_run(argv, *, timeout, cwd=None, env=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def locator(home, temp=(), tool=()):
    return Locator(RepoResolver(real_run), home, list(temp), list(tool))


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.mark.no_subprocess_block
def test_rule1_worktree_maps_to_main_tree(tmp_path, home):
    main = tmp_path / "proj"
    main.mkdir()
    git("init", "-q", cwd=main)
    git("commit", "-q", "--allow-empty", "-m", "i", cwd=main)
    lane = tmp_path / "lane"
    git("worktree", "add", "-q", str(lane), cwd=main)
    loc = locator(home)
    root = str(main.resolve())
    assert loc.place(str(lane), set()) == Place(root, "exists")
    assert loc.known_roots([str(lane), str(main)]) == {root}


@pytest.mark.no_subprocess_block
def test_rule2_non_git_existing_folder_is_outside(tmp_path, home):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert locator(home).place(str(plain), set()) == Place("outside", "exists")


def test_rule2_temp_tool_home_and_home_are_outside():
    gone_home = Path("/nonexistent-home")  # the fixture home exists; deleted paths must not
    loc = locator(gone_home, temp=[Path("/nonexistent-tmp")], tool=[gone_home / ".claude"])
    for gone in ("/nonexistent-tmp/x/y", str(gone_home / ".claude" / "z"), str(gone_home)):
        assert loc.place(gone, set()) == Place("outside", "deleted")
    assert loc.place(str(gone_home / "other"), set()) == Place("unknown", "deleted")


def test_rule3_herdr_lane_exact_basename(home):
    loc = locator(home)
    known = {"/x/spring_dream_server"}
    lane = f"{home}/.herdr/worktrees/spring_dream_server/lane-1"
    assert loc.place(lane, known) == Place("/x/spring_dream_server", "deleted")
    hyphen = f"{home}/.herdr/worktrees/spring-dream-server/lane-1"
    assert loc.place(hyphen, known) == Place("unknown", "deleted")
    twice = {"/x/spring_dream_server", "/y/spring_dream_server"}
    assert loc.place(lane, twice) == Place("unknown", "deleted")


def test_rule4_deleted_inside_known_root(home):
    loc = locator(home)
    known = {"/w/spring-src", "/w/spring-src/inner"}
    cwd = "/w/spring-src/webtest-1023/ab/e2e-menu"
    assert loc.place(cwd, known) == Place("/w/spring-src", "deleted")
    assert loc.place("/w/spring-src-other/x", known) == Place("unknown", "deleted")


def test_rule6_unreadable_is_unknown_not_deleted(home, monkeypatch):
    real_stat = os.stat

    def stat(path, *a, **k):
        if str(path) == "/locked/dir":
            raise PermissionError
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", stat)
    place = locator(home).place("/locked/dir", {"/locked"})
    assert place == Place("unknown", "unreadable")
    item = {"id": "a", "cwd": "/locked/dir", "bytes": 1, "state": "idle", "last_activity": "t"}
    [group] = grouping.groups([item], {"/locked/dir": place})
    assert group["items"][0]["place_reason"] == "cwd_unreadable"


@pytest.mark.no_subprocess_block
def test_git_runs_once_per_distinct_cwd(tmp_path, home):
    dirs = []
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(str(d))
    calls = []

    def counting_run(argv, **kw):
        calls.append(argv)
        return real_run(argv, **kw)

    loc = Locator(RepoResolver(counting_run), home, [], [])
    cwds = [dirs[i % 3] for i in range(100)]
    known = loc.known_roots(cwds)
    for cwd in cwds:
        loc.place(cwd, known)
    assert len(calls) == 3


def test_resolver_strips_git_env_and_reports_failure(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/nope")
    seen = {}

    def fake(argv, *, timeout, env=None, **kw):
        seen["env"], seen["timeout"] = env, timeout
        return subprocess.CompletedProcess(argv, 0, "/r/repo.git\n", "")

    resolver = RepoResolver(fake)
    assert resolver.root_of("/r") == "/r/repo.git"  # bare: the common dir itself
    assert "GIT_DIR" not in seen["env"] and seen["timeout"] == 5

    def failing(argv, **kw):
        raise grouping.procs.ToolFailure("timeout")

    with pytest.raises(grouping.procs.ToolFailure):
        RepoResolver(failing).root_of("/r")


def test_groups_sorted_by_bytes_with_counts():
    def item(i, cwd, size, state, at):
        return {"id": i, "cwd": cwd, "bytes": size, "state": state, "last_activity": at}

    items = [
        item("1", "/a", 10, "running", "2026-10-25T01:00:00Z"),
        item("2", "/a2", 5, "idle", "2026-10-25T03:00:00Z"),
        item("3", "/o", 100, "idle", "2026-10-25T02:00:00Z"),
        item("4", "/a2", 1, "idle", "2026-10-25T04:00:00Z"),
    ]
    places = {
        "/a": Place("/r/app", "exists"),
        "/a2": Place("/r/app", "deleted"),
        "/o": Place("outside", "exists"),
    }
    out = grouping.groups(items, places)
    assert [g["key"] for g in out] == ["outside", "/r/app"]
    assert out[0]["name"] == "프로젝트 밖"
    app = out[1]
    assert (app["name"], app["folders"], app["folders_deleted"]) == ("app", 2, 1)
    assert (app["sessions"], app["bytes"], app["running"]) == (3, 16, 1)
    assert [i["id"] for i in app["items"]] == ["4", "2", "1"]


def _fake_git(returncode=0, stderr="", stdout="", raises=None):
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    fake.calls = calls
    return fake


def test_git_timeout_is_unknown_not_outside(tmp_path, home):
    fake = _fake_git(raises=grouping.procs.ToolFailure("timeout"))
    loc = Locator(RepoResolver(fake), home, [], [])
    assert loc.place(str(tmp_path), set()) == Place("unknown", "exists")
    assert loc.known_roots([str(tmp_path)]) == set()
    assert len(fake.calls) == 1  # the failure is cached too


def test_not_a_repo_exit_128_is_outside(tmp_path, home):
    fake = _fake_git(128, "fatal: not a git repository (or any of the parent directories): .git")
    loc = Locator(RepoResolver(fake), home, [], [])
    assert loc.place(str(tmp_path), set()) == Place("outside", "exists")


def test_git_other_failure_place_reason_is_git_failed(tmp_path, home):
    fake = _fake_git(128, "fatal: detected dubious ownership in repository")
    loc = Locator(RepoResolver(fake), home, [], [])
    place = loc.place(str(tmp_path), set())
    assert place == Place("unknown", "exists")
    item = {"id": "a", "cwd": str(tmp_path), "bytes": 1, "state": "idle", "last_activity": "t"}
    [group] = grouping.groups([item], {str(tmp_path): place})
    assert group["items"][0]["place_reason"] == "git_failed"


def test_cwd_status_other_errors_are_unreadable(tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    assert grouping.cwd_status(str(loop)) == "unreadable"
    assert grouping.cwd_status("a\x00b") == "unreadable"
