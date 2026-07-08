"""Tests for Claude account (auth) switching backed by the macOS Keychain.

An in-memory `fake_keychain` fixture stands in for the real `security` CLI so
these are fast unit tests. The live Claude credential lives at
(LIVE_SERVICE, login-user); dotsync's own store is a single consolidated
Keychain item holding every saved account plus the active pointer.
"""
import getpass
import json

import pytest

from dotsync import claude_account as ca
from dotsync import keychain

LOGIN = getpass.getuser()


def _blob(sub="max"):
    return json.dumps(
        {
            "claudeAiOauth": {"accessToken": "sk-ant-oat-A", "subscriptionType": sub},
            "designOauth": {"accessToken": "sk-ant-oat-D", "clientId": "cid"},
        }
    )


def _seed_live(fake_home, fake_keychain, *, blob=None, email="a@x.io", uid="U-AAA"):
    """Put a live Claude credential in the keychain + identity on disk."""
    fake_keychain.set(ca.LIVE_SERVICE, LOGIN, blob or _blob())
    (fake_home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {"emailAddress": email, "accountUuid": uid},
                "userID": "user-" + uid,
                "mcpServers": {"stitch": {"url": "x"}},
            }
        )
    )


def test_add_captures_current_account_and_lists_it_active(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain)

    ca.add("work")

    infos = ca.list_accounts()
    assert [i.name for i in infos] == ["work"]
    assert infos[0].active is True
    assert infos[0].subscription == "max"


def test_add_without_live_credential_raises(fake_home, fake_keychain):
    # no live credential seeded
    (fake_home / ".claude.json").write_text(json.dumps({"oauthAccount": {}}))
    with pytest.raises(ca.AccountError):
        ca.add("work")


def test_add_rejects_reserved_and_invalid_names(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain)
    for bad in ("", "__previous__", "a/b", "with space"):
        with pytest.raises(ca.AccountError):
            ca.add(bad)


def test_add_duplicate_name_raises(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain)
    ca.add("work")
    with pytest.raises(ca.AccountError):
        ca.add("work")


def test_remove_deletes_and_clears_active(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain)
    ca.add("work")
    ca.remove("work")
    assert ca.list_accounts() == []


def test_remove_unknown_name_raises(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain)
    with pytest.raises(ca.AccountError):
        ca.remove("nope")


def test_two_accounts_only_one_active(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain, email="a@x.io", uid="AAA")
    ca.add("work")
    _seed_live(fake_home, fake_keychain, email="b@y.io", uid="BBB")
    ca.add("personal")

    infos = {i.name: i for i in ca.list_accounts()}
    assert set(infos) == {"work", "personal"}
    assert infos["personal"].active is True
    assert infos["work"].active is False


def _blob2(claude_at, design_at):
    return json.dumps(
        {
            "claudeAiOauth": {"accessToken": claude_at, "refreshToken": claude_at + "-r"},
            "designOauth": {"accessToken": design_at, "clientId": "cid"},
        }
    )


def _two_accounts(fake_home, fake_keychain):
    """work saved; then personal saved and left live. Returns nothing."""
    _seed_live(fake_home, fake_keychain, blob=_blob2("WORK", "D-WORK"), uid="WWW")
    ca.add("work")
    _seed_live(fake_home, fake_keychain, blob=_blob2("PERS", "D-PERS"), uid="PPP")
    ca.add("personal")


def test_use_restores_token_and_identity_preserving_designoauth(fake_home, fake_keychain):
    _two_accounts(fake_home, fake_keychain)  # live = personal

    ca.use("work")

    live = json.loads(keychain.read_secret(ca.LIVE_SERVICE, LOGIN))
    # token flips to work, but designOauth is the PRESERVED live (personal) one
    assert live["claudeAiOauth"]["accessToken"] == "WORK"
    assert live["designOauth"]["accessToken"] == "D-PERS"
    # on-disk identity is work's; unrelated keys preserved
    doc = json.loads((fake_home / ".claude.json").read_text())
    assert doc["oauthAccount"]["accountUuid"] == "WWW"
    assert doc["userID"] == "user-WWW"
    assert "mcpServers" in doc
    assert next(i for i in ca.list_accounts() if i.name == "work").active


def test_use_unknown_account_raises(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain)
    ca.add("work")
    with pytest.raises(ca.AccountError):
        ca.use("ghost")


def test_undo_restores_previous_account(fake_home, fake_keychain):
    _two_accounts(fake_home, fake_keychain)  # live = personal
    ca.use("work")  # live -> work, previous = personal

    ca.undo()

    live = json.loads(keychain.read_secret(ca.LIVE_SERVICE, LOGIN))
    assert live["claudeAiOauth"]["accessToken"] == "PERS"
    doc = json.loads((fake_home / ".claude.json").read_text())
    assert doc["oauthAccount"]["accountUuid"] == "PPP"


def test_current_matches_live_by_account_uuid(fake_home, fake_keychain):
    _two_accounts(fake_home, fake_keychain)  # live = personal
    ca.use("work")  # live is now work's identity (uuid WWW)

    cur = ca.current()
    assert cur.name == "work"
    assert cur.matched is True


def test_current_unmatched_when_live_identity_unknown(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain, uid="ZZZ")
    ca.add("work")
    # make live a different, unsaved identity
    (fake_home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"accountUuid": "OTHER"}, "userID": "u"})
    )
    cur = ca.current()
    assert cur.name is None
    assert cur.matched is False
