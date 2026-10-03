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


class FakeCodexCli:
    """Stands in for the `codex` binary at the subprocess boundary.

    Answers the plugin commands dotsync runs from in-memory state and records
    every call. Tests tune `installed`, `marketplaces`, `marketplace_sources`
    (source -> name a successful `marketplace add` registers), `failing`
    (command prefixes that exit 1), `stdout` (command prefix -> raw output
    printed instead) and `missing` (binary not installed).
    """

    def __init__(self) -> None:
        self.installed: list[dict] = []
        self.marketplaces: list[dict] = []
        self.marketplace_sources: dict[str, str] = {}
        self.failing: set[str] = set()
        self.missing = False
        self.stdout: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        import json
        import subprocess

        cmd = list(cmd)
        if cmd[0] != "codex":
            raise AssertionError(f"unexpected command: {cmd!r}")
        if self.missing:
            raise FileNotFoundError("codex")
        self.calls.append(cmd)
        words = cmd[1:]
        if any(" ".join(words).startswith(prefix) for prefix in self.failing):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        for prefix, raw in self.stdout.items():
            if " ".join(words).startswith(prefix):
                return subprocess.CompletedProcess(cmd, 0, stdout=raw, stderr="")
        if words[:3] == ["plugin", "list", "--json"]:
            payload = {"installed": self.installed, "available": []}
        elif words[:4] == ["plugin", "marketplace", "list", "--json"]:
            payload = {"marketplaces": self.marketplaces}
        elif words[:3] == ["plugin", "marketplace", "add"]:
            name = self.marketplace_sources.get(words[3])
            if name is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such source")
            self.marketplaces.append({"name": name})
            payload = {"name": name}
        elif words[:2] == ["plugin", "add"]:
            self.installed.append(
                {"pluginId": words[2], "enabled": True, "installPolicy": "AVAILABLE"}
            )
            payload = {"pluginId": words[2]}
        else:
            raise AssertionError(f"unexpected codex command: {cmd!r}")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    def commands(self, *prefix: str) -> list[list[str]]:
        """Calls whose arguments after `codex` start with `prefix`."""
        return [c[1:] for c in self.calls if c[1 : 1 + len(prefix)] == list(prefix)]


@pytest.fixture
def fake_codex_cli(monkeypatch):
    import subprocess

    cli = FakeCodexCli()
    monkeypatch.setattr(subprocess, "run", cli)
    return cli
