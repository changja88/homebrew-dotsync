import json
from pathlib import Path

import pytest

from dotsync.cli import main

SEAT = "Claude Code-credentials"
ALICE = {"accountUuid": "uuid-a", "emailAddress": "alice@example.com"}
BOB = {"accountUuid": "uuid-b", "emailAddress": "bob@example.com"}


@pytest.fixture(autouse=True)
def no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")


def _seat(home: Path, cli, account: dict, secret: str) -> None:
    cli.keychain[SEAT] = secret
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": account}, indent=2))


def _saved(home: Path, cli, name: str, account: dict, secret: str) -> Path:
    folder = home / ".claude-accounts" / name
    folder.mkdir(parents=True)
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": account}, indent=2))
    cli.keychain[cli.service_for(folder)] = secret
    return folder


def test_account_login_saves_the_browser_account(fake_home, fake_accounts_cli, capsys):
    fake_accounts_cli.browser = {"oauthAccount": BOB, "secret": "b1"}

    assert main(["account", "login", "bob"]) == 0

    out = capsys.readouterr().out
    assert "bob" in out and "bob@example.com" in out
    assert "dotsync account use bob" in out


def test_account_use_switches_claude_to_the_account(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a2")

    assert main(["account", "use", "bob"]) == 0

    assert cli.keychain[SEAT] == "b1"
    assert "bob@example.com" in capsys.readouterr().out


def test_account_use_reports_an_account_already_in_use(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, BOB, "b2")

    assert main(["account", "use", "bob"]) == 0
    assert "already" in capsys.readouterr().out


def test_account_use_asks_before_dropping_an_unsaved_login(fake_home, fake_accounts_cli, monkeypatch, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, {"accountUuid": "u", "emailAddress": "u@example.com"}, "u1")
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "n")

    assert main(["account", "use", "bob"]) == 1

    assert cli.keychain[SEAT] == "u1"
    assert "u@example.com" in prompts[0]


def test_account_use_drops_an_unsaved_login_when_confirmed(fake_home, fake_accounts_cli, monkeypatch):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, {"accountUuid": "u", "emailAddress": "u@example.com"}, "u1")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert main(["account", "use", "bob"]) == 0
    assert cli.keychain[SEAT] == "b1"


def test_account_use_yes_skips_the_question(fake_home, fake_accounts_cli, monkeypatch):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, {"accountUuid": "u", "emailAddress": "u@example.com"}, "u1")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))

    assert main(["account", "use", "bob", "--yes"]) == 0
    assert cli.keychain[SEAT] == "b1"


def test_account_list_marks_the_account_in_use(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    (fake_home / ".claude-accounts" / "carol").mkdir()
    _seat(fake_home, cli, BOB, "b2")

    assert main(["account", "list"]) == 0

    lines = capsys.readouterr().out.splitlines()
    bob = next(line for line in lines if "bob@example.com" in line)
    alice = next(line for line in lines if "alice@example.com" in line)
    carol = next(line for line in lines if "carol" in line)
    assert "●" in bob and "●" not in alice
    assert "not logged in" in carol


def test_account_list_shows_an_unsaved_login_in_use(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, {"accountUuid": "u", "emailAddress": "u@example.com"}, "u1")

    assert main(["account", "list"]) == 0

    out = capsys.readouterr().out
    assert "u@example.com" in out and "not saved" in out


def test_account_list_without_accounts_explains_how_to_add_one(fake_home, fake_accounts_cli, capsys):
    assert main(["account", "list"]) == 0
    assert "dotsync account login" in capsys.readouterr().out


def test_account_remove_deletes_the_account(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    bob = _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a1")

    assert main(["account", "remove", "bob"]) == 0
    assert not bob.exists()


def test_account_errors_exit_nonzero_with_the_reason(fake_home, fake_accounts_cli, capsys):
    assert main(["account", "use", "ghost"]) == 5
    assert "dotsync account login ghost" in capsys.readouterr().err


def test_account_needs_a_subcommand(fake_home, fake_accounts_cli):
    with pytest.raises(SystemExit) as exc:
        main(["account"])
    assert exc.value.code == 2


def test_account_commands_do_not_need_a_sync_folder(fake_home, fake_accounts_cli):
    # No dotsync.toml / DOTSYNC_DIR anywhere: accounts are separate from sync.
    assert main(["account", "list"]) == 0


UNSAVED = {"accountUuid": "uuid-u", "emailAddress": "unsaved@example.com"}


def _json_out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_account_list_json(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a2")

    assert main(["account", "list", "--json"]) == 0

    assert _json_out(capsys) == {
        "active": "alice",
        "seat": {"email": "alice@example.com", "saved": True},
        "accounts": [
            {"name": "alice", "label": "alice", "email": "alice@example.com", "logged_in": True},
            {"name": "bob", "label": "bob", "email": "bob@example.com", "logged_in": True},
        ],
    }


def test_account_list_json_with_an_unsaved_login_and_a_lost_one(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    (fake_home / ".claude-accounts" / "bob").mkdir(parents=True)
    _seat(fake_home, cli, UNSAVED, "u1")

    assert main(["account", "list", "--json"]) == 0

    out = _json_out(capsys)
    assert out["active"] is None
    assert out["seat"] == {"email": "unsaved@example.com", "saved": False}
    assert out["accounts"] == [{"name": "bob", "label": "bob", "email": None, "logged_in": False}]


def test_account_list_json_without_any_login(fake_home, fake_accounts_cli, capsys):
    assert main(["account", "list", "--json"]) == 0
    assert _json_out(capsys) == {"active": None, "seat": None, "accounts": []}


def test_account_list_json_with_a_corrupt_claude_json(fake_home, fake_accounts_cli, capsys):
    cli = fake_accounts_cli
    folder = _saved(fake_home, cli, "bob", BOB, "b1")
    (folder / ".claude.json").write_text("{")

    assert main(["account", "list", "--json"]) == 0
    assert _json_out(capsys)["accounts"] == [
        {"name": "bob", "label": "bob", "email": None, "logged_in": True}
    ]


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

    monkeypatch.setattr(accounts, "snapshot", boom)
    assert main(["account", "list", "--json"]) == 1
    assert _json_out(capsys) == {"error": {"code": "failed", "message": "disk on fire"}}
