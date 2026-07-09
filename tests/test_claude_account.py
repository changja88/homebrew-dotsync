"""Tests for per-tab Claude account tokens (name -> setup-token).

An in-memory `fake_keychain` fixture stands in for the real `security` CLI. A
tab account is just a name plus a token; there is no global-login machinery.
"""
import json

import pytest

from dotsync import claude_account as ca
from dotsync import keychain

TOKEN_A = "sk-ant-oat01-" + "A" * 40
TOKEN_B = "sk-ant-oat01-" + "B" * 40


def test_set_token_creates_account_and_token_of_returns(fake_home, fake_keychain):
    ca.set_token("work", TOKEN_A)
    assert ca.token_of("work") == TOKEN_A
    assert [i.name for i in ca.list_accounts()] == ["work"]


def test_set_token_needs_no_prior_registration(fake_home, fake_keychain):
    # empty store, no add/login step anywhere — set just works
    ca.set_token("changja00", TOKEN_A)
    ca.set_token("numchida", TOKEN_B)
    assert {i.name for i in ca.list_accounts()} == {"changja00", "numchida"}


def test_set_token_replaces_existing(fake_home, fake_keychain):
    ca.set_token("work", TOKEN_A)
    ca.set_token("work", TOKEN_B)
    assert ca.token_of("work") == TOKEN_B
    assert [i.name for i in ca.list_accounts()] == ["work"]  # not duplicated


def test_set_token_trims_whitespace(fake_home, fake_keychain):
    ca.set_token("work", f"  {TOKEN_A}\n")
    assert ca.token_of("work") == TOKEN_A


def test_set_token_rejects_malformed_or_api_key(fake_home, fake_keychain):
    for bad in ("", "   ", "not-a-token", "sk-ant-api03-xxx"):
        with pytest.raises(ca.AccountError):
            ca.set_token("work", bad)
    assert ca.list_accounts() == []  # nothing created by a rejected token


def test_set_token_rejects_invalid_name(fake_home, fake_keychain):
    for bad in ("", "bad name", "a/b"):
        with pytest.raises(ca.AccountError):
            ca.set_token(bad, TOKEN_A)


def test_token_of_unknown_account_raises(fake_home, fake_keychain):
    ca.set_token("work", TOKEN_A)
    with pytest.raises(ca.AccountError):
        ca.token_of("ghost")


def test_list_accounts_sorted(fake_home, fake_keychain):
    ca.set_token("zeta", TOKEN_A)
    ca.set_token("alpha", TOKEN_B)
    assert [i.name for i in ca.list_accounts()] == ["alpha", "zeta"]


def test_remove_deletes(fake_home, fake_keychain):
    ca.set_token("work", TOKEN_A)
    ca.remove("work")
    assert ca.list_accounts() == []


def test_remove_unknown_raises(fake_home, fake_keychain):
    with pytest.raises(ca.AccountError):
        ca.remove("ghost")


def test_load_store_fails_closed_on_keychain_error(fake_home, fake_keychain):
    """A REAL keychain read error must NOT be treated as an empty store — else a
    write would persist emptiness over real data. set_token must abort, leaving
    the store intact."""
    ca.set_token("work", TOKEN_A)
    fake_read = keychain.read_secret  # the fake_keychain in-memory reader

    def boom(service, account):
        raise keychain.KeychainError("keychain locked (exit 51)")

    keychain.read_secret = boom
    try:
        with pytest.raises(keychain.KeychainError):
            ca.set_token("newtab", TOKEN_B)
    finally:
        keychain.read_secret = fake_read  # restore only the fake read

    # the original account must be untouched (no wipe)
    assert ca.token_of("work") == TOKEN_A
    assert "newtab" not in {i.name for i in ca.list_accounts()}


def test_store_shape_is_name_to_token(fake_home, fake_keychain):
    ca.set_token("work", TOKEN_A)
    raw = keychain.read_secret(ca.STORE_SERVICE, ca.STORE_ACCOUNT)
    doc = json.loads(raw)
    assert doc == {"accounts": {"work": {"token": TOKEN_A}}}  # no active/__previous__/creds
