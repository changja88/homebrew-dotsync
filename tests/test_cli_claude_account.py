"""End-to-end CLI dispatch tests for `dotsync claude account ...`.

These drive the real command handler + claude_account against the in-memory
`fake_keychain`, with no dotsync.toml (account commands are config-free).
"""
import getpass
import json

from dotsync.cli import main

LOGIN = getpass.getuser()


def _blob(sub="max"):
    return json.dumps(
        {"claudeAiOauth": {"accessToken": "A", "subscriptionType": sub}, "designOauth": {}}
    )


def _seed_live(fake_home, fake_keychain, *, uid="AAA", sub="max"):
    from dotsync import claude_account as ca

    fake_keychain.set(ca.LIVE_SERVICE, LOGIN, _blob(sub))
    (fake_home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"accountUuid": uid, "emailAddress": "a@x"}, "userID": "u"})
    )


def test_add_then_list_shows_active(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain)
    assert main(["claude", "account", "add", "work"]) == 0
    rc = main(["claude", "account", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "work" in out


def test_list_porcelain_is_tab_separated(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain, sub="max")
    main(["claude", "account", "add", "work"])
    capsys.readouterr()
    rc = main(["claude", "account", "list", "--porcelain"])
    out = capsys.readouterr().out
    assert rc == 0
    # name<TAB>active<TAB>subscription
    line = next(l for l in out.splitlines() if l.startswith("work"))
    fields = line.split("\t")
    assert fields[0] == "work"
    assert fields[1] in ("active", "")
    assert fields[2] == "max"


def test_list_empty_hints_add(fake_home, fake_keychain, capsys):
    rc = main(["claude", "account", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "add" in out.lower()


def test_use_with_yes_switches_without_prompt(fake_home, fake_keychain, capsys):
    from dotsync import claude_account as ca

    _seed_live(fake_home, fake_keychain, uid="WWW")
    main(["claude", "account", "add", "work"])
    _seed_live(fake_home, fake_keychain, uid="PPP")
    main(["claude", "account", "add", "personal"])  # live = personal
    capsys.readouterr()

    rc = main(["claude", "account", "use", "work", "--yes"])
    assert rc == 0
    assert ca.current().name == "work"


def test_current_reports_active(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain, uid="WWW")
    main(["claude", "account", "add", "work"])
    capsys.readouterr()
    rc = main(["claude", "account", "current"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "work" in out


def test_remove_unknown_returns_nonzero(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain)
    rc = main(["claude", "account", "remove", "ghost"])
    assert rc != 0


def test_add_without_login_returns_nonzero(fake_home, fake_keychain, capsys):
    # no live credential seeded
    rc = main(["claude", "account", "add", "work"])
    assert rc != 0


def test_login_dispatches_to_account_login(fake_home, fake_keychain, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("dotsync.claude_account.login", lambda name: calls.append(name))
    rc = main(["claude", "account", "login", "gmail2"])
    assert rc == 0
    assert calls == ["gmail2"]


# --- per-tab token subcommands -------------------------------------------------

TOKEN = "sk-ant-oat01-" + "Z" * 40


def test_token_set_reads_hidden_prompt_and_saves(
    fake_home, fake_keychain, monkeypatch, capsys
):
    from dotsync import claude_account as ca

    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: TOKEN)
    capsys.readouterr()

    rc = main(["claude", "account", "token", "set", "work"])  # no token arg → prompt
    assert rc == 0
    assert ca.token_of("work") == TOKEN
    # the token itself must not be echoed to stdout
    assert TOKEN not in capsys.readouterr().out


def test_token_set_accepts_token_as_argument(
    fake_home, fake_keychain, monkeypatch, capsys
):
    """`token set <name> <token>` must work directly — no hidden prompt."""
    from dotsync import claude_account as ca

    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])

    def _boom(*a, **k):
        raise AssertionError("getpass must not be called when token is passed")

    monkeypatch.setattr("getpass.getpass", _boom)
    capsys.readouterr()

    rc = main(["claude", "account", "token", "set", "work", TOKEN])
    assert rc == 0
    assert ca.token_of("work") == TOKEN


def test_token_set_unknown_account_nonzero(fake_home, fake_keychain, monkeypatch):
    _seed_live(fake_home, fake_keychain)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: TOKEN)
    rc = main(["claude", "account", "token", "set", "ghost"])
    assert rc != 0


def test_env_prints_token_only(fake_home, fake_keychain, monkeypatch, capsys):
    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: TOKEN)
    main(["claude", "account", "token", "set", "work"])
    capsys.readouterr()

    rc = main(["claude", "account", "env", "work"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == TOKEN  # token, nothing else


def test_env_no_token_exit_1(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])  # no token yet
    capsys.readouterr()
    rc = main(["claude", "account", "env", "work"])
    assert rc == 1


def test_env_no_account_exit_3(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])
    capsys.readouterr()
    rc = main(["claude", "account", "env", "ghost"])
    assert rc == 3


def test_list_porcelain_has_token_column(fake_home, fake_keychain, monkeypatch, capsys):
    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: TOKEN)
    main(["claude", "account", "token", "set", "work"])
    capsys.readouterr()

    main(["claude", "account", "list", "--porcelain"])
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("work"))
    fields = line.split("\t")
    # existing 3 fields unchanged; token flag appended as a trailing 4th column
    assert fields[0] == "work"
    assert fields[2] == "max"
    assert fields[3] == "token"


def test_pick_single_account_prints_name(fake_home, fake_keychain, capsys):
    _seed_live(fake_home, fake_keychain)
    main(["claude", "account", "add", "work"])
    capsys.readouterr()
    rc = main(["claude", "account", "pick"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "work"  # name on stdout, nothing else


def test_pick_no_accounts_exits_nonzero(fake_home, fake_keychain, capsys):
    rc = main(["claude", "account", "pick"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == ""


def test_pick_multi_account_nontty_returns_nonzero(fake_home, fake_keychain, capsys):
    # pytest streams aren't TTYs → picker makes no selection → nonzero, no stdout
    _seed_live(fake_home, fake_keychain, uid="AAA")
    main(["claude", "account", "add", "work"])
    _seed_live(fake_home, fake_keychain, uid="BBB")
    main(["claude", "account", "add", "personal"])
    capsys.readouterr()
    rc = main(["claude", "account", "pick"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == ""
