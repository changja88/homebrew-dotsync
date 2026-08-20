from __future__ import annotations

import threading
import uuid

import pytest

from dotsync.accounts import AccountNotFound, AccountStore, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.private_fs import UnsafePrivatePath
from dotsync.providers import LoginProgress, ProviderError
from dotsync.usage import UsageCache, UsageSnapshot, UsageWindow


class FakeProvider:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.cancel_events: dict[str, threading.Event | None] = {}
        self.failures: dict[str, ProviderError] = {}
        self.refresh_percent = 42.0
        self.on_logout = None

    def login(self, account, report, *, cancel_event=None):
        self.events.append(("login", account.id))
        self.cancel_events["login"] = cancel_event
        report(LoginProgress("starting"))
        self._raise_failure("login")
        return ProviderIdentity(None, "person@example.invalid", "plus")

    def refresh_usage(self, account, *, cancel_event=None):
        self.events.append(("refresh", account.id))
        self.cancel_events["refresh"] = cancel_event
        self._raise_failure("refresh")
        return _snapshot(account.id, used_percent=self.refresh_percent)

    def logout(self, account, *, cancel_event=None):
        self.events.append(("logout", account.id))
        self.cancel_events["logout"] = cancel_event
        if self.on_logout is not None:
            self.on_logout(account)
        self._raise_failure("logout")

    def fail_with(self, operation: str, error: ProviderError) -> None:
        self.failures[operation] = error

    def _raise_failure(self, operation: str) -> None:
        error = self.failures.get(operation)
        if error is not None:
            raise error


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Condition()
        self.entered_count = 0
        self.active_count = 0
        self.maximum_active = 0
        self.release = threading.Event()

    def refresh_usage(self, account, *, cancel_event=None):
        self.events.append(("refresh", account.id))
        with self.entered:
            self.entered_count += 1
            self.active_count += 1
            self.maximum_active = max(self.maximum_active, self.active_count)
            self.entered.notify_all()
        try:
            if not self.release.wait(timeout=2):
                raise AssertionError("test did not release blocking provider")
            return _snapshot(account.id)
        finally:
            with self.entered:
                self.active_count -= 1
                self.entered.notify_all()

    def wait_for_entries(self, count: int) -> None:
        with self.entered:
            assert self.entered.wait_for(
                lambda: self.entered_count >= count,
                timeout=2,
            )


@pytest.fixture
def paths(tmp_path):
    return AppPaths(tmp_path / "DotSync")


@pytest.fixture
def accounts(paths):
    return AccountStore(paths)


@pytest.fixture
def cache(paths):
    return UsageCache(paths)


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def service(paths, accounts, cache, provider):
    from dotsync.usage.service import UsageService

    return UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": provider},
    )


@pytest.fixture
def account(accounts):
    return accounts.create("codex", "Personal")


def test_create_and_rename_account_use_validated_store(service):
    created = service.create_account("codex", " Personal ")

    renamed = service.rename_account(created.id, "Work")

    assert created.label == "Personal"
    assert renamed.label == "Work"
    assert service.list_accounts() == [renamed]


def test_login_passes_cancellation_and_sets_identity_only_after_success(
    service, provider, account
):
    cancel_event = threading.Event()
    progress = []

    identified = service.login(
        account.id,
        progress.append,
        cancel_event=cancel_event,
    )

    assert provider.cancel_events["login"] is cancel_event
    assert identified.state == "ready"
    assert identified.identity.email == "person@example.invalid"
    assert [item.state for item in progress] == ["starting"]


def test_failed_login_preserves_account_state_and_identity(
    service, provider, accounts, account
):
    provider.fail_with(
        "login", ProviderError("provider_unavailable", "temporarily unavailable")
    )

    with pytest.raises(ProviderError) as captured:
        service.login(account.id, lambda progress: None)

    assert captured.value.code == "provider_unavailable"
    assert accounts.get(account.id) == account


def test_successful_refresh_passes_cancellation_and_updates_cache(
    service, provider, cache, account
):
    cancel_event = threading.Event()

    result = service.refresh(account.id, cancel_event=cancel_event)

    assert provider.cancel_events["refresh"] is cancel_event
    assert result.snapshot == cache.load(account.id)
    assert result.snapshot.windows[0].used_percent == 42.0
    assert result.stale is False
    assert result.error_code is None


