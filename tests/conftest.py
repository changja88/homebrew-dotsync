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
