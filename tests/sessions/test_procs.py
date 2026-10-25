import subprocess
from types import SimpleNamespace

import pytest

from dotsync.sessions import procs


def completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def patch_run(monkeypatch, result):
    calls = []

    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("dotsync.sessions.procs.subprocess.run", fake)
    return calls


def test_ps_table_parses_etime_forms(monkeypatch):
    out = (
        "  100     1       05:10 /usr/bin/foo\n"
        "  200   100    02:36:50 /Applications/Some App.app/Contents/MacOS/Some App\n"
        "  300   200 01-09:19:29 bar\n"
    )
    calls = patch_run(monkeypatch, completed(stdout=out))
    table = procs.ps_table()
    assert table[100] == procs.Proc(100, 1, 310, "/usr/bin/foo")
    assert table[200].elapsed == 9410
    assert table[200].comm == "/Applications/Some App.app/Contents/MacOS/Some App"
    assert table[300] == procs.Proc(300, 200, 119969, "bar")
    argv, kwargs = calls[0]
    assert argv == ["/bin/ps", "-axo", "pid=,ppid=,etime=,comm="]
    assert kwargs["timeout"] == 10
    assert kwargs["env"]["LC_ALL"] == "C"


def test_ps_failure_on_stderr(monkeypatch):
    patch_run(monkeypatch, completed(stdout="1 0 00:01 x\n", stderr="oops"))
    with pytest.raises(procs.ToolFailure) as info:
        procs.ps_table()
    assert info.value.code == "failed"


def test_lsof_scan_parses_records(monkeypatch):
    out = (
        "p10\ncclaude\nfcwd\nn/work/a\nftxt\nn/opt/claude\nf5\nn/tmp/x\nf6\nn/tmp/y\n"
        "p20\ncnode\nfcwd\nn/work/b\n"
    )
    calls = patch_run(monkeypatch, completed(stdout=out))
    scan = procs.lsof_scan()
    assert scan[10] == procs.OpenFiles("claude", "/work/a", ["/opt/claude"], ["/tmp/x", "/tmp/y"])
    assert scan[20] == procs.OpenFiles("node", "/work/b", [], [])
    argv, kwargs = calls[0]
    assert argv[:6] == ["/usr/sbin/lsof", "-nP", "-w", "-u", str(__import__("os").getuid()), "-F"]
    assert argv[6] == "pcfn" and "-a" not in argv
    assert kwargs["timeout"] == 10


def test_lsof_scan_limits_fds(monkeypatch):
    calls = patch_run(monkeypatch, completed(stdout=""))
    procs.lsof_scan(fds="cwd,txt")
    assert calls[0][0][-3:] == ["-a", "-d", "cwd,txt"]


def test_lsof_exit_1_is_failure(monkeypatch):
    patch_run(monkeypatch, completed(stdout="p1\n", returncode=1))
    with pytest.raises(procs.ToolFailure) as info:
        procs.lsof_scan()
    assert info.value.code == "failed"


def test_lsof_stderr_is_failure(monkeypatch):
    patch_run(monkeypatch, completed(stdout="p1\n", stderr="warning"))
    with pytest.raises(procs.ToolFailure) as info:
        procs.lsof_scan()
    assert info.value.code == "failed"


def test_run_timeout_and_missing(monkeypatch):
    patch_run(monkeypatch, subprocess.TimeoutExpired(["x"], 1))
    with pytest.raises(procs.ToolFailure) as info:
        procs.run(["x"], timeout=1)
    assert info.value.code == "timeout"
    patch_run(monkeypatch, FileNotFoundError())
    with pytest.raises(procs.ToolFailure) as info:
        procs.run(["x"], timeout=1)
    assert info.value.code == "missing"


def test_run_passes_options_and_ignores_return_code(monkeypatch):
    calls = patch_run(monkeypatch, completed(returncode=3))
    result = procs.run(["x"], timeout=2, cwd=None, env={"A": "1"})
    assert result.returncode == 3
    kwargs = calls[0][1]
    assert kwargs["capture_output"] and kwargs["text"] and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 2 and kwargs["env"] == {"A": "1"}


def test_ancestors_stop_on_loop():
    table = {
        5: procs.Proc(5, 4, 1, "a"),
        4: procs.Proc(4, 3, 1, "b"),
        3: procs.Proc(3, 4, 1, "c"),
    }
    assert procs.ancestors(5, table) == [4, 3]


def test_ancestors_stop_at_pid_1():
    table = {5: procs.Proc(5, 2, 1, "a"), 2: procs.Proc(2, 1, 1, "b"), 1: procs.Proc(1, 0, 1, "launchd")}
    assert procs.ancestors(5, table) == [2]


def test_exe_name_prefers_txt_path():
    table = {7: procs.Proc(7, 1, 1, "/usr/bin/node")}
    scan = {7: procs.OpenFiles("claude", None, ["/opt/homebrew/Caskroom/claude-code@latest/2.1.310/claude"], [])}
    assert procs.exe_name(7, table, scan) == "claude"
    assert procs.exe_name(7, table, {}) == "node"


def test_exe_name_is_empty_for_an_unknown_pid():
    assert procs.exe_name(99, {}, {}) == ""


def test_run_maps_other_oserror_to_failed(monkeypatch):
    patch_run(monkeypatch, PermissionError(13, "Permission denied"))
    with pytest.raises(procs.ToolFailure) as info:
        procs.run(["x"], timeout=1)
    assert info.value.code == "failed"
