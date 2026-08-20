"""Strict, non-secret persistence for managed DotSync accounts."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import unicodedata
import uuid
from typing import Any, cast

from dotsync.app_paths import AppPaths
from dotsync.private_fs import atomic_write_json, ensure_private_dir, read_private_json

from .model import AccountState, ManagedAccount, ProviderIdentity, ProviderName


class AccountStoreError(ValueError):
    """Raised when account metadata is invalid or cannot be safely understood."""


class AccountConflict(AccountStoreError):
    """Raised when an account label conflicts within one provider."""


class AccountNotFound(AccountStoreError):
    """Raised when no managed account has the requested identifier."""


_PROVIDERS = frozenset({"claude", "codex"})
_STATES = frozenset(
    {"logged_out", "ready", "reauth_required", "unsupported", "error"}
)
_ROOT_FIELDS = frozenset({"schema_version", "accounts"})
_ACCOUNT_FIELDS = frozenset(
    {"id", "provider", "label", "state", "identity", "created_at"}
)
_IDENTITY_FIELDS = frozenset({"display_name", "email", "plan"})


class AccountStore:
    """Persist managed account metadata without retaining secrets or paths."""

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._registry_path = paths.root / "accounts.json"
        self._lock = threading.RLock()

    def create(self, provider: ProviderName, label: str) -> ManagedAccount:
        with self._lock:
            validated_provider = _validate_provider(provider)
            validated_label = _validate_label(label)
            accounts = self._load()
            _ensure_unique_label(accounts, validated_provider, validated_label)

            account = ManagedAccount(
                id=str(uuid.uuid4()),
                provider=validated_provider,
                label=validated_label,
                state="logged_out",
                identity=ProviderIdentity(None, None, None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            ensure_private_dir(
                self._paths.account_home(account.provider, account.id),
                root=self._paths.root,
            )
            self._save([*accounts, account])
            return account

    def list(self) -> list[ManagedAccount]:
        with self._lock:
            return sorted(self._load(), key=lambda account: (account.provider, account.created_at))

    def get(self, account_id: str) -> ManagedAccount:
        with self._lock:
            return self._find(self._load(), _validate_account_id(account_id))

    def rename(self, account_id: str, label: str) -> ManagedAccount:
        with self._lock:
            validated_id = _validate_account_id(account_id)
            validated_label = _validate_label(label)
            accounts = self._load()
            current = self._find(accounts, validated_id)
            _ensure_unique_label(
                accounts, current.provider, validated_label, excluding_id=current.id
            )
            replacement = ManagedAccount(
                id=current.id,
                provider=current.provider,
                label=validated_label,
                state=current.state,
                identity=current.identity,
                created_at=current.created_at,
            )
            self._save(self._replace(accounts, replacement))
            return replacement

    def set_identity(
        self,
        account_id: str,
        identity: ProviderIdentity,
        state: AccountState,
    ) -> ManagedAccount:
        with self._lock:
            validated_id = _validate_account_id(account_id)
            validated_identity = _validate_identity(identity)
            validated_state = _validate_state(state)
            accounts = self._load()
            current = self._find(accounts, validated_id)
            replacement = ManagedAccount(
                id=current.id,
                provider=current.provider,
                label=current.label,
                state=validated_state,
                identity=validated_identity,
                created_at=current.created_at,
            )
            self._save(self._replace(accounts, replacement))
            return replacement

    def set_state(self, account_id: str, state: AccountState) -> ManagedAccount:
        with self._lock:
            validated_id = _validate_account_id(account_id)
            validated_state = _validate_state(state)
            accounts = self._load()
            current = self._find(accounts, validated_id)
            replacement = ManagedAccount(
                id=current.id,
                provider=current.provider,
                label=current.label,
                state=validated_state,
                identity=current.identity,
                created_at=current.created_at,
            )
            self._save(self._replace(accounts, replacement))
            return replacement

    def delete_metadata(self, account_id: str) -> None:
        with self._lock:
            validated_id = _validate_account_id(account_id)
            accounts = self._load()
            self._find(accounts, validated_id)
            self._save([account for account in accounts if account.id != validated_id])

    def _load(self) -> list[ManagedAccount]:
        try:
            data = read_private_json(self._registry_path, root=self._paths.root)
        except FileNotFoundError:
            return []
        return _decode_registry(data)

    def _save(self, accounts: list[ManagedAccount]) -> None:
        atomic_write_json(
            self._registry_path,
            {
                "schema_version": 1,
                "accounts": [_encode_account(account) for account in accounts],
            },
            root=self._paths.root,
        )

    @staticmethod
    def _find(accounts: list[ManagedAccount], account_id: str) -> ManagedAccount:
        for account in accounts:
            if account.id == account_id:
                return account
        raise AccountNotFound(f"managed account not found: {account_id}")

    @staticmethod
    def _replace(
        accounts: list[ManagedAccount], replacement: ManagedAccount
    ) -> list[ManagedAccount]:
        return [
            replacement if account.id == replacement.id else account for account in accounts
        ]


def _decode_registry(data: Any) -> list[ManagedAccount]:
    if type(data) is not dict or set(data) != _ROOT_FIELDS:
        raise AccountStoreError("unsupported account registry schema")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise AccountStoreError("unsupported account registry schema")
    raw_accounts = data["accounts"]
    if type(raw_accounts) is not list:
        raise AccountStoreError("unsupported account registry schema")

    accounts = [_decode_account(raw_account) for raw_account in raw_accounts]
    identifiers = {account.id for account in accounts}
    if len(identifiers) != len(accounts):
        raise AccountStoreError("account registry contains duplicate id")
    for account in accounts:
        _ensure_unique_label(accounts, account.provider, account.label, account.id)
    return accounts


def _decode_account(data: Any) -> ManagedAccount:
    if type(data) is not dict or set(data) != _ACCOUNT_FIELDS:
        raise AccountStoreError("unsupported account registry schema")
    account_id = _validate_account_id(data["id"])
    provider = _validate_provider(data["provider"])
    label = _validate_label(data["label"])
    if label != data["label"]:
        raise AccountStoreError("account label must be stripped")
    state = _validate_state(data["state"])
    identity = _decode_identity(data["identity"])
    created_at = data["created_at"]
    if type(created_at) is not str or not created_at:
        raise AccountStoreError("account created_at must be a non-empty string")
    return ManagedAccount(
        id=account_id,
        provider=provider,
        label=label,
        state=state,
        identity=identity,
        created_at=created_at,
    )


def _decode_identity(data: Any) -> ProviderIdentity:
    if type(data) is not dict or set(data) != _IDENTITY_FIELDS:
        raise AccountStoreError("unsupported account registry schema")
    return _validate_identity(
        ProviderIdentity(
            display_name=data["display_name"],
            email=data["email"],
            plan=data["plan"],
        )
    )


def _validate_provider(provider: Any) -> ProviderName:
    if type(provider) is not str or provider not in _PROVIDERS:
        raise AccountStoreError("unsupported account provider")
    return cast(ProviderName, provider)


def _validate_account_id(account_id: Any) -> str:
    if type(account_id) is not str:
        raise AccountStoreError("account id must be a canonical UUID")
    try:
        parsed = uuid.UUID(account_id)
    except ValueError as error:
        raise AccountStoreError("account id must be a canonical UUID") from error
    if str(parsed) != account_id:
        raise AccountStoreError("account id must be a canonical UUID")
    return account_id


def _validate_label(label: Any) -> str:
    if type(label) is not str:
        raise AccountStoreError("account label must be a string")
    normalized = label.strip()
    if not normalized or len(normalized) > 80:
        raise AccountStoreError("account label must contain 1 to 80 characters")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise AccountStoreError("account label cannot contain control characters")
    return normalized


def _validate_state(state: Any) -> AccountState:
    if type(state) is not str or state not in _STATES:
        raise AccountStoreError("unsupported account state")
    return cast(AccountState, state)


def _validate_identity(identity: Any) -> ProviderIdentity:
    if type(identity) is not ProviderIdentity:
        raise AccountStoreError("account identity has an unsupported type")
    values = (identity.display_name, identity.email, identity.plan)
    if any(value is not None and type(value) is not str for value in values):
        raise AccountStoreError("account identity fields must be strings or null")
    return identity


def _ensure_unique_label(
    accounts: list[ManagedAccount],
    provider: ProviderName,
    label: str,
    excluding_id: str | None = None,
) -> None:
    normalized_label = label.casefold()
    for account in accounts:
        if (
            account.provider == provider
            and account.id != excluding_id
            and account.label.casefold() == normalized_label
        ):
            raise AccountConflict(
                f"account label already exists for {provider}: {account.label}"
            )


def _encode_account(account: ManagedAccount) -> dict[str, object]:
    return {
        "id": account.id,
        "provider": account.provider,
        "label": account.label,
        "state": account.state,
        "identity": {
            "display_name": account.identity.display_name,
            "email": account.identity.email,
            "plan": account.identity.plan,
        },
        "created_at": account.created_at,
    }
