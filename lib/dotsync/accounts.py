"""Claude Code accounts: keep several logins and switch the one Claude uses.

Claude Code keeps one login per config folder: the tokens in a macOS
Keychain entry named after the folder, the account details in the folder's
`.claude.json`. `dotsync account login <name>` lets Claude Code log in with
`CLAUDE_CONFIG_DIR=~/.claude-accounts/<name>`, so every saved account has
its own Keychain entry. `dotsync account use <name>` then does what `/login`
does to the default folder: it puts that account's login in Claude's default
Keychain entry and its details in `~/.claude.json`. Running sessions pick the
new login up the same way they pick up a `/login`.

Claude refreshes tokens while in use and a used refresh token stops working,
so before switching away from a saved account, the latest login goes back
from the default entry to that account's own entry.
"""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotsync.apps.base import write_text_safely

DEFAULT_SERVICE = "Claude Code-credentials"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RESERVED_NAMES = {"default"}
# `security find-generic-password` exit status for "no such item".
_ITEM_NOT_FOUND = 44
# How long a command waits for another account command to finish.
LOCK_WAIT_SECONDS = 60.0
# The dotsync app's display name for an account lives in its folder.
_LABEL_FILE = ".dotsync-account.json"
LABEL_MAX = 40


class AccountError(RuntimeError):
    """A `dotsync account` command can't go ahead. The message says why;
    `code` names the reason for `--json` callers such as the dotsync app."""

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


class UnsavedLoginError(AccountError):
    """Switching would overwrite a login that no saved account holds."""

    def __init__(self, email: str | None) -> None:
        self.email = email
        super().__init__(
            f"the login Claude uses now ({email or 'unknown account'}) is not "
            "saved in dotsync — switching would drop it",
            "unsaved_login",
        )


def validate_name(name: str) -> str:
    if not _NAME.fullmatch(name) or name in _RESERVED_NAMES:
        raise AccountError(
            f"invalid account name {name!r}: use letters, digits, '.', '_' or '-' "
            "(not starting with '.', and not 'default')",
            "invalid_name",
        )
    return name


def accounts_root() -> Path:
    return Path.home() / ".claude-accounts"


def account_dir(name: str) -> Path:
    return accounts_root() / validate_name(name)


def _existing_dir(name: str) -> Path:
    folder = account_dir(name)
    if not folder.is_dir() or folder.is_symlink():
        raise AccountError(
            f"no saved account named {name} — add it with: dotsync account login {name}",
            "not_found",
        )
    return folder


def service_for(folder: Path) -> str:
    """The Keychain entry Claude Code uses when CLAUDE_CONFIG_DIR is `folder`."""
    digest = hashlib.sha256(str(folder).encode()).hexdigest()[:8]
    return f"{DEFAULT_SERVICE}-{digest}"


@contextmanager
def locked() -> Iterator[None]:
    """Hold ~/.claude-accounts/.lock so only one command changes accounts at
    a time — the dotsync app and a terminal can both run them."""
    root = accounts_root()
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".lock", "w") as handle:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AccountError(
                        "another dotsync account command is running — try again shortly",
                        "busy",
                    ) from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def saved_accounts() -> list[str]:
    root = accounts_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.is_symlink() and _NAME.fullmatch(p.name)
    )


def account_email(name: str) -> str | None:
    return _email(_oauth_account(accounts_root() / name / ".claude.json"))


def label(name: str) -> str:
    """The name the dotsync app shows; the account name until renamed."""
    try:
        doc = json.loads((account_dir(name) / _LABEL_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return name
    value = doc.get("label") if isinstance(doc, dict) else None
    return value if isinstance(value, str) and value.strip() else name


def rename(name: str, new_label: str) -> None:
    """Change the name shown for `name`. The folder — and so the login — stays."""
    folder = _existing_dir(name)
    text = new_label.strip()
    if not text or len(text) > LABEL_MAX:
        raise AccountError(f"a label needs 1–{LABEL_MAX} characters", "invalid_label")
    write_text_safely(
        folder / _LABEL_FILE,
        json.dumps({"label": text}, indent=2, ensure_ascii=False),
        _LABEL_FILE,
    )


def seat_email() -> str | None:
    """Email of the login Claude uses now (the default config folder)."""
    return _email(_oauth_account(_seat_config()))


def is_logged_in(name: str) -> bool:
    return _has_secret(service_for(account_dir(name)))


def active_account() -> str | None:
    """The saved account whose login Claude uses now, or None when the login
    in use isn't saved (or there is none)."""
    seat = _oauth_account(_seat_config())
    uuid = seat.get("accountUuid") if seat else None
    if not uuid:
        return None
    for name in saved_accounts():
        saved = _oauth_account(accounts_root() / name / ".claude.json")
        if saved and saved.get("accountUuid") == uuid:
            return name
    return None


def account_info(name: str) -> dict:
    """What the dotsync app shows for one saved account."""
    logged_in = is_logged_in(name)
    return {
        "name": name,
        "label": label(name),
        "email": account_email(name) if logged_in else None,
        "logged_in": logged_in,
    }


def snapshot() -> dict:
    """Saved accounts, the one in use, and the login Claude uses now."""
    email = seat_email()
    active = active_account()
    return {
        "active": active,
        "seat": None if email is None else {"email": email, "saved": active is not None},
        "accounts": [account_info(name) for name in saved_accounts()],
    }


def login(name: str) -> str:
    """Run `claude auth login` for `name`'s own folder — the browser decides
    which claude.ai account it saves. Returns that account's email."""
    folder = account_dir(name)
    if name == active_account():
        raise AccountError(
            f"{name} is the account in use — switch to another account before "
            "logging it in again"
        )
    claude = _claude_binary()
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [claude, "auth", "login"],
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(folder)},
        # stdout is reserved for dotsync's own answer (`--json`).
        stdout=sys.stderr,
    )
    email = account_email(name)
    if result.returncode != 0 or email is None or not _has_secret(service_for(folder)):
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        raise AccountError(f"`claude auth login` did not finish for {name}")
    return email


