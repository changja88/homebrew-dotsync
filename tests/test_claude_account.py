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


def test_add_rejects_same_account_under_second_name(fake_home, fake_keychain):
    """The same Claude account must not be registerable under two names. `add`
    snapshots whoever is currently live, so `add other` while logged in as an
    already-saved account would silently duplicate it (same accountUuid, two
    names). Match by accountUuid, like login's dupe-guard.
    """
    _seed_live(fake_home, fake_keychain, uid="AAA")
    ca.add("work")
    # still logged in as the SAME account (uid AAA) — a second name must be refused
    with pytest.raises(ca.AccountError):
        ca.add("work-again")
    assert [i.name for i in ca.list_accounts()] == ["work"]


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


def test_use_writes_back_outgoing_rotated_live_token(fake_home, fake_keychain):
    """Switching away must re-snapshot the outgoing account's CURRENT live token
    into its own slot. Claude rotates the refresh token whenever it renews the
    access token, invalidating the one we saved; without a write-back, switching
    back later reinstalls a revoked refresh token -> 'Not logged in'.
    """
    _two_accounts(fake_home, fake_keychain)  # live = personal (uid PPP)
    ca.use("work")  # live -> work (accessToken WORK), identity uid WWW

    # Claude renews the live token in the background: access + refresh both rotate.
    live = json.loads(keychain.read_secret(ca.LIVE_SERVICE, LOGIN))
    live["claudeAiOauth"]["accessToken"] = "WORK-NEW"
    live["claudeAiOauth"]["refreshToken"] = "WORK-NEW-r"
    fake_keychain.set(ca.LIVE_SERVICE, LOGIN, json.dumps(live))

    ca.use("personal")  # switch away from work

    # work's saved slot must now hold the ROTATED token, not the stale one.
    store = json.loads(keychain.read_secret(ca.STORE_SERVICE, ca.STORE_ACCOUNT))
    saved = json.loads(store["accounts"]["work"]["credentials"])
    assert saved["claudeAiOauth"]["accessToken"] == "WORK-NEW"
    assert saved["claudeAiOauth"]["refreshToken"] == "WORK-NEW-r"


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


def test_login_runs_claude_login_then_saves_new_account(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain, blob=_blob2("G1", "D1"), uid="G1")
    ca.add("gmail1")  # already on gmail1

    def fake_login():  # simulates the browser flow landing on gmail2
        _seed_live(fake_home, fake_keychain, blob=_blob2("G2", "D2"), uid="G2")
        return 0

    ca.login("gmail2", login_fn=fake_login)

    infos = {i.name: i for i in ca.list_accounts()}
    assert set(infos) == {"gmail1", "gmail2"}
    assert infos["gmail2"].active is True


def test_login_writes_back_outgoing_rotated_token(fake_home, fake_keychain):
    """`login` overwrites the live token with the newly-authed account. The
    OUTGOING account's live token may have rotated since it was saved, so snapshot
    it back into its own slot first — otherwise it keeps a revoked token and
    breaks the next time you switch to it.
    """
    _seed_live(fake_home, fake_keychain, blob=_blob2("G1", "D1"), uid="G1")
    ca.add("gmail1")  # store[gmail1] = G1 (initial)

    # Claude rotates gmail1's live token in the background before the new login.
    live = json.loads(keychain.read_secret(ca.LIVE_SERVICE, LOGIN))
    live["claudeAiOauth"]["accessToken"] = "G1-NEW"
    live["claudeAiOauth"]["refreshToken"] = "G1-NEW-r"
    fake_keychain.set(ca.LIVE_SERVICE, LOGIN, json.dumps(live))

    def fake_login():  # browser lands on gmail2
        _seed_live(fake_home, fake_keychain, blob=_blob2("G2", "D2"), uid="G2")
        return 0

    ca.login("gmail2", login_fn=fake_login)

    store = json.loads(keychain.read_secret(ca.STORE_SERVICE, ca.STORE_ACCOUNT))
    saved = json.loads(store["accounts"]["gmail1"]["credentials"])
    assert saved["claudeAiOauth"]["accessToken"] == "G1-NEW"
    assert saved["claudeAiOauth"]["refreshToken"] == "G1-NEW-r"


def test_login_dupe_guard_refuses_same_account_under_new_name(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain, uid="G1")
    ca.add("gmail1")

    def fake_login():  # browser re-authed the SAME account
        _seed_live(fake_home, fake_keychain, uid="G1")
        return 0

    with pytest.raises(ca.AccountError):
        ca.login("gmail2", login_fn=fake_login)
    # no duplicate created
    assert [i.name for i in ca.list_accounts()] == ["gmail1"]


def test_login_aborts_when_claude_login_fails(fake_home, fake_keychain):
    with pytest.raises(ca.AccountError):
        ca.login("gmail2", login_fn=lambda: 1)
    assert ca.list_accounts() == []


def test_login_without_name_derives_from_email(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain, uid="G1", email="dev@numchida.com")
    ca.add("gmail1")

    def fake_login():
        _seed_live(fake_home, fake_keychain, uid="G2", email="work@numchida.com")
        return 0

    saved = ca.login(None, login_fn=fake_login)

    assert saved == "work"  # derived from work@numchida.com local-part
    assert "work" in {i.name for i in ca.list_accounts()}


def test_login_derived_name_avoids_collision(fake_home, fake_keychain):
    # an account already named 'work' exists; a different login with a 'work@'
    # email must not clobber it — it gets a distinct derived name.
    _seed_live(fake_home, fake_keychain, uid="G1", email="work@a.com")
    ca.add("work")

    def fake_login():
        _seed_live(fake_home, fake_keychain, uid="G2", email="work@b.com")
        return 0

    saved = ca.login(None, login_fn=fake_login)
    assert saved != "work"
    assert {"work", saved} <= {i.name for i in ca.list_accounts()}


def test_login_refuses_when_still_same_account(fake_home, fake_keychain):
    # currently on gmail1 (not even saved); the browser re-auths the SAME account
    _seed_live(fake_home, fake_keychain, uid="G1")

    def fake_login():
        _seed_live(fake_home, fake_keychain, uid="G1")  # unchanged
        return 0

    with pytest.raises(ca.AccountError):
        ca.login("gmail2", login_fn=fake_login)
    assert ca.list_accounts() == []


def test_login_rejects_duplicate_name(fake_home, fake_keychain):
    _seed_live(fake_home, fake_keychain, uid="G1")
    ca.add("gmail1")
    with pytest.raises(ca.AccountError):
        ca.login("gmail1", login_fn=lambda: 0)
