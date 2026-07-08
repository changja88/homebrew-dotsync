from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Auto-applied: scrub env vars that affect dotsync's behavior.

    SHELL is also blanked by default so the new shell-rc auto-init step
    short-circuits unless a test explicitly opts in via monkeypatch.setenv.
    Tests that need to exercise rc auto-write must set SHELL themselves.
    """
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("SHELL", raising=False)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Override $HOME to a temp dir for filesystem-isolated tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def fake_keychain(monkeypatch):
    """In-memory stand-in for the macOS `security` CLI.

    Replaces dotsync.keychain.{read,write,delete}_secret with an in-memory
    (service, account) -> secret map so account tests never touch the real
    Keychain. `.set(service, account, secret)` seeds an entry.
    """
    from dotsync import keychain

    store: dict = {}

    def read(service, account):
        return store.get((service, account))

    def write(service, account, secret):
        store[(service, account)] = secret

    def delete(service, account):
        return store.pop((service, account), None) is not None

    monkeypatch.setattr(keychain, "read_secret", read)
    monkeypatch.setattr(keychain, "write_secret", write)
    monkeypatch.setattr(keychain, "delete_secret", delete)

    class Handle:
        raw = store

        def set(self, service, account, secret):
            store[(service, account)] = secret

    return Handle()


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