def use(name: str, *, allow_unsaved_overwrite: bool = False) -> bool:
    """Make Claude use `name`'s login. Returns False when it already does."""
    folder = _existing_dir(name)
    secret = _read_secret(service_for(folder))
    account = _oauth_account(folder / ".claude.json")
    if secret is None or account is None:
        raise AccountError(
            f"{name} is not logged in — run `dotsync account login {name}` first",
            "login_required",
        )
    current = active_account()
    if current == name:
        return False
    seat_secret = _read_secret(DEFAULT_SERVICE)
    if current is not None:
        # Hand the latest login back before overwriting it: the copy saved
        # at the last switch may hold a refresh token Claude already used.
        current_folder = account_dir(current)
        if seat_secret is not None:
            _write_secret(service_for(current_folder), seat_secret)
        _set_oauth_account(current_folder / ".claude.json", _oauth_account(_seat_config()))
    elif seat_secret is not None and not allow_unsaved_overwrite:
        raise UnsavedLoginError(seat_email())
    _write_secret(DEFAULT_SERVICE, secret)
    _set_oauth_account(_seat_config(), account)
    return True


def remove(name: str) -> None:
    """Log `name` out and delete its folder."""
    folder = _existing_dir(name)
    if name == active_account():
        raise AccountError(
            f"{name} is the account in use — switch to another account first",
            "active_account",
        )
    result = subprocess.run(
        [_claude_binary(), "auth", "logout"],
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(folder)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AccountError(f"`claude auth logout` failed for {name}: {result.stderr.strip()}")
    shutil.rmtree(folder)


def _seat_config() -> Path:
    return Path.home() / ".claude.json"


def _claude_binary() -> str:
    claude = shutil.which("claude")
    if claude is None:
        raise AccountError("claude is not installed (not found on PATH)", "claude_missing")
    return claude


def _email(account: dict | None) -> str | None:
    email = account.get("emailAddress") if account else None
    return email if isinstance(email, str) else None


def _oauth_account(config: Path) -> dict | None:
    try:
        doc = json.loads(config.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    account = doc.get("oauthAccount") if isinstance(doc, dict) else None
    return account if isinstance(account, dict) else None


def _set_oauth_account(config: Path, account: dict | None) -> None:
    if account is None:
        return
    created = not config.exists()
    try:
        doc = {} if created else json.loads(config.read_text())
    except json.JSONDecodeError as e:
        raise AccountError(f"{config} is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise AccountError(f"{config} is not a JSON object")
    doc["oauthAccount"] = account
    write_text_safely(config, json.dumps(doc, indent=2, ensure_ascii=False), config.name)
    if created:
        config.chmod(0o600)


def _security(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["security", *args], input=stdin, capture_output=True, text=True)


def _has_secret(service: str) -> bool:
    result = _security("find-generic-password", "-s", service, "-a", getpass.getuser())
    if result.returncode not in (0, _ITEM_NOT_FOUND):
        raise AccountError(f"could not read the Keychain entry {service!r}: {result.stderr.strip()}")
    return result.returncode == 0


def _read_secret(service: str) -> str | None:
    result = _security("find-generic-password", "-s", service, "-a", getpass.getuser(), "-w")
    if result.returncode == _ITEM_NOT_FOUND:
        return None
    if result.returncode != 0:
        # Reading as "no login" here would let `use` overwrite a login
        # without handing it back first.
        raise AccountError(f"could not read the Keychain entry {service!r}: {result.stderr.strip()}")
    out = result.stdout
    return out[:-1] if out.endswith("\n") else out


def _write_secret(service: str, secret: str) -> None:
    # `security -i` reads the command from stdin, so the secret never shows
    # up in the process list the way a command-line argument would.
    command = (
        f'add-generic-password -U -a "{getpass.getuser()}" -s "{service}" '
        f'-X "{secret.encode().hex()}"\n'
    )
    _security("-i", stdin=command)
    if _read_secret(service) != secret:
        raise AccountError(f"could not save the login in the Keychain entry {service!r}")