def test_failed_refresh_returns_stale_successful_snapshot(service, provider, account):
    service.refresh(account.id)
    provider.fail_with(
        "refresh", ProviderError("provider_unavailable", "temporarily unavailable")
    )

    result = service.refresh(account.id)

    assert result.snapshot.windows[0].used_percent == 42.0
    assert result.stale is True
    assert result.error_code == "provider_unavailable"


def test_refresh_auth_failure_alone_sets_reauth_required(
    service, provider, accounts, account
):
    accounts.set_state(account.id, "ready")
    provider.fail_with(
        "refresh", ProviderError("reauth_required", "authentication required")
    )

    result = service.refresh(account.id)

    assert result.snapshot is None
    assert result.stale is True
    assert result.error_code == "reauth_required"
    assert accounts.get(account.id).state == "reauth_required"


@pytest.mark.parametrize(
    "code",
    ["provider_unavailable", "refresh_timeout", "unsupported_cli_version"],
)
def test_non_auth_refresh_failure_does_not_change_lifecycle_state(
    service, provider, accounts, account, code
):
    accounts.set_state(account.id, "ready")
    provider.fail_with("refresh", ProviderError(code, "safe failure"))

    result = service.refresh(account.id)

    assert result.error_code == code
    assert accounts.get(account.id).state == "ready"


def test_unavailable_registered_provider_returns_stale_cache(
    paths, accounts, cache, account
):
    from dotsync.usage.service import UsageService

    cached = _snapshot(account.id, used_percent=23.0)
    cache.save(cached)
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={},
    )

    result = service.refresh(account.id)

    assert result.snapshot == cached
    assert result.stale is True
    assert result.error_code == "provider_unavailable"


def test_same_account_refresh_is_single_flight(paths, accounts, cache):
    from dotsync.usage.service import OperationConflict, UsageService

    blocking_provider = BlockingProvider()
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": blocking_provider},
    )
    account = accounts.create("codex", "Personal")
    thread, outcomes = _start_thread(lambda: service.refresh(account.id))
    blocking_provider.wait_for_entries(1)

    with pytest.raises(OperationConflict, match="already running"):
        service.refresh(account.id)

    blocking_provider.release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert outcomes["error"] is None


def test_provider_operations_are_globally_bounded_to_two(
    paths, accounts, cache
):
    from dotsync.usage.service import UsageService

    blocking_provider = BlockingProvider()
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": blocking_provider},
    )
    managed = [
        accounts.create("codex", f"Account {index}") for index in range(3)
    ]
    start = threading.Barrier(4)
    outcomes = []

    def refresh(account_id):
        start.wait()
        try:
            service.refresh(account_id)
        except BaseException as error:
            outcomes.append(error)

    threads = [
        threading.Thread(target=refresh, args=(item.id,)) for item in managed
    ]
    for thread in threads:
        thread.start()
    start.wait()
    blocking_provider.wait_for_entries(2)

    assert blocking_provider.maximum_active == 2
    assert blocking_provider.entered_count == 2

    blocking_provider.release.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert outcomes == []
    assert blocking_provider.entered_count == 3
    assert blocking_provider.maximum_active == 2


def test_refresh_cancellation_stops_while_waiting_for_provider_slot(
    paths, accounts, cache
):
    from dotsync.usage.service import UsageService

    blocking_provider = BlockingProvider()
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": blocking_provider},
    )
    managed = [
        accounts.create("codex", f"Account {index}") for index in range(3)
    ]
    first, first_outcome = _start_thread(lambda: service.refresh(managed[0].id))
    second, second_outcome = _start_thread(lambda: service.refresh(managed[1].id))
    blocking_provider.wait_for_entries(2)
    cancel_event = threading.Event()
    started = threading.Event()

    def cancelled_refresh():
        started.set()
        return service.refresh(managed[2].id, cancel_event=cancel_event)

    third, third_outcome = _start_thread(cancelled_refresh)
    assert started.wait(timeout=2)
    cancel_event.set()
    third.join(timeout=2)

    assert not third.is_alive()
    assert third_outcome["error"] is None
    assert third_outcome["value"].stale is True
    assert third_outcome["value"].error_code == "refresh_cancelled"
    assert ("refresh", managed[2].id) not in blocking_provider.events

    blocking_provider.release.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert first_outcome["error"] is None
    assert second_outcome["error"] is None


