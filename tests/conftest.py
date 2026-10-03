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


class FakeAccountsCli:
    """Stands in for `security` (the macOS Keychain) and `claude` for
    `dotsync account`.

    `keychain` maps a Keychain service name to its stored secret. `browser`
    is the claude.ai account the browser approves at `claude auth login`:
    {"oauthAccount": {...}, "secret": "..."}. `login_fails` makes the login
    exit 1, `login_interrupted` makes it raise KeyboardInterrupt (as Ctrl-C
    or SIGTERM would), and `ignore_writes` drops every Keychain write. The
    service name for a config folder is computed here independently of
    dotsync, so tests pin Claude Code's naming rule.
    """

    USER = "tester"
    # Recorded 2026-10-04 from `claude -p … get_usage` with an empty,
    # never-logged-in CLAUDE_CONFIG_DIR (exit 0).
    NOT_LOGGED_IN_ANSWER = (
        '{"type":"control_response","response":{"subtype":"success","request_id":"u1",'
        '"response":{"session":{"total_cost_usd":0,"total_api_duration_ms":0,'
        '"total_duration_ms":257,"total_lines_added":0,"total_lines_removed":0,'
        '"model_usage":{}},"subscription_type":null,"rate_limits_available":false,'
        '"rate_limits":null,"behaviors":null}}}\n'
    )

    def __init__(self) -> None:
        self.keychain: dict[str, str] = {}
        self.browser: dict | None = None
        self.login_fails = False
        self.login_interrupted = False
        self.usage: dict = {}
        self.probes: list[str] = []
        self.probe_hook = None
        self.probe_cwds: dict = {}
        self.ignore_writes = False
        self.calls: list[list[str]] = []
        self.stdin: list[str] = []

    @staticmethod
    def service_for(folder: Path) -> str:
        import hashlib

        return "Claude Code-credentials-" + hashlib.sha256(str(folder).encode()).hexdigest()[:8]

    def __call__(self, cmd, *args, input=None, env=None, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[0] == "security":
            return self._security(cmd, input)
        if Path(cmd[0]).name == "claude":
            return self._claude(cmd, env or {}, {**kwargs, "input": input})
        raise AssertionError(f"unexpected command: {cmd!r}")

    def _security(self, cmd, stdin):
        import shlex
        import subprocess

        if cmd[1:] == ["-i"]:
            self.stdin.append(stdin)
            for line in stdin.splitlines():
                words = shlex.split(line)
                if not words:
                    continue
                assert words[0] == "add-generic-password" and "-U" in words, line
                assert words[words.index("-a") + 1] == self.USER
                service = words[words.index("-s") + 1]
                if not self.ignore_writes:
                    self.keychain[service] = bytes.fromhex(words[words.index("-X") + 1]).decode()
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "delete-generic-password":
            assert cmd[cmd.index("-a") + 1] == self.USER
            found = self.keychain.pop(cmd[cmd.index("-s") + 1], None) is not None
            return subprocess.CompletedProcess(cmd, 0 if found else 44, stdout="", stderr="")
        if cmd[1] == "find-generic-password":
            assert cmd[cmd.index("-a") + 1] == self.USER
            service = cmd[cmd.index("-s") + 1]
            if service not in self.keychain:
                return subprocess.CompletedProcess(
                    cmd, 44, stdout="",
                    stderr="security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.\n",
                )
            out = self.keychain[service] + "\n" if "-w" in cmd else f'    "svce"<blob>="{service}"\n'
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        raise AssertionError(f"unexpected security command: {cmd!r}")

    def _claude(self, cmd, env, kwargs):
        import json
        import subprocess
        import sys

        if cmd[1] == "-p":
            return self._probe(cmd, env, kwargs)
        folder = Path(env["CLAUDE_CONFIG_DIR"])
        doc_path = folder / ".claude.json"
        doc = json.loads(doc_path.read_text()) if doc_path.exists() else {}
        if cmd[1:] == ["auth", "login"]:
            # The real command prints its progress; it must not reach
            # dotsync's stdout, which carries the --json answer.
            (kwargs.get("stdout") or sys.stdout).write("Opening browser to sign in…\n")
            if self.login_interrupted:
                raise KeyboardInterrupt
            if self.login_fails or self.browser is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            folder.mkdir(parents=True, exist_ok=True)
            doc["oauthAccount"] = self.browser["oauthAccount"]
            self.keychain[self.service_for(folder)] = self.browser["secret"]
        elif cmd[1:] == ["auth", "logout"]:
            doc.pop("oauthAccount", None)
            self.keychain.pop(self.service_for(folder), None)
        else:
            raise AssertionError(f"unexpected claude command: {cmd!r}")
        if folder.exists():
            doc_path.write_text(json.dumps(doc, indent=2))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    @staticmethod
    def usage_answer(five, week, subscription="max") -> str:
        """A logged-in `get_usage` answer shaped like the one recorded
        2026-10-03; `five`/`week` are (utilization, resets_at) or None."""
        import json

        def window(w):
            if w is None:
                return None
            return {"utilization": w[0], "resets_at": w[1], "limit_dollars": None,
                    "used_dollars": None, "remaining_dollars": None, "locked_reason": None}

        data = {
            "session": {"total_cost_usd": 0, "model_usage": {}},
            "subscription_type": subscription,
            "rate_limits_available": True,
            "rate_limits": {"five_hour": window(five), "seven_day": window(week),
                            "seven_day_opus": None},
        }
        return json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": "u1", "response": data}}) + "\n"

    def reply_usage(self, key, five, week, subscription="max"):
        self.usage[key] = (0, self.usage_answer(five, week, subscription))

    def reply_not_logged_in(self, key):
        self.usage[key] = (0, self.NOT_LOGGED_IN_ANSWER)

    def reply_raw(self, key, stdout, returncode=0):
        self.usage[key] = (returncode, stdout)

    def reply_timeout(self, key):
        self.usage[key] = "timeout"

    def reply_bytes(self, key, raw: bytes, returncode=0):
        """Raw output, decoded the way subprocess.run(text=True) would."""
        self.usage[key] = ("bytes", returncode, raw)

    def reply_raise(self, key, error: BaseException):
        self.usage[key] = ("raise", error)

    def _probe(self, cmd, env, kwargs):
        import json
        import subprocess

        assert json.loads(kwargs["input"]) == {
            "type": "control_request", "request_id": "u1", "request": {"subtype": "get_usage"}
        }
        assert kwargs.get("timeout") == 30
        key = Path(env["CLAUDE_CONFIG_DIR"]).name if "CLAUDE_CONFIG_DIR" in env else "seat"
        self.probes.append(key)
        self.probe_cwds[key] = kwargs.get("cwd")
        if self.probe_hook is not None:
            self.probe_hook(key)
        if key not in self.usage:
            raise AssertionError(f"unexpected usage probe for {key}")
        reply = self.usage[key]
        if reply == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if reply[0] == "raise":
            raise reply[1]
        if reply[0] == "bytes":
            _, returncode, raw = reply
            stdout = raw.decode("utf-8", kwargs.get("errors") or "strict")
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
        returncode, stdout = reply
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    def argv_text(self) -> str:
        """Every argument passed on a command line, joined — secrets must
        never show up here (they would be visible in the process list)."""
        return " ".join(" ".join(c) for c in self.calls)


@pytest.fixture
def fake_accounts_cli(monkeypatch, fake_home):
    import getpass
    import shutil
    import subprocess

    cli = FakeAccountsCli()
    monkeypatch.setattr(subprocess, "run", cli)
    monkeypatch.setattr(getpass, "getuser", lambda: FakeAccountsCli.USER)
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}" if name == "claude" else None)
    return cli
