import json
from pathlib import Path

import pytest

from dotsync.cli import main

SEAT = "Claude Code-credentials"
ALICE = {"accountUuid": "uuid-a", "emailAddress": "alice@example.com"}
BOB = {"accountUuid": "uuid-b", "emailAddress": "bob@example.com"}


def _seat(home: Path, cli, account: dict, secret: str) -> None:
    cli.keychain[SEAT] = secret
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": account}, indent=2))


def _saved(home: Path, cli, name: str, account: dict, secret: str) -> Path:
    folder = home / ".claude-accounts" / name
    folder.mkdir(parents=True)
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": account}, indent=2))
    cli.keychain[cli.service_for(folder)] = secret
    return folder


def test_account_needs_a_subcommand(fake_home, fake_accounts_cli):
    with pytest.raises(SystemExit) as exc:
        main(["account"])
    assert exc.value.code == 2


def test_account_commands_do_not_need_a_sync_folder(fake_home, fake_accounts_cli):
    # No dotsync.toml anywhere: accounts are separate from sync.
    _saved(fake_home, fake_accounts_cli, "bob", BOB, "b1")
    assert main(["account", "rename", "bob", "밥", "--json"]) == 0


UNSAVED = {"accountUuid": "uuid-u", "emailAddress": "unsaved@example.com"}


def _json_out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_account_login_json_keeps_claudes_output_off_stdout(fake_home, fake_accounts_cli, capsys):
    fake_accounts_cli.browser = {"oauthAccount": BOB, "secret": "b1"}

    assert main(["account", "login", "bob", "--json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "name": "bob", "label": "bob", "email": "bob@example.com", "logged_in": True,
    }
    assert "Opening browser" in captured.err


def test_account_use_json(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a2")

    assert main(["account", "use", "bob", "--json"]) == 0

    assert _json_out(capsys)["name"] == "bob"
    assert cli.keychain[SEAT] == "b1"


def test_account_use_json_never_asks_about_an_unsaved_login(fake_home, fake_accounts_cli, monkeypatch, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, UNSAVED, "u1")
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked a question"))

    assert main(["account", "use", "bob", "--json"]) == 1

    assert _json_out(capsys) == {"error": {
        "code": "unsaved_login",
        "message": "the login Claude uses now (unsaved@example.com) is not saved in dotsync — switching would drop it",
        "email": "unsaved@example.com",
    }}
    assert cli.keychain[SEAT] == "u1"


def test_account_use_json_yes_drops_the_unsaved_login(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, UNSAVED, "u1")

    assert main(["account", "use", "bob", "--yes", "--json"]) == 0
    assert cli.keychain[SEAT] == "b1"


def test_account_remove_json(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a1")

    assert main(["account", "remove", "bob", "--json"]) == 0
    assert _json_out(capsys) == {"removed": "bob"}


def test_account_json_errors_carry_the_code(fake_home, fake_accounts_cli, capsys):
    assert main(["account", "use", "ghost", "--json"]) == 1
    error = _json_out(capsys)["error"]
    assert error["code"] == "not_found"
    assert "ghost" in error["message"]


def test_account_json_unexpected_errors_are_failed(fake_home, fake_accounts_cli, monkeypatch, capsys):
    from dotsync import accounts

    def boom():
        raise OSError("disk on fire")

    monkeypatch.setattr(accounts, "usage", boom)
    assert main(["account", "usage", "--json"]) == 1
    assert _json_out(capsys) == {"error": {"code": "failed", "message": "disk on fire"}}


def test_account_use_json_is_busy_while_another_command_runs(fake_home, fake_accounts_cli, monkeypatch, capsys):
    from dotsync import accounts

    monkeypatch.setattr(accounts, "LOCK_WAIT_SECONDS", 0.2)
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a2")

    with accounts.locked():
        assert main(["account", "use", "bob", "--json"]) == 1

    assert _json_out(capsys)["error"]["code"] == "busy"
    assert cli.keychain[SEAT] == "a2"


def test_account_rename_json(fake_home, fake_accounts_cli, capsys):
    _saved(fake_home, fake_accounts_cli, "bob", BOB, "b1")

    assert main(["account", "rename", "bob", "밥", "--json"]) == 0

    assert _json_out(capsys) == {
        "name": "bob", "label": "밥", "email": "bob@example.com", "logged_in": True,
    }


def test_account_login_turns_sigterm_into_a_cancel(fake_home, fake_accounts_cli, monkeypatch, capsys):
    import signal

    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.__setitem__(sig, handler))
    fake_accounts_cli.login_interrupted = True

    assert main(["account", "login", "bob", "--json"]) == 1

    assert installed[signal.SIGTERM] is signal.default_int_handler
    assert _json_out(capsys)["error"]["code"] == "cancelled"
    assert not (fake_home / ".claude-accounts" / "bob").exists()


def test_other_account_commands_keep_the_default_sigterm(fake_home, fake_accounts_cli, monkeypatch):
    import signal

    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.__setitem__(sig, handler))
    main(["account", "remove", "ghost", "--json"])
    assert installed == {}


FIVE = (31, "2026-10-04T15:39:00+00:00")
WEEK = (15, "2026-10-09T05:59:00+00:00")


def test_account_usage_json(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _seat(fake_home, cli, ALICE, "a2")
    cli.reply_usage("seat", FIVE, WEEK)

    assert main(["account", "usage", "--json"]) == 0

    out = _json_out(capsys)
    assert out["active"] == "alice"
    assert out["accounts"][0]["five_hour"] == {"percent": 31, "resets_at": "2026-10-04T15:39:00Z"}


@pytest.fixture
def utc_local_time():
    import os
    import time

    saved = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def test_account_usage_json_without_claude(fake_home, fake_accounts_cli, monkeypatch, capsys):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert main(["account", "usage", "--json"]) == 1
    assert _json_out(capsys)["error"]["code"] == "claude_missing"


def test_account_json_reports_an_interrupted_command_as_cancelled(fake_home, fake_accounts_cli, monkeypatch, capsys):
    import signal

    from dotsync import accounts

    monkeypatch.setattr(signal, "signal", lambda sig, handler: None)

    def interrupted(name):
        raise KeyboardInterrupt

    monkeypatch.setattr(accounts, "login", interrupted)

    assert main(["account", "login", "bob", "--json"]) == 1
    assert _json_out(capsys)["error"]["code"] == "cancelled"
