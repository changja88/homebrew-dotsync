"""Account-scoped lifecycle orchestration for isolated usage providers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from dotsync.accounts import AccountStore, ManagedAccount, ProviderName
from dotsync.app_paths import AppPaths
from dotsync.private_fs import remove_private_tree
from dotsync.providers import LoginProgress, ProviderError, UsageProvider

from .cache import UsageCache, UsageCacheError
from .model import UsageSnapshot


class OperationConflict(RuntimeError):
    """Raised when an operation is already running for one account."""


@dataclass(frozen=True)
class UsageResult:
    snapshot: UsageSnapshot | None
    stale: bool
    error_code: str | None


class UsageService:
    """Coordinate account metadata, provider work, and cached usage."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        accounts: AccountStore,
        cache: UsageCache,
        providers: Mapping[ProviderName, UsageProvider],
    ) -> None:
        self._paths = paths
        self._accounts = accounts
        self._cache = cache
        self._providers = dict(providers)
        self._provider_slots = threading.BoundedSemaphore(2)
        self._account_locks: dict[str, threading.Lock] = {}
        self._account_locks_guard = threading.Lock()

    def create_account(self, provider: ProviderName, label: str) -> ManagedAccount:
        return self._accounts.create(provider, label)

    def list_accounts(self) -> list[ManagedAccount]:
        return self._accounts.list()

    def rename_account(self, account_id: str, label: str) -> ManagedAccount:
        with self._account_operation(account_id):
            return self._accounts.rename(account_id, label)

    def cached_usage(self, account_id: str) -> UsageSnapshot | None:
        with self._account_operation(account_id):
            account = self._accounts.get(account_id)
            return self._load_cached(account)

    def login(
        self,
        account_id: str,
        report: Callable[[LoginProgress], None],
        *,
        cancel_event: threading.Event | None = None,
    ) -> ManagedAccount:
        with self._account_operation(account_id):
            account = self._accounts.get(account_id)
            provider = self._provider_for(account)
            with self._provider_operation("login", cancel_event):
                identity = provider.login(
                    account,
                    report,
                    cancel_event=cancel_event,
                )
            return self._accounts.set_identity(account.id, identity, "ready")

    def refresh(
        self,
        account_id: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> UsageResult:
        with self._account_operation(account_id):
            account = self._accounts.get(account_id)
            try:
                provider = self._provider_for(account)
                with self._provider_operation("refresh", cancel_event):
                    snapshot = provider.refresh_usage(
                        account,
                        cancel_event=cancel_event,
                    )
                self._validate_provider_snapshot(account, snapshot)
            except ProviderError as error:
                if error.code == "reauth_required":
                    self._accounts.set_state(account.id, "reauth_required")
                return UsageResult(
                    snapshot=self._load_cached(account),
                    stale=True,
                    error_code=error.code,
                )
            self._cache.save(snapshot)
            return UsageResult(snapshot=snapshot, stale=False, error_code=None)

    def logout(
        self,
        account_id: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ManagedAccount:
        with self._account_operation(account_id):
            account = self._accounts.get(account_id)
            provider = self._provider_for(account)
            with self._provider_operation("logout", cancel_event):
                provider.logout(account, cancel_event=cancel_event)
            return self._accounts.set_state(account.id, "logged_out")

    def delete_account(
        self,
        account_id: str,
        *,
        force_local: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if type(force_local) is not bool:
            raise TypeError("force_local must be a boolean")
        with self._account_operation(account_id):
            account = self._accounts.get(account_id)
            logout_succeeded = False
            try:
                provider = self._provider_for(account)
                with self._provider_operation("logout", cancel_event):
                    provider.logout(account, cancel_event=cancel_event)
            except ProviderError:
                if not force_local:
                    raise
            else:
                logout_succeeded = True

            if logout_succeeded:
                self._accounts.set_state(account.id, "logged_out")

            profile_root = self._paths.account_root(account.provider, account.id)
            remove_private_tree(
                profile_root,
                allowed_root=self._paths.accounts / account.provider,
            )
            self._cache.delete(account.id)
            self._accounts.delete_metadata(account.id)

    @contextmanager
    def _account_operation(self, account_id: str) -> Iterator[None]:
        if type(account_id) is not str:
            self._accounts.get(account_id)
        lock = self._lock_for_account(account_id)
        if not lock.acquire(blocking=False):
            raise OperationConflict("an account operation is already running")
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def _provider_operation(
        self,
        operation: str,
        cancel_event: threading.Event | None,
    ) -> Iterator[None]:
        acquired = False
        if cancel_event is None:
            self._provider_slots.acquire()
            acquired = True
        else:
            while not acquired:
                if cancel_event.is_set():
                    raise _cancelled_provider_error(operation)
                acquired = self._provider_slots.acquire(timeout=0.05)
            if cancel_event.is_set():
                self._provider_slots.release()
                acquired = False
                raise _cancelled_provider_error(operation)
        try:
            yield
        finally:
            if acquired:
                self._provider_slots.release()

    def _lock_for_account(self, account_id: str) -> threading.Lock:
        with self._account_locks_guard:
            lock = self._account_locks.get(account_id)
            if lock is None:
                lock = threading.Lock()
                self._account_locks[account_id] = lock
            return lock

    def _provider_for(self, account: ManagedAccount) -> UsageProvider:
        provider = self._providers.get(account.provider)
        if provider is None:
            raise ProviderError(
                "provider_unavailable",
                "The account provider is unavailable.",
            )
        return provider

    def _load_cached(self, account: ManagedAccount) -> UsageSnapshot | None:
        snapshot = self._cache.load(account.id)
        if snapshot is not None and snapshot.provider != account.provider:
            raise UsageCacheError("usage cache provider does not match its account")
        return snapshot

    @staticmethod
    def _validate_provider_snapshot(
        account: ManagedAccount,
        snapshot: UsageSnapshot,
    ) -> None:
        if (
            type(snapshot) is not UsageSnapshot
            or snapshot.account_id != account.id
            or snapshot.provider != account.provider
        ):
            raise ProviderError(
                "provider_unavailable",
                "The usage provider returned an invalid account snapshot.",
            )


def _cancelled_provider_error(operation: str) -> ProviderError:
    codes = {
        "login": "login_cancelled",
        "refresh": "refresh_cancelled",
        "logout": "logout_cancelled",
    }
    return ProviderError(codes[operation], "The account operation was cancelled.")
