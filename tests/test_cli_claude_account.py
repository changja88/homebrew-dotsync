"""End-to-end CLI dispatch tests for `dotsync claude account ...`.

A tab account is a name + a `claude setup-token` value. These drive the real
command handler + claude_account against the in-memory `fake_keychain`, with no
dotsync.toml (account commands are config-free).
"""
from dotsync.cli import main

TOKEN = "sk-ant-oat01-" + "Z" * 40
TOKEN2 = "sk-ant-oat01-" + "Y" * 40


def test_set_creates_account_and_list_shows_it(fake_home, fake_keychain, capsys):
    assert main(["claude", "account", "set", "work", TOKEN]) == 0
    rc = main(["claude", "account", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "work" in out


def test_set_accepts_token_as_argument_no_prompt(fake_home, fake_keychain, monkeypatch):
    from dotsync import claude_account as ca

    def _boom(*a, **k):
        raise AssertionError("getpass must not be called when token is passed")

    monkeypatch.setattr("getpass.getpass", _boom)
    assert main(["claude", "account", "set", "work", TOKEN]) == 0
    assert ca.token_of("work") == TOKEN


def test_set_prompts_hidden_when_token_omitted(fake_home, fake_keychain, monkeypatch, capsys):
    from dotsync import claude_account as ca

    monkeypatch.setattr("getpass.getpass", lambda *a, **k: TOKEN)
    rc = main(["claude", "account", "set", "work"])
    assert rc == 0
    assert ca.token_of("work") == TOKEN
    assert TOKEN not in capsys.readouterr().out  # never echoed to stdout


def test_set_needs_no_prior_login(fake_home, fake_keychain):
    from dotsync import claude_account as ca

    # brand-new name, no add/login anywhere
    assert main(["claude", "account", "set", "changja00", TOKEN]) == 0
    assert ca.token_of("changja00") == TOKEN


def test_set_rejects_malformed_token(fake_home, fake_keychain):
    assert main(["claude", "account", "set", "work", "not-a-token"]) != 0


def test_list_empty_hints_set(fake_home, fake_keychain, capsys):
    rc = main(["claude", "account", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "set" in out.lower()


def test_list_porcelain_is_names_only(fake_home, fake_keychain, capsys):
    main(["claude", "account", "set", "work", TOKEN])
    main(["claude", "account", "set", "home", TOKEN2])
    capsys.readouterr()
    rc = main(["claude", "account", "list", "--porcelain"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sorted(l for l in out.splitlines() if l.strip()) == ["home", "work"]


def test_remove(fake_home, fake_keychain, capsys):
    main(["claude", "account", "set", "work", TOKEN])
    assert main(["claude", "account", "remove", "work"]) == 0
    capsys.readouterr()
    main(["claude", "account", "list", "--porcelain"])
    assert capsys.readouterr().out.strip() == ""


def test_remove_unknown_nonzero(fake_home, fake_keychain):
    assert main(["claude", "account", "remove", "ghost"]) != 0


def test_env_prints_token_only(fake_home, fake_keychain, capsys):
    main(["claude", "account", "set", "work", TOKEN])
    capsys.readouterr()
    rc = main(["claude", "account", "env", "work"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == TOKEN


def test_env_no_account_exit_3(fake_home, fake_keychain, capsys):
    rc = main(["claude", "account", "env", "ghost"])
    assert rc == 3


def test_pick_single_account_prints_name(fake_home, fake_keychain, capsys):
    main(["claude", "account", "set", "work", TOKEN])
    capsys.readouterr()
    rc = main(["claude", "account", "pick"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "work"


def test_pick_no_accounts_nonzero(fake_home, fake_keychain, capsys):
    rc = main(["claude", "account", "pick"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == ""


def test_pick_multi_account_nontty_nonzero(fake_home, fake_keychain, capsys):
    main(["claude", "account", "set", "work", TOKEN])
    main(["claude", "account", "set", "home", TOKEN2])
    capsys.readouterr()
    rc = main(["claude", "account", "pick"])  # pytest streams aren't TTYs
    assert rc == 1
    assert capsys.readouterr().out.strip() == ""
