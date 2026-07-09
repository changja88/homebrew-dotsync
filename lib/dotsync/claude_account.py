"""Claude Code account (auth) switching.

Claude Code authenticates from two places that must stay consistent:
  1. the macOS Keychain item (service "Claude Code-credentials") holding the
     OAuth token blob (`claudeAiOauth` + `designOauth`), and
  2. `~/.claude.json`'s on-disk identity (`oauthAccount` + `userID`), which is
     what `claude auth status` reports.

Switching accounts therefore swaps the token AND the on-disk identity together;
swapping only the token leaves a split-brain (disk says A, token says B).
`designOauth` is left untouched — it carries its own `clientId` and is not part
of the Claude account identity.

Because Claude rotates the refresh token on every access-token renewal (revoking
the previous one), switching AWAY from an account (`use`/`login`) first writes its
CURRENT live token back into its saved slot — otherwise switching back later would
reinstall a revoked credential and Claude reports "Not logged in".

Every saved account lives in ONE consolidated Keychain item (atomic
read-modify-write) — never a plaintext file. Nothing here writes tokens to disk.
"""
from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dotsync import keychain

# Keychain services -----------------------------------------------------------
LIVE_SERVICE = "Claude Code-credentials"  # what Claude Code reads
STORE_SERVICE = "dotsync-claude-account"  # dotsync's saved-accounts store
STORE_ACCOUNT = "store"                   # single item holding the whole store

# Reserved account name used to auto-snapshot the outgoing credential on switch.
PREVIOUS_NAME = "__previous__"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AccountError(RuntimeError):
    """A user-facing account operation failed (bad name, no live login, etc.)."""


@dataclass(frozen=True)
class AccountInfo:
    name: str
    active: bool
    subscription: str | None
    has_token: bool = False


@dataclass(frozen=True)
class CurrentStatus:
    name: str | None          # saved account matching the live credential, if any
    account_uuid: str | None
    email: str | None
    subscription: str | None
    matched: bool             # True when live matches a saved account


# --- paths / live state ------------------------------------------------------

def _live_account_name() -> str:
    """The Keychain `account` attribute Claude uses — the macOS login user.

    Verified on macOS: Claude stores its credential under the login username.
    We always pass this as `-a` so reads/writes target exactly one item.
    """
    return getpass.getuser()


def _claude_json_path() -> Path:
    return Path.home() / ".claude.json"


def _read_live_credentials() -> str | None:
    return keychain.read_secret(LIVE_SERVICE, _live_account_name())


def _read_live_identity() -> tuple[dict[str, Any] | None, Any]:
    """Return (oauthAccount, userID) from ~/.claude.json, or (None, None)."""
    path = _claude_json_path()
    if not path.exists():
        return None, None
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None, None
    if not isinstance(doc, dict):
        return None, None
    return doc.get("oauthAccount"), doc.get("userID")


# --- store (single consolidated Keychain item) -------------------------------

def _empty_store() -> dict[str, Any]:
    return {"active": None, "accounts": {}}