def test_cache_and_metadata_operations_do_not_consume_provider_slots(
    paths, accounts, cache
):
    from dotsync.usage.service import UsageService

    blocking_provider = BlockingProvider()
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": blocking_provider},
    )
    managed = [
        accounts.create("codex", f"Account {index}") for index in range(3)
    ]
    cached = _snapshot(managed[2].id, used_percent=17.0)
    cache.save(cached)
    first, first_outcome = _start_thread(lambda: service.refresh(managed[0].id))
    second, second_outcome = _start_thread(lambda: service.refresh(managed[1].id))
    blocking_provider.wait_for_entries(2)

    renamed = service.rename_account(managed[2].id, "Renamed")
    loaded = service.cached_usage(managed[2].id)

    assert renamed.label == "Renamed"
    assert loaded == cached
    blocking_provider.release.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert first_outcome["error"] is None
    assert second_outcome["error"] is None


def test_logout_calls_provider_before_setting_logged_out(
    service, provider, accounts, account
):
    ready = accounts.set_state(account.id, "ready")
    observed_states = []
    provider.on_logout = lambda current: observed_states.append(
        accounts.get(current.id).state
    )
    cancel_event = threading.Event()

    logged_out = service.logout(account.id, cancel_event=cancel_event)

    assert provider.events == [("logout", account.id)]
    assert provider.cancel_events["logout"] is cancel_event
    assert observed_states == ["ready"]
    assert ready.state == "ready"
    assert logged_out.state == "logged_out"


def test_failed_logout_preserves_account_state(service, provider, accounts, account):
    accounts.set_state(account.id, "ready")
    provider.fail_with("logout", ProviderError("logout_failed", "safe failure"))

    with pytest.raises(ProviderError) as captured:
        service.logout(account.id)

    assert captured.value.code == "logout_failed"
    assert accounts.get(account.id).state == "ready"


def test_delete_logs_out_before_removing_profile_and_cache(
    service, provider, accounts, cache, account, paths
):
    accounts.set_state(account.id, "ready")
    cache.save(_snapshot(account.id))
    existed_during_logout = []
    provider.on_logout = lambda current: existed_during_logout.append(
        (
            paths.account_root(current.provider, current.id).exists(),
            cache.load(current.id) is not None,
        )
    )

    service.delete_account(account.id, force_local=False)

    assert provider.events == [("logout", account.id)]
    assert existed_during_logout == [(True, True)]
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)


def test_delete_logout_failure_preserves_metadata_profile_and_cache(
    service, provider, accounts, cache, account, paths
):
    cache.save(_snapshot(account.id))
    provider.fail_with("logout", ProviderError("logout_failed", "safe failure"))

    with pytest.raises(ProviderError) as captured:
        service.delete_account(account.id, force_local=False)

    assert captured.value.code == "logout_failed"
    assert accounts.get(account.id) == account
    assert paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is not None


def test_force_local_delete_continues_only_after_logout_failure(
    service, provider, accounts, cache, account, paths
):
    cache.save(_snapshot(account.id))
    provider.fail_with("logout", ProviderError("logout_failed", "safe failure"))

    service.delete_account(account.id, force_local=True)

    assert provider.events == [("logout", account.id)]
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)


def test_force_local_delete_allows_unavailable_registered_provider(
    paths, accounts, cache, account
):
    from dotsync.usage.service import UsageService

    cache.save(_snapshot(account.id))
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={},
    )

    service.delete_account(account.id, force_local=True)

    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)


def test_delete_rejects_symlinked_profile_without_touching_target_or_metadata(
    service, provider, accounts, cache, account, paths, tmp_path
):
    accounts.set_state(account.id, "ready")
    cache.save(_snapshot(account.id))
    profile_root = paths.account_root(account.provider, account.id)
    moved_profile = profile_root.with_name(f"{account.id}-moved")
    profile_root.rename(moved_profile)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    profile_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        service.delete_account(account.id, force_local=False)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert accounts.get(account.id).state == "logged_out"
    assert cache.load(account.id) is not None
    assert moved_profile.exists()


def _start_thread(callable_):
    outcomes = {"value": None, "error": None}

    def run():
        try:
            outcomes["value"] = callable_()
        except BaseException as error:
            outcomes["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcomes


def _snapshot(account_id: str, *, used_percent: float = 42.0) -> UsageSnapshot:
    return UsageSnapshot(
        account_id=account_id,
        provider="codex",
        windows=(
            UsageWindow(
                name="five_hour",
                limit_id="primary",
                label="Five hour",
                used_percent=used_percent,
                duration_minutes=300,
                resets_at="2026-08-21T05:00:00Z",
            ),
        ),
        observed_at="2026-08-21T00:00:00Z",
        source="codex_app_server",
        provider_version="1.2.3",
    )
