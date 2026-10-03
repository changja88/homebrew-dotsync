import json
import stat
from pathlib import Path

import pytest

from dotsync import accounts
from dotsync.accounts import AccountError, UnsavedLoginError

SEAT = "Claude Code-credentials"
ALICE = {"accountUuid": "uuid-a", "emailAddress": "alice@example.com"}
BOB = {"accountUuid": "uuid-b", "emailAddress": "bob@example.com"}
UNSAVED = {"accountUuid": "uuid-u", "emailAddress": "unsaved@example.com"}


def _seat(home: Path, cli, account: dict, secret: str) -> None:
    """Put a login in Claude's default slot, as `/login` would."""
    cli.keychain[SEAT] = secret
    doc = {"numStartups": 3, "oauthAccount": account, "projects": {"/w": {"x": 1}}}
    path = home / ".claude.json"
    path.write_text(json.dumps(doc, indent=2))
    path.chmod(0o600)


def _saved(home: Path, cli, name: str, account: dict, secret: str) -> Path:
    """An account saved earlier with `dotsync account login`."""
    folder = home / ".claude-accounts" / name
    folder.mkdir(parents=True)
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": account}, indent=2))
    cli.keychain[cli.service_for(folder)] = secret
    return folder


def _doc(path: Path) -> dict:
    return json.loads(path.read_text())


def test_service_for_matches_claude_codes_keychain_name():
    # Observed on a real machine: Claude Code keys the Keychain entry of a
    # CLAUDE_CONFIG_DIR by the first 8 hex digits of the path's SHA-256.
    folder = Path("/Users/hyun/.claude-accounts/changja00")
    assert accounts.service_for(folder) == "Claude Code-credentials-44411801"


@pytest.mark.parametrize("name", ["alice", "changja00", "a.b-c_d"])
def test_validate_name_accepts_simple_names(name):
    assert accounts.validate_name(name) == name


@pytest.mark.parametrize("name", ["", "a/b", ".", "..", "a b", ".hidden", "default"])
def test_validate_name_rejects_unsafe_or_reserved_names(name):
    with pytest.raises(AccountError):
        accounts.validate_name(name)


def test_saved_accounts_lists_account_folders_sorted(fake_home):
    root = fake_home / ".claude-accounts"
    (root / "bob").mkdir(parents=True)
    (root / "alice").mkdir()
    (root / ".tmp").mkdir()
    (root / "notes.txt").write_text("x")
    assert accounts.saved_accounts() == ["alice", "bob"]


def test_saved_accounts_is_empty_without_the_accounts_folder(fake_home):
    assert accounts.saved_accounts() == []


