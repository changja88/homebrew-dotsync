"""Account-scoped lifecycle orchestration for isolated usage providers."""

from __future__ import annotations

import threading
import time
import uuid
import weakref
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Protocol

from dotsync.accounts import (
    AccountConflict,
    AccountNotFound,
    AccountStore,
    AccountStoreError,
    ManagedAccount,
    ProviderName,
)
from dotsync.app_paths import AppPaths
from dotsync.private_fs import (
    PrivateAtomicWriteUncertain,
    PrivateDirectoryIdentity,
    ensure_private_root_identity,
    fsync_private_directory,
    validate_private_tree,
)
from dotsync.providers import LoginProgress, ProviderError, UsageProvider

from .cache import UsageCache, UsageCacheError
from .deletion import (
    AccountDeletion,
    DeletionCleanupPending,
    DeletionRecoveryError,
    ManifestlessDeletionRoot,
)
from .model import UsageSnapshot


class OperationConflict(RuntimeError):
    """Raised when an operation is already running for one account."""


class DeletionJobContext(Protocol):
    """Job-owned cancellation boundary required by asynchronous deletion."""

    cancel_event: threading.Event

    def acquire_point_of_no_return(self) -> bool:
        """Atomically acquire a new deletion's irreversible boundary."""
        raise NotImplementedError

    def resume_beyond_point_of_no_return(self) -> None:
        """Record that recovered deletion state is already irreversible."""
        raise NotImplementedError


@dataclass(frozen=True)
class UsageResult:
    snapshot: UsageSnapshot | None
    stale: bool
    error_code: str | None


