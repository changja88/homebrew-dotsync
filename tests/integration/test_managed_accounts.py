from __future__ import annotations

import json
import os
import stat
import threading
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dotsync.accounts import AccountNotFound, AccountStore, ManagedAccount
from dotsync.app_paths import AppPaths
from dotsync.providers import ProviderError
from dotsync.providers.codex import CodexUsageProvider
from dotsync.usage import UsageCache, UsageResult, UsageService


_FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

home = Path(os.environ["CODEX_HOME"])
account_id = home.parent.name
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "home": os.environ.get("HOME"),
    "codex_home": os.environ.get("CODEX_HOME"),
    "tmpdir": os.environ.get("TMPDIR"),
    "sensitive_present": sorted(
        key for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
        )
        if key in os.environ
    ),
}
with (home / "fixture-invocations.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(record, sort_keys=True) + "\n")

if sys.argv[1:] == ["logout"]:
    if (home / "fail-logout").exists():
        raise SystemExit(9)
    (home / "auth.json").unlink(missing_ok=True)
    raise SystemExit(0)

if sys.argv[1:] != ["app-server"]:
    raise SystemExit(2)

def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()

for source in sys.stdin:
    message = json.loads(source)
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        result = {
            "userAgent": "codex_cli_rs/0.42.0 (DotSync integration fixture)",
            "codexHome": str(home),
            "platformFamily": "unix",
            "platformOs": "macos",
        }
    elif method == "account/login/start":
        (home / "auth.json").write_text(
            json.dumps({"fixture_account": account_id}), encoding="utf-8"
        )
        result = {
            "type": "chatgpt",
            "loginId": "login-" + account_id,
            "authUrl": "https://auth.openai.invalid/fixture",
        }
        send({"jsonrpc": "2.0", "id": request_id, "result": result})
        send({
            "jsonrpc": "2.0",
            "method": "account/login/completed",
            "params": {
                "loginId": "login-" + account_id,
                "success": True,
                "error": None,
            },
        })
        continue
    elif method == "account/read":
        result = {
            "account": {
                "type": "chatgpt",
                "email": account_id[:8] + "@example.invalid",
                "planType": "plus",
            },
            "requiresOpenaiAuth": True,
        }
    elif method == "account/rateLimits/read":
        if (home / "fail-refresh").exists():
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "private fixture failure"},
            })
            continue
        percentage = 20 + int(account_id[0:2], 16) % 70
        result = {
            "rateLimits": {
                "limitId": "codex",
                "limitName": "Codex",
                "primary": {
                    "usedPercent": percentage,
                    "windowDurationMins": 300,
                    "resetsAt": 1787302800,
                },
                "secondary": {
                    "usedPercent": percentage + 1,
                    "windowDurationMins": 10080,
                    "resetsAt": 1787907600,
                },
            }
        }
    elif method == "account/logout":
        if (home / "fail-logout").exists():
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "private fixture failure"},
            })
            continue
        (home / "auth.json").unlink(missing_ok=True)
        result = {}
    else:
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        })
        continue
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
'''


@dataclass(frozen=True)
class FakeCodexCLI:
    executable: Path

    def invocations(self, home: Path) -> list[dict[str, object]]:
        log = home / "fixture-invocations.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def fake_codex_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCodexCLI:
    executable = tmp_path / "fixture-bin" / "codex"
    executable.parent.mkdir()
    executable.write_text(_FAKE_CODEX, encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{executable.parent}:{os.defpath}")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-provider")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-reach-provider")
    return FakeCodexCLI(executable)


class ManagedAccountsApp:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.accounts = AccountStore(paths)
        self.cache = UsageCache(paths)
        provider = CodexUsageProvider(
            paths,
            clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
            rpc_timeout=2.0,
            login_timeout=2.0,
        )
        self.service = UsageService(
            paths=paths,
            accounts=self.accounts,
            cache=self.cache,
            providers={"codex": provider},
        )

    def create_and_login_codex(self, label: str) -> ManagedAccount:
        created = self.service.create_account("codex", label)
        return self.service.login(created.id, lambda progress: None)

    def refresh(self, account_id: str) -> UsageResult:
        return self.service.refresh(account_id)


@pytest.fixture
def app(fake_home: Path, fake_codex_cli: FakeCodexCLI) -> ManagedAccountsApp:
    return ManagedAccountsApp(AppPaths.for_home(fake_home))


def snapshot_tree(home: Path, names: list[str]) -> tuple[tuple[object, ...], ...]:
    entries: list[tuple[object, ...]] = []
    for name in names:
        root = home / name
        if not root.exists() and not root.is_symlink():
            entries.append((name, "missing"))
            continue
        for path in [root, *sorted(root.rglob("*"))] if root.is_dir() else [root]:
            metadata = path.lstat()
            relative = path.relative_to(home).as_posix()
            if stat.S_ISREG(metadata.st_mode):
                entries.append(
                    (
                        relative,
                        "file",
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_mtime_ns,
                        path.read_bytes(),
                    )
                )
            elif stat.S_ISDIR(metadata.st_mode):
                entries.append(
                    (
                        relative,
                        "directory",
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_mtime_ns,
                    )
                )
            else:
                entries.append((relative, "other", metadata.st_mode, metadata.st_mtime_ns))
    return tuple(entries)


def _seed_default_profiles(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(
        '{"sentinel":"default-claude"}', encoding="utf-8"
    )
    (home / ".claude.json").write_text("default-claude-root", encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(
        '{"sentinel":"default-codex"}', encoding="utf-8"
    )


def _assert_exact_invocation_scope(
    cli: FakeCodexCLI,
    app: ManagedAccountsApp,
    account: ManagedAccount,
) -> None:
    account_root = app.paths.account_root("codex", account.id)
    home = app.paths.account_home("codex", account.id)
    probe = app.paths.account_probe("codex", account.id)
    temporary = app.paths.account_tmp("codex", account.id)
    invocations = cli.invocations(home)

    assert invocations
    assert {tuple(item["argv"]) for item in invocations} == {("app-server",)}
    assert {item["codex_home"] for item in invocations} == {str(home)}
    assert {item["home"] for item in invocations} == {str(home)}
    assert {item["tmpdir"] for item in invocations} == {str(temporary)}
    assert {item["cwd"] for item in invocations} == {str(probe)}
    assert {tuple(item["sensitive_present"]) for item in invocations} == {()}
    assert all(home.is_relative_to(account_root) for home in [home, probe, temporary])
    assert tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))[
        "cli_auth_credentials_store"
    ] == "file"
    assert stat.S_IMODE((home / "config.toml").stat().st_mode) == 0o600


def test_two_codex_accounts_are_independent_and_defaults_are_unchanged(
    app: ManagedAccountsApp,
    fake_codex_cli: FakeCodexCLI,
    fake_home: Path,
) -> None:
    _seed_default_profiles(fake_home)
    before = snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"])

    personal = app.create_and_login_codex("Personal")
    work = app.create_and_login_codex("Work")
    personal_usage = app.refresh(personal.id)
    work_usage = app.refresh(work.id)

    assert personal_usage.snapshot is not None
    assert work_usage.snapshot is not None
    assert personal_usage.snapshot.account_id == personal.id
    assert work_usage.snapshot.account_id == work.id
    assert personal_usage.snapshot.account_id != work_usage.snapshot.account_id
    assert app.paths.account_home("codex", personal.id) != app.paths.account_home(
        "codex", work.id
    )
    assert snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"]) == before

    personal = app.service.rename_account(personal.id, "Personal renamed")
    work = app.service.rename_account(work.id, "Work renamed")
    personal = app.service.login(personal.id, lambda progress: None)
    work = app.service.login(work.id, lambda progress: None)
    assert (personal.label, work.label) == ("Personal renamed", "Work renamed")

    work_home = app.paths.account_home("codex", work.id)
    (work_home / "fail-refresh").touch()
    failed_work = app.refresh(work.id)
    usable_personal = app.refresh(personal.id)
    assert failed_work.stale is True
    assert failed_work.error_code == "provider_unavailable"
    assert failed_work.snapshot == work_usage.snapshot
    assert usable_personal.stale is False
    assert usable_personal.snapshot is not None
    assert usable_personal.snapshot.account_id == personal.id
    (work_home / "fail-refresh").unlink()

    personal = app.service.logout(personal.id)
    work = app.service.logout(work.id)
    assert personal.state == "logged_out"
    assert work.state == "logged_out"

    _assert_exact_invocation_scope(fake_codex_cli, app, personal)
    _assert_exact_invocation_scope(fake_codex_cli, app, work)

    app.service.delete_account(personal.id)
    with pytest.raises(AccountNotFound):
        app.accounts.get(personal.id)
    assert app.accounts.get(work.id).id == work.id

    (work_home / "fail-logout").touch()
    with pytest.raises(ProviderError) as failure:
        app.service.delete_account(work.id)
    assert failure.value.code == "provider_unavailable"
    assert app.accounts.get(work.id).id == work.id
    assert work_home.exists()

    app.service.delete_account(work.id, force_local=True)
    with pytest.raises(AccountNotFound):
        app.accounts.get(work.id)
    assert not work_home.exists()
    assert snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"]) == before


def test_concurrent_refreshes_keep_account_home_and_snapshot_correlation(
    app: ManagedAccountsApp,
    fake_codex_cli: FakeCodexCLI,
    fake_home: Path,
) -> None:
    _seed_default_profiles(fake_home)
    before = snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"])
    accounts = [
        app.create_and_login_codex("Personal"),
        app.create_and_login_codex("Work"),
    ]
    gate = threading.Barrier(3)
    results: dict[str, UsageResult] = {}

    def refresh(account: ManagedAccount) -> None:
        gate.wait(timeout=2.0)
        results[account.id] = app.refresh(account.id)

    threads = [threading.Thread(target=refresh, args=(account,)) for account in accounts]
    for thread in threads:
        thread.start()
    gate.wait(timeout=2.0)
    for thread in threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert set(results) == {account.id for account in accounts}
    for account in accounts:
        result = results[account.id]
        assert result.snapshot is not None
        assert result.snapshot.account_id == account.id
        _assert_exact_invocation_scope(fake_codex_cli, app, account)
    assert snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"]) == before