def test_login_saves_the_browser_account_in_its_own_folder(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _seat(fake_home, cli, ALICE, "seat-secret")
    cli.browser = {"oauthAccount": BOB, "secret": "bob-secret"}

    email = accounts.login("bob")

    folder = fake_home / ".claude-accounts" / "bob"
    assert email == "bob@example.com"
    assert cli.keychain[cli.service_for(folder)] == "bob-secret"
    assert _doc(folder / ".claude.json")["oauthAccount"] == BOB
    assert cli.keychain[SEAT] == "seat-secret"
    assert _doc(fake_home / ".claude.json")["oauthAccount"] == ALICE


def test_login_raises_when_claude_login_fails(fake_home, fake_accounts_cli):
    fake_accounts_cli.login_fails = True
    with pytest.raises(AccountError, match="login"):
        accounts.login("bob")


def test_login_refuses_the_account_in_use(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _seat(fake_home, cli, ALICE, "a2")
    _saved(fake_home, cli, "alice", ALICE, "a1")
    cli.browser = {"oauthAccount": ALICE, "secret": "a3"}
    with pytest.raises(AccountError, match="in use"):
        accounts.login("alice")
    assert not any(Path(c[0]).name == "claude" for c in cli.calls)


def test_login_raises_when_claude_is_not_installed(fake_home, fake_accounts_cli, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(AccountError, match="claude"):
        accounts.login("bob")


def test_active_account_is_the_saved_account_in_the_seat(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, BOB, "b2")
    assert accounts.active_account() == "bob"


def test_active_account_is_none_for_an_unsaved_login(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _seat(fake_home, cli, UNSAVED, "u1")
    assert accounts.active_account() is None


def test_use_puts_the_saved_login_in_the_seat(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a2")

    assert accounts.use("bob") is True

    assert cli.keychain[SEAT] == "b1"
    seat_doc = _doc(fake_home / ".claude.json")
    assert seat_doc["oauthAccount"] == BOB
    assert seat_doc["numStartups"] == 3 and seat_doc["projects"] == {"/w": {"x": 1}}
    assert accounts.active_account() == "bob"


def test_use_returns_the_latest_seat_login_to_its_account(fake_home, fake_accounts_cli):
    # Claude refreshes tokens while in use, and a used refresh token stops
    # working — so the seat's current secret, not the stale saved copy, must
    # go back to the account that was in use.
    cli = fake_accounts_cli
    alice = _saved(fake_home, cli, "alice", ALICE, "a1-stale")
    _saved(fake_home, cli, "bob", BOB, "b1")
    refreshed = dict(ALICE, organizationRole="admin")
    _seat(fake_home, cli, refreshed, "a2-latest")

    accounts.use("bob")

    assert cli.keychain[cli.service_for(alice)] == "a2-latest"
    assert _doc(alice / ".claude.json")["oauthAccount"] == refreshed


def test_use_refuses_to_overwrite_an_unsaved_login(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, UNSAVED, "u1")

    with pytest.raises(UnsavedLoginError) as err:
        accounts.use("bob")

    assert err.value.email == "unsaved@example.com"
    assert cli.keychain[SEAT] == "u1"
    assert _doc(fake_home / ".claude.json")["oauthAccount"] == UNSAVED


def test_use_overwrites_an_unsaved_login_when_allowed(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, UNSAVED, "u1")

    accounts.use("bob", allow_unsaved_overwrite=True)

    assert cli.keychain[SEAT] == "b1"


def test_use_fills_an_empty_seat(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")

    accounts.use("bob")

    assert cli.keychain[SEAT] == "b1"
    assert _doc(fake_home / ".claude.json")["oauthAccount"] == BOB


def test_use_of_the_account_in_use_changes_nothing(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, BOB, "b2")

    assert accounts.use("bob") is False
    assert cli.keychain[SEAT] == "b2"
    assert cli.stdin == []


def test_use_requires_a_logged_in_account(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    (fake_home / ".claude-accounts" / "bob").mkdir(parents=True)
    _seat(fake_home, cli, ALICE, "a1")
    with pytest.raises(AccountError, match="dotsync account login bob"):
        accounts.use("bob")
    assert cli.keychain[SEAT] == "a1"


def test_use_passes_secrets_on_stdin_not_the_command_line(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "alice", ALICE, "a1")
    _saved(fake_home, cli, "bob", BOB, "b1-secret")
    _seat(fake_home, cli, ALICE, "a2-secret")

    accounts.use("bob")

    assert "b1-secret" not in cli.argv_text() and "a2-secret" not in cli.argv_text()
    assert "b1-secret".encode().hex() not in cli.argv_text()
    assert any("b1-secret".encode().hex() in s for s in cli.stdin)


def test_use_fails_loudly_when_the_keychain_write_does_not_stick(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    cli.ignore_writes = True
    with pytest.raises(AccountError, match="Keychain"):
        accounts.use("bob")
    assert not (fake_home / ".claude.json").exists()


def test_use_keeps_the_claude_json_format_and_permissions(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, dict(ALICE, displayName="앨리스"), "a1")
    _saved(fake_home, cli, "alice", ALICE, "a0")

    accounts.use("bob")

    path = fake_home / ".claude.json"
    text = path.read_text()
    assert text.startswith('{\n  "') and not text.endswith("\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    alice_doc = (fake_home / ".claude-accounts" / "alice" / ".claude.json").read_text()
    assert "앨리스" in alice_doc


def test_remove_logs_out_and_deletes_the_folder(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    bob = _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a1")

    accounts.remove("bob")

    assert not bob.exists()
    assert cli.service_for(bob) not in cli.keychain
    assert cli.keychain[SEAT] == "a1"


def test_remove_refuses_the_account_in_use(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    bob = _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, BOB, "b2")
    with pytest.raises(AccountError, match="in use"):
        accounts.remove("bob")
    assert bob.exists()


def test_remove_of_an_unknown_account_raises(fake_home, fake_accounts_cli):
    with pytest.raises(AccountError, match="no saved account"):
        accounts.remove("ghost")


def test_account_email_reads_the_saved_account(fake_home, fake_accounts_cli):
    cli = fake_accounts_cli
    _saved(fake_home, cli, "bob", BOB, "b1")
    _seat(fake_home, cli, ALICE, "a1")
    assert accounts.account_email("bob") == "bob@example.com"
    assert accounts.seat_email() == "alice@example.com"
    assert accounts.is_logged_in("bob") is True
    assert accounts.account_email("ghost") is None