class _ProviderLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._available = limit
        self._waiters = 0
        self._condition = threading.Condition()

    def acquire(self, cancel_event: threading.Event | None) -> bool:
        with self._condition:
            if self._available == 0:
                self._waiters += 1
                self._condition.notify_all()
                try:
                    while self._available == 0:
                        if cancel_event is not None and cancel_event.is_set():
                            return False
                        self._condition.wait(
                            timeout=0.05 if cancel_event is not None else None
                        )
                finally:
                    self._waiters -= 1
                    self._condition.notify_all()
            if cancel_event is not None and cancel_event.is_set():
                return False
            self._available -= 1
            return True

    def release(self) -> None:
        with self._condition:
            if self._available >= self._limit:
                raise RuntimeError("provider operation limiter released too often")
            self._available += 1
            self._condition.notify()

    def wait_for_waiters(self, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            return self._condition.wait_for(
                lambda: self._waiters >= count,
                timeout=max(0.0, deadline - time.monotonic()),
            )


class _ServiceCoordination:
    def __init__(self) -> None:
        self.provider_limiter = _ProviderLimiter(2)
        self._account_locks: weakref.WeakValueDictionary[
            str, threading.Lock
        ] = weakref.WeakValueDictionary()
        self._account_locks_guard = threading.Lock()

    def lock_for_account(self, account_id: str) -> threading.Lock:
        with self._account_locks_guard:
            lock = self._account_locks.get(account_id)
            if lock is None:
                lock = threading.Lock()
                self._account_locks[account_id] = lock
            return lock

    def lock_count(self) -> int:
        with self._account_locks_guard:
            return len(self._account_locks)


_COORDINATIONS: weakref.WeakValueDictionary[
    PrivateDirectoryIdentity, _ServiceCoordination
] = weakref.WeakValueDictionary()
_COORDINATIONS_GUARD = threading.Lock()


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
        self._coordination = _coordination_for(paths)

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
            try:
                return self._accounts.set_identity(account.id, identity, "ready")
            except AccountConflict:
                raise ProviderError(
                    "account_conflict",
                    "This provider account is already managed by DotSync.",
                ) from None

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
        job_context: DeletionJobContext | None = None,
    ) -> None:
        """Delete one account through either a direct or job-owned boundary.

        Direct synchronous callers may pass ``cancel_event``. A registered job
        passes its context as ``job_context`` instead so cancellation and the
        point of no return are decided atomically by the job registry.
        """
        if type(force_local) is not bool:
            raise TypeError("force_local must be a boolean")
        if cancel_event is not None and job_context is not None:
            raise TypeError("pass either cancel_event or job_context, not both")
        effective_cancel_event = (
            job_context.cancel_event if job_context is not None else cancel_event
        )
        with self._account_operation(
            account_id,
            wait_cancel_event=effective_cancel_event,
        ):
            if self._recover_deletion(account_id, job_context):
                return
            account = self._accounts.get(account_id)
            profile_root = self._paths.account_root(account.provider, account.id)
            cache_root = self._paths.usage / account.id
            validate_private_tree(
                profile_root,
                allowed_root=self._paths.root,
                allow_symlinks=True,
            )
            validate_private_tree(cache_root, allowed_root=self._paths.root)

            if not force_local and account.state != "logged_out":
                provider = self._provider_for(account)
                with self._provider_operation("logout", effective_cancel_event):
                    provider.logout(account, cancel_event=effective_cancel_event)

            # Point of no return: cancellation is honored through this check.
            # Once the deletion transaction begins, it must either commit or
            # remain explicitly retryable; it must never report cancellation
            # after moving account data out of the live trees.
            if job_context is not None:
                if not job_context.acquire_point_of_no_return():
                    raise _cancelled_provider_error("logout")
            elif (
                effective_cancel_event is not None
                and effective_cancel_event.is_set()
            ):
                raise _cancelled_provider_error("logout")
            deletion = AccountDeletion.begin(self._paths, account)
            self._commit_deletion(deletion)

    def _recover_deletion(
        self,
        account_id: str,
        job_context: DeletionJobContext | None,
    ) -> bool:
        deletion = AccountDeletion.load(self._paths, account_id)
        if deletion is None:
            return False
        if isinstance(deletion, AccountDeletion) and job_context is not None:
            job_context.resume_beyond_point_of_no_return()
        try:
            fsync_private_directory(self._paths.root, root=self._paths.root)
        except OSError:
            raise DeletionCleanupPending(
                "account deletion metadata commit is uncertain; retry required"
            ) from None
        if isinstance(deletion, ManifestlessDeletionRoot):
            metadata_exists = self._metadata_exists(account_id)
            if not metadata_exists and job_context is not None:
                job_context.resume_beyond_point_of_no_return()
            deletion.cleanup_if_empty()
            return not metadata_exists
        if self._metadata_exists(account_id):
            account = self._accounts.get(account_id)
            if account.provider != deletion.provider:
                raise AccountStoreError(
                    "account deletion provider does not match metadata"
                )
            self._commit_deletion(deletion)
            return True
        deletion.cleanup_committed()
        return True

    def _commit_deletion(
        self,
        deletion: AccountDeletion,
    ) -> None:
        try:
            deletion.stage()
            self._accounts.delete_metadata(deletion.account_id)
        except PrivateAtomicWriteUncertain:
            raise DeletionCleanupPending(
                "account deletion metadata commit is uncertain; retry required"
            ) from None
        except BaseException as error:
            if self._metadata_exists(deletion.account_id):
                if not isinstance(error, Exception):
                    raise
                raise DeletionRecoveryError(
                    "account deletion interrupted; staged data left quarantined"
                ) from None
            deletion.cleanup_committed()
            return
        deletion.cleanup_committed()

    def _metadata_exists(self, account_id: str) -> bool:
        try:
            self._accounts.get(account_id)
        except AccountNotFound:
            return False
        return True

    @contextmanager
    def _account_operation(
        self,
        account_id: str,
        *,
        wait_cancel_event: threading.Event | None = None,
    ) -> Iterator[None]:
        validated_id = _validate_account_id(account_id)
        lock = self._coordination.lock_for_account(validated_id)
        if wait_cancel_event is None:
            if not lock.acquire(blocking=False):
                raise OperationConflict("an account operation is already running")
        else:
            while not lock.acquire(timeout=0.05):
                if wait_cancel_event.is_set():
                    raise _cancelled_provider_error("logout")
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
        acquired = self._coordination.provider_limiter.acquire(cancel_event)
        if not acquired:
            raise _cancelled_provider_error(operation)
        try:
            yield
        finally:
            self._coordination.provider_limiter.release()

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


def _is_cancellation(
    error: ProviderError,
    cancel_event: threading.Event | None,
) -> bool:
    return (
        (cancel_event is not None and cancel_event.is_set())
        or error.code == "cancelled"
        or error.code.endswith("_cancelled")
    )


def _validate_account_id(account_id: object) -> str:
    if type(account_id) is not str:
        raise AccountStoreError("account id must be a canonical UUID")
    try:
        parsed = uuid.UUID(account_id)
    except ValueError:
        raise AccountStoreError("account id must be a canonical UUID") from None
    if str(parsed) != account_id:
        raise AccountStoreError("account id must be a canonical UUID")
    return account_id


def _coordination_for(paths: AppPaths) -> _ServiceCoordination:
    identity = ensure_private_root_identity(paths.root)
    with _COORDINATIONS_GUARD:
        coordination = _COORDINATIONS.get(identity)
        if coordination is None:
            coordination = _ServiceCoordination()
            _COORDINATIONS[identity] = coordination
        return coordination


def _coordination_registry_stats() -> tuple[int, int]:
    with _COORDINATIONS_GUARD:
        coordinations = list(_COORDINATIONS.values())
    return (
        len(coordinations),
        sum(coordination.lock_count() for coordination in coordinations),
    )


def _wait_for_provider_waiters(
    service: UsageService,
    *,
    count: int,
    timeout: float,
) -> None:
    if not service._coordination.provider_limiter.wait_for_waiters(count, timeout):
        raise AssertionError("provider operation did not reach the shared limit")
