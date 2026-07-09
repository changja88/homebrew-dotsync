"""Per-tab Claude account tokens.

dotsync maps account names to long-lived subscription tokens
(`CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`) so different terminal
tabs can run different Claude accounts at once. A tab account is nothing more
than a NAME plus a TOKEN — dotsync stores the mapping and the launcher injects
the right token per tab.

The whole store lives in ONE consolidated macOS Keychain item (never a plaintext
file). It is deliberately introspection-free: `setup-token` values are opaque
and carry no local identity, so the name→token binding is user-asserted.

This module does NOT switch the machine-global Claude login or touch
`~/.claude.json` — that is Claude Code's own `claude auth login`. dotsync only
ever reads/stores its own Keychain item and hands tokens to the launcher.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from dotsync import keychain

# Keychain item holding dotsync's whole tab-account store.
STORE_SERVICE = "dotsync-claude-account"
STORE_ACCOUNT = "store"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AccountError(RuntimeError):
    """A user-facing account operation failed (bad name, missing token, etc.)."""


@dataclass(frozen=True)
class AccountInfo:
    name: str


# --- store (single consolidated Keychain item) -------------------------------

def _empty_store() -> dict:
    return {"accounts": {}}


def _load_store() -> dict:
    """Load the store. Fails CLOSED: a real Keychain read error propagates (so a
    later write can't persist an empty store over real data); only a genuinely
    absent item yields an empty store."""
    raw = keychain.read_secret(STORE_SERVICE, STORE_ACCOUNT)
    if not raw:
        return _empty_store()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AccountError(f"account store is corrupted (invalid JSON)") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("accounts"), dict):
        raise AccountError("account store is malformed")
    return doc


def _save_store(store: dict) -> None:
    keychain.write_secret(
        STORE_SERVICE, STORE_ACCOUNT, json.dumps(store, ensure_ascii=False)
    )


# --- validation --------------------------------------------------------------

def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise AccountError(
            f"invalid account name `{name}` "
            "(use letters, digits, dot, dash, underscore)"
        )


def _validate_token(token: str) -> str:
    """Normalize + validate a `claude setup-token` value.

    These are OAuth *subscription* tokens (`sk-ant-oat...`), NOT metered API keys
    (`sk-ant-api...`). Reject anything not shaped like an oat token so a stray
    API key can't be stored and silently divert billing.
    """
    stripped = (token or "").strip()
    if not stripped.startswith("sk-ant-oat"):
        raise AccountError(
            "invalid token — expected a `claude setup-token` value "
            "(starts with `sk-ant-oat`), not an API key"
        )
    return stripped


# --- operations --------------------------------------------------------------

def set_token(name: str, token: str) -> None:
    """Save (or replace) the per-tab token for `name`, creating it if needed.

    No login/registration required — a tab account IS just a name plus a token.
    Run `claude setup-token` while signed in to claude.ai as that account; the
    binding is user-asserted (the token is opaque and leaves no local trace).
    """
    token = _validate_token(token)
    _validate_name(name)
    store = _load_store()
    store["accounts"][name] = {"token": token}
    _save_store(store)


def token_of(name: str) -> str | None:
    """The saved token for `name`. Raises `AccountError` if `name` is unknown, so
    callers can tell "no such account" from other states."""
    store = _load_store()
    rec = store["accounts"].get(name)
    if rec is None:
        raise AccountError(f"no saved account `{name}`")
    return rec.get("token")


def list_accounts() -> list[AccountInfo]:
    """Saved tab accounts, sorted by name."""
    store = _load_store()
    return [AccountInfo(name=name) for name in sorted(store["accounts"])]


def remove(name: str) -> None:
    """Forget a saved tab account (token and all)."""
    store = _load_store()
    if name not in store["accounts"]:
        raise AccountError(f"no saved account `{name}`")
    del store["accounts"][name]
    _save_store(store)
