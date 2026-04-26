from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Auto-applied: scrub env vars that affect dotsync's behavior."""
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Override $HOME to a temp dir for filesystem-isolated tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def subprocess_blocked(monkeypatch, request):
    """Default: subprocess.run raises so tests can't accidentally execute real
    commands. Tests that explicitly want to call subprocess.run must override
    via their own monkeypatch / unittest.mock.patch (which takes precedence)."""
    if "no_subprocess_block" in request.keywords:
        return
    import subprocess
    def _block(*args, **kwargs):
        raise AssertionError(
            f"subprocess.run was called without a test-side mock: {args!r}. "
            f"Add a patch('dotsync.<module>.subprocess.run') or monkeypatch."
        )
    monkeypatch.setattr(subprocess, "run", _block)
