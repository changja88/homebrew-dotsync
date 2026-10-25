"""Every sessions test runs against `tmp_path`, never the real ~/.claude,
~/.codex, /private/tmp/claude-<uid> or ~/Library."""

from pathlib import Path

import pytest

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