def _load_store() -> dict[str, Any]:
    raw = keychain.read_secret(STORE_SERVICE, STORE_ACCOUNT)
    if not raw:
        return _empty_store()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AccountError(f"account store is corrupted: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("accounts"), dict):
        raise AccountError("account store is malformed")
    doc.setdefault("active", None)
    return doc


def _save_store(store: dict[str, Any]) -> None:
    keychain.write_secret(
        STORE_SERVICE, STORE_ACCOUNT, json.dumps(store, ensure_ascii=False)
    )


# --- helpers -----------------------------------------------------------------

def _validate_name(name: str) -> None:
    if name == PREVIOUS_NAME or name.startswith("__"):
        raise AccountError(f"`{name}` is a reserved name")
    if not _NAME_RE.match(name or ""):
        raise AccountError(
            f"invalid account name `{name}` "
            "(use letters, digits, dot, dash, underscore)"
        )


def _validate_token(token: str) -> str:
    """Normalize + validate a `claude setup-token` value (CLAUDE_CODE_OAUTH_TOKEN).

    These are OAuth *subscription* tokens (`sk-ant-oat...`), NOT metered API keys
    (`sk-ant-api...`). Reject anything that isn't shaped like an oat token so a
    stray API key can't be stored and silently divert billing.
    """
    stripped = (token or "").strip()
    if not stripped.startswith("sk-ant-oat"):
        raise AccountError(
            "invalid OAuth token — expected a `claude setup-token` value "
            "(starts with `sk-ant-oat`), not an API key"
        )
    return stripped


def _writeback_outgoing(store: dict[str, Any], name: str, snap: dict[str, Any]) -> None:
    """Replace `name`'s record with the live snapshot, PRESERVING its setupToken.

    `snap` (from `_snapshot_live`) carries only credential+identity, so a plain
    overwrite would drop a saved per-tab token. Keep it.
    """
    token = store["accounts"].get(name, {}).get("setupToken")
    rec = dict(snap)
    if token is not None:
        rec["setupToken"] = token
    store["accounts"][name] = rec


def _subscription_of(record: dict[str, Any]) -> str | None:
    """Best-effort subscription label from a saved account's token blob."""
    try:
        blob = json.loads(record.get("credentials", ""))
        return blob.get("claudeAiOauth", {}).get("subscriptionType")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _visible(store: dict[str, Any]) -> dict[str, Any]:
    """Accounts excluding reserved bookkeeping slots."""
    return {
        name: rec
        for name, rec in store["accounts"].items()
        if not name.startswith("__")
    }


# --- operations --------------------------------------------------------------

def _derive_account_name(email: Any, taken: set[str]) -> str:
    """Make a valid, unique account name from an email (its local-part).

    dev@numchida.com -> 'dev'. On collision, try the full-email slug, then a
    numeric suffix. Falls back to 'account' when there's no usable email.
    """
    local = (email or "").split("@")[0] if isinstance(email, str) else ""
    base = re.sub(r"[^A-Za-z0-9._-]", "-", local).strip("-_.").lower()
    if not _NAME_RE.match(base) or base.startswith("__"):
        base = "account"
    if base not in taken:
        return base
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", email if isinstance(email, str) else "")
    slug = slug.strip("-_.").lower()
    if _NAME_RE.match(slug) and not slug.startswith("__") and slug not in taken:
        return slug
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _run_claude_login_default() -> int:
    """Shell out to the real `claude auth login` (interactive browser flow).

    `shutil.which` finds the on-PATH binary (the interactive shell's `claude`
    launcher function is not visible to a subprocess), so this never recurses
    back into the launcher. stdio is inherited so the user completes the login.
    """
    exe = shutil.which("claude")
    if not exe:
        raise AccountError("`claude` CLI not found on PATH")
    return subprocess.run([exe, "auth", "login"]).returncode


def login(
    name: str | None = None, *, login_fn: Callable[[], int] | None = None
) -> str:
    """Run `claude auth login`, then save the resulting account. Returns its name.

    `name` is optional: when omitted, it's derived from the account's email after
    login (dev@x.com -> 'dev'). Does NOT call `claude auth logout` first — a
    logout can revoke the previous account's tokens server-side, which would kill
    an already-saved account. The user picks the target account in the browser. A
    dupe-guard refuses to save the same account under a second name (e.g. when the
    browser re-authed the account you were already on).
    """
    store = _load_store()
    if name is not None:
        _validate_name(name)
        if name in _visible(store):
            raise AccountError(f"account `{name}` already exists (remove it first)")
    before_account, _before_uid = _read_live_identity()
    before_uuid = (before_account or {}).get("accountUuid")
    # Write-back: the login below overwrites the live token with the new account.
    # If the outgoing account is one we've saved, its live token may have rotated
    # since we snapshotted it (a token renewal revokes the old refresh token), so
    # persist the current live token back into its slot first — otherwise it's
    # left holding a revoked credential. Saved now so it survives even if the
    # interactive login is cancelled.
    before_creds = _read_live_credentials()
    if before_creds:
        before_snap = _snapshot_live(before_creds)
        outgoing = _match_visible_name(store, before_snap["oauthAccount"])
        if outgoing:
            _writeback_outgoing(store, outgoing, before_snap)
            _save_store(store)
    runner = login_fn or _run_claude_login_default
    if runner() != 0:
        raise AccountError("`claude auth login` did not complete — nothing saved")
    creds = _read_live_credentials()
    if not creds:
        raise AccountError("login finished but no live Claude credential was found")
    oauth_account, user_id = _read_live_identity()
    new_uuid = (oauth_account or {}).get("accountUuid")
    if before_uuid and new_uuid == before_uuid:
        raise AccountError(
            "still logged in as the same account — the browser re-authed the "
            "account you were already on. Pick the other account in the browser "
            "(sign out of it / use an incognito window), then retry"
        )
    existing = _match_visible_name(store, oauth_account)
    if existing:
        raise AccountError(
            f"you logged in as the account already saved as `{existing}` — "
            "to register a different account, pick the other account in the browser "
            "(sign out of it / use an incognito window), then retry"
        )
    if name is None:
        email = (oauth_account or {}).get("emailAddress")
        name = _derive_account_name(email, set(_visible(store)))
    store["accounts"][name] = {
        "credentials": creds,
        "oauthAccount": oauth_account,
        "userID": user_id,
    }
    store["active"] = name
    _save_store(store)
    return name


def add(name: str) -> None:
    """Snapshot the currently logged-in account under `name` and make it active."""
    _validate_name(name)
    store = _load_store()
    if name in _visible(store):
        raise AccountError(f"account `{name}` already exists (remove it first)")
    creds = _read_live_credentials()
    if not creds:
        raise AccountError(
            "no live Claude credential to save — run `claude auth login` first"
        )
    oauth_account, user_id = _read_live_identity()
    existing = _match_visible_name(store, oauth_account)
    if existing:
        raise AccountError(
            f"this account is already saved as `{existing}` — each Claude account "
            "can only be registered once (you are logged in as that account now)"
        )
    store["accounts"][name] = {
        "credentials": creds,
        "oauthAccount": oauth_account,
        "userID": user_id,
    }
    store["active"] = name
    _save_store(store)


def list_accounts() -> list[AccountInfo]:
    """Saved accounts (excluding reserved slots), sorted by name."""
    store = _load_store()
    active = store.get("active")
    return [
        AccountInfo(
            name=name,
            active=(name == active),
            subscription=_subscription_of(rec),
            has_token=bool(rec.get("setupToken")),
        )
        for name, rec in sorted(_visible(store).items())
    ]


def set_token(name: str, token: str) -> None:
    """Save a long-lived per-tab token (CLAUDE_CODE_OAUTH_TOKEN) for `name`.

    From `claude setup-token`, run while logged in to claude.ai as that account.
    The account↔token binding is user-asserted — setup-token leaves no local
    identity trace to verify against (see the design spike), so dotsync trusts
    the caller's `name`.
    """
    store = _load_store()
    if name not in _visible(store):
        raise AccountError(f"no saved account `{name}`")
    store["accounts"][name]["setupToken"] = _validate_token(token)
    _save_store(store)


def token_of(name: str) -> str | None:
    """The saved per-tab token for `name`, or None if it has none.

    Raises `AccountError` if `name` isn't a saved account, so callers can tell
    "no such account" apart from "account exists but no token yet".
    """
    store = _load_store()
    visible = _visible(store)
    if name not in visible:
        raise AccountError(f"no saved account `{name}`")
    return visible[name].get("setupToken")


def remove(name: str) -> None:
    """Forget a saved account."""
    store = _load_store()
    if name not in _visible(store):
        raise AccountError(f"no saved account named `{name}`")
    del store["accounts"][name]
    if store.get("active") == name:
        store["active"] = None
    _save_store(store)


# --- switching ---------------------------------------------------------------

def _merged_live_blob(target_creds: str, live_creds: str | None) -> str:
    """Token to install: target's `claudeAiOauth` + the PRESERVED live
    `designOauth` (device/client-scoped — not part of the Claude account)."""
    target = json.loads(target_creds)
    out: dict[str, Any] = {"claudeAiOauth": target.get("claudeAiOauth")}
    design = None
    if live_creds:
        try:
            design = json.loads(live_creds).get("designOauth")
        except json.JSONDecodeError:
            design = None
    if design is None:
        design = target.get("designOauth")
    if design is not None:
        out["designOauth"] = design
    return json.dumps(out, ensure_ascii=False)


def _apply_identity(oauth_account: Any, user_id: Any) -> None:
    """Write oauthAccount+userID into ~/.claude.json, preserving all other keys."""
    path = _claude_json_path()
    doc: Any = {}
    if path.exists():
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    if oauth_account is not None:
        doc["oauthAccount"] = oauth_account
    if user_id is not None:
        doc["userID"] = user_id
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


def _snapshot_live(live_creds: str) -> dict[str, Any]:
    oa, uid = _read_live_identity()
    return {"credentials": live_creds, "oauthAccount": oa, "userID": uid}


def _install(record: dict[str, Any], live_creds: str | None) -> None:
    """Make `record` the live account (token + on-disk identity)."""
    keychain.write_secret(
        LIVE_SERVICE,
        _live_account_name(),
        _merged_live_blob(record["credentials"], live_creds),
    )
    _apply_identity(record.get("oauthAccount"), record.get("userID"))


def _match_visible_name(store: dict[str, Any], oauth_account: Any) -> str | None:
    uuid = (oauth_account or {}).get("accountUuid")
    if not uuid:
        return None
    for name, rec in _visible(store).items():
        if (rec.get("oauthAccount") or {}).get("accountUuid") == uuid:
            return name
    return None


def use(name: str) -> None:
    """Switch the live Claude login to saved account `name`.

    The outgoing credential is auto-snapshotted into a reserved slot first, so a
    switch is always undoable even if the current login was never `add`-ed.
    """
    store = _load_store()
    visible = _visible(store)
    if name not in visible:
        have = ", ".join(sorted(visible)) or "none"
        raise AccountError(f"no saved account `{name}` (saved: {have})")
    live_creds = _read_live_credentials()
    if live_creds:
        snap = _snapshot_live(live_creds)
        # Write-back: Claude rotates the refresh token whenever it renews the
        # access token, revoking the one we saved. So the live credential for
        # the OUTGOING account is likely fresher than its saved snapshot. Persist
        # it back into that account's own slot before switching, or a later
        # switch back reinstalls a revoked token -> "Not logged in".
        outgoing = _match_visible_name(store, snap["oauthAccount"])
        if outgoing:
            _writeback_outgoing(store, outgoing, snap)
        store["accounts"][PREVIOUS_NAME] = snap
    _install(visible[name], live_creds)
    store["active"] = name
    _save_store(store)


def undo() -> None:
    """Restore the credential replaced by the most recent `use`."""
    store = _load_store()
    prev = store["accounts"].get(PREVIOUS_NAME)
    if not prev:
        raise AccountError("nothing to undo")
    live_creds = _read_live_credentials()
    new_prev = _snapshot_live(live_creds) if live_creds else prev
    _install(prev, live_creds)
    store["accounts"][PREVIOUS_NAME] = new_prev
    store["active"] = _match_visible_name(store, prev.get("oauthAccount"))
    _save_store(store)


def current() -> CurrentStatus:
    """Report which saved account the live credential currently matches.

    Matched by on-disk `oauthAccount.accountUuid` (stable across token refreshes)
    rather than a marker, so it stays correct even after out-of-band
    `claude auth` logins.
    """
    store = _load_store()
    oauth_account, _uid = _read_live_identity()
    account_uuid = (oauth_account or {}).get("accountUuid")
    email = (oauth_account or {}).get("emailAddress")
    subscription = None
    live_creds = _read_live_credentials()
    if live_creds:
        try:
            subscription = (
                json.loads(live_creds).get("claudeAiOauth", {}).get("subscriptionType")
            )
        except json.JSONDecodeError:
            subscription = None
    name = _match_visible_name(store, oauth_account)
    return CurrentStatus(
        name=name,
        account_uuid=account_uuid,
        email=email,
        subscription=subscription,
        matched=name is not None,
    )
