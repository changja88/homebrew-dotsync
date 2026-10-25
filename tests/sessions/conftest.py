"""Every sessions test runs against `tmp_path`, never the real ~/.claude,
~/.codex, /private/tmp/claude-<uid> or ~/Library."""

import errno
from pathlib import Path

import pytest

from dotsync import cli_sessions
from dotsync.sessions import roots


@pytest.fixture(autouse=True)
def isolate_session_roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("CODEX_HOME", "CLAUDE_CODE_TMPDIR", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(roots, "claude_temp_root", lambda: tmp_path / "claude-tmp")


@pytest.fixture(autouse=True)
def popen_blocked(monkeypatch, request):
    """Like tests/conftest.py does for subprocess.run: no real child process
    unless the test is marked `no_subprocess_block`."""
    if "no_subprocess_block" in request.keywords:
        return
    import subprocess

    def _block(*args, **kwargs):
        raise AssertionError(f"subprocess.Popen was called without a test-side mock: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", _block)


class GoneStderr:
    """stderr whose reader has gone, as when the app quit before a long run
    ended: every write raises `error`, BrokenPipeError unless set."""

    def __init__(self):
        self.error: OSError = BrokenPipeError(errno.EPIPE, "Broken pipe")
        self.writes = 0

    def write(self, text):
        self.writes += 1
        raise self.error

    def flush(self):
        raise self.error


@pytest.fixture
def gone_stderr(monkeypatch):
    """A GoneStderr, with progress() not yet told. The test puts it on
    sys.stderr in its body: pytest sets its own capture back there after the
    fixtures have run."""
    monkeypatch.setattr(cli_sessions, "_stderr_gone", False, raising=False)
    return GoneStderr()
