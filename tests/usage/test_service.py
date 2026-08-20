from __future__ import annotations

import gc
import os
import stat
import threading
import uuid
import weakref

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


class FirstCallBlockingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self.call_count = 0
        self._guard = threading.Lock()

    def refresh_usage(self, account, *, cancel_event=None):
        with self._guard:
            self.call_count += 1
            call_number = self.call_count
        if call_number == 1:
            self.first_entered.set()
            assert self.release_first.wait(timeout=2)
        return _snapshot(account.id)


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


def test_same_account_is_single_flight_across_service_instances(
    paths, accounts, cache
):
    from dotsync.usage.service import OperationConflict, UsageService

    blocking_provider = FirstCallBlockingProvider()
    first_service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": blocking_provider},
    )
    second_service = UsageService(
        paths=AppPaths(paths.root / "."),
        accounts=AccountStore(AppPaths(paths.root / ".")),
        cache=UsageCache(AppPaths(paths.root / ".")),
        providers={"codex": blocking_provider},
    )
    account = accounts.create("codex", "Personal")
    thread, outcome = _start_thread(lambda: first_service.refresh(account.id))
    assert blocking_provider.first_entered.wait(timeout=2)

    with pytest.raises(OperationConflict, match="already running"):
        second_service.refresh(account.id)

    blocking_provider.release_first.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert outcome["error"] is None
    assert blocking_provider.call_count == 1


def test_same_account_is_single_flight_across_case_aliases(tmp_path):
    from dotsync.usage.service import OperationConflict, UsageService

    first_paths, alias_paths = _case_alias_paths(tmp_path)
    first_accounts = AccountStore(first_paths)
    account = first_accounts.create("codex", "Personal")
    blocking_provider = FirstCallBlockingProvider()
    first_service = UsageService(
        paths=first_paths,
        accounts=first_accounts,
        cache=UsageCache(first_paths),
        providers={"codex": blocking_provider},
    )
    alias_service = UsageService(
        paths=alias_paths,
        accounts=AccountStore(alias_paths),
        cache=UsageCache(alias_paths),
        providers={"codex": blocking_provider},
    )
    thread, outcome = _start_thread(lambda: first_service.refresh(account.id))
    assert blocking_provider.first_entered.wait(timeout=2)

    try:
        with pytest.raises(OperationConflict, match="already running"):
            alias_service.refresh(account.id)
    finally:
        blocking_provider.release_first.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert outcome["error"] is None
    assert blocking_provider.call_count == 1


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


def test_provider_limit_is_shared_across_service_instances(
    paths, accounts, cache
):
    from dotsync.usage.service import UsageService, _wait_for_provider_waiters

    blocking_provider = BlockingProvider()
    services = [
        UsageService(
            paths=paths,
            accounts=accounts,
            cache=cache,
            providers={"codex": blocking_provider},
        )
        for _ in range(2)
    ]
    managed = [
        accounts.create("codex", f"Shared {index}") for index in range(3)
    ]
    outcomes = []

    def refresh(index):
        try:
            services[index % 2].refresh(managed[index].id)
        except BaseException as error:
            outcomes.append(error)

    threads = [threading.Thread(target=refresh, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    blocking_provider.wait_for_entries(2)
    third = threading.Thread(target=refresh, args=(2,))
    third.start()
    _wait_for_provider_waiters(services[0], count=1, timeout=2)

    assert blocking_provider.maximum_active == 2
    assert blocking_provider.entered_count == 2

    blocking_provider.release.set()
    for thread in [*threads, third]:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert outcomes == []
    assert blocking_provider.entered_count == 3
    assert blocking_provider.maximum_active == 2


def test_provider_limit_is_shared_across_case_aliases(tmp_path):
    from dotsync.usage.service import UsageService, _wait_for_provider_waiters

    first_paths, alias_paths = _case_alias_paths(tmp_path)
    first_accounts = AccountStore(first_paths)
    alias_accounts = AccountStore(alias_paths)
    blocking_provider = BlockingProvider()
    first_service = UsageService(
        paths=first_paths,
        accounts=first_accounts,
        cache=UsageCache(first_paths),
        providers={"codex": blocking_provider},
    )
    alias_service = UsageService(
        paths=alias_paths,
        accounts=alias_accounts,
        cache=UsageCache(alias_paths),
        providers={"codex": blocking_provider},
    )
    accounts = [
        first_accounts.create("codex", f"Alias {index}") for index in range(3)
    ]
    threads = [
        _start_thread(lambda item=item: first_service.refresh(item.id))
        for item in accounts[:2]
    ]
    blocking_provider.wait_for_entries(2)
    third_thread, third_outcome = _start_thread(
        lambda: alias_service.refresh(accounts[2].id)
    )

    try:
        _wait_for_provider_waiters(alias_service, count=1, timeout=0.25)
        assert blocking_provider.entered_count == 2
        assert blocking_provider.maximum_active == 2
    finally:
        blocking_provider.release.set()
        for thread, _ in threads:
            thread.join(timeout=2)
        third_thread.join(timeout=2)

    assert all(outcome["error"] is None for _, outcome in threads)
    assert third_outcome["error"] is None
    assert blocking_provider.entered_count == 3
    assert blocking_provider.maximum_active == 2


def test_invalid_account_ids_allocate_no_coordination_locks(
    service,
):
    from dotsync.accounts import AccountStoreError
    from dotsync.usage.service import _coordination_registry_stats

    before = _coordination_registry_stats()

    for index in range(100):
        with pytest.raises(AccountStoreError, match="account id"):
            service.cached_usage(f"invalid-{index}")

    after_roots, after_locks = _coordination_registry_stats()
    before_roots, before_locks = before
    assert after_roots <= before_roots
    assert after_locks == before_locks


def test_service_coordination_registry_releases_unused_roots(tmp_path):
    from dotsync.usage.service import UsageService, _coordination_registry_stats

    gc.collect()
    before_roots, before_locks = _coordination_registry_stats()
    paths = AppPaths(tmp_path / "ephemeral" / "DotSync")
    service = UsageService(
        paths=paths,
        accounts=AccountStore(paths),
        cache=UsageCache(paths),
        providers={},
    )
    coordination = weakref.ref(service._coordination)
    assert _coordination_registry_stats() == (before_roots + 1, before_locks)

    del service
    gc.collect()

    assert coordination() is None
    assert _coordination_registry_stats() == (before_roots, before_locks)


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


def test_force_local_delete_cancellation_while_waiting_preserves_all_local_state(
    paths, accounts, cache
):
    from dotsync.usage.service import UsageService, _wait_for_provider_waiters

    blocking_provider = BlockingProvider()
    service = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": blocking_provider},
    )
    refreshing = [
        accounts.create("codex", f"Blocking {index}") for index in range(2)
    ]
    deleting = accounts.create("codex", "Delete me")
    ready = accounts.set_state(deleting.id, "ready")
    cached = _snapshot(deleting.id)
    cache.save(cached)
    profile = paths.account_home(deleting.provider, deleting.id) / "auth.json"
    profile.write_bytes(b"secret-profile")
    first, first_outcome = _start_thread(lambda: service.refresh(refreshing[0].id))
    second, second_outcome = _start_thread(lambda: service.refresh(refreshing[1].id))
    blocking_provider.wait_for_entries(2)
    cancel_event = threading.Event()
    deleting_thread, delete_outcome = _start_thread(
        lambda: service.delete_account(
            deleting.id,
            force_local=True,
            cancel_event=cancel_event,
        )
    )
    _wait_for_provider_waiters(service, count=1, timeout=2)

    cancel_event.set()
    deleting_thread.join(timeout=2)
    try:
        assert not deleting_thread.is_alive()
        assert isinstance(delete_outcome["error"], ProviderError)
        assert delete_outcome["error"].code == "logout_cancelled"
        assert accounts.get(deleting.id) == ready
        assert profile.read_bytes() == b"secret-profile"
        assert cache.load(deleting.id) == cached
        assert ("logout", deleting.id) not in blocking_provider.events
    finally:
        blocking_provider.release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        assert first_outcome["error"] is None
        assert second_outcome["error"] is None


def test_force_local_delete_provider_cancellation_preserves_all_local_state(
    service, provider, accounts, cache, account, paths
):
    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"secret-profile")
    provider.fail_with(
        "logout",
        ProviderError("logout_cancelled", "logout cancelled"),
    )

    with pytest.raises(ProviderError) as captured:
        service.delete_account(account.id, force_local=True)

    assert captured.value.code == "logout_cancelled"
    assert accounts.get(account.id) == ready
    assert profile.read_bytes() == b"secret-profile"
    assert cache.load(account.id) == cached


def test_force_local_delete_signaled_cancellation_after_logout_preserves_local_state(
    service, provider, accounts, cache, account, paths
):
    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"secret-profile")
    cancel_event = threading.Event()
    provider.on_logout = lambda current: cancel_event.set()
    point_of_no_return_calls = []

    with pytest.raises(ProviderError) as captured:
        service.delete_account(
            account.id,
            force_local=True,
            cancel_event=cancel_event,
            mark_point_of_no_return=lambda: point_of_no_return_calls.append(True),
        )

    assert captured.value.code == "logout_cancelled"
    assert accounts.get(account.id) == ready
    assert profile.read_bytes() == b"secret-profile"
    assert cache.load(account.id) == cached
    assert point_of_no_return_calls == []


def test_delete_cancellation_after_first_staged_tree_finishes_deletion(
    service, provider, accounts, cache, account, paths, monkeypatch
):
    import dotsync.usage.deletion as deletion_module

    accounts.set_state(account.id, "ready")
    cache.save(_snapshot(account.id))
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    cancel_event = threading.Event()
    real_move = deletion_module.move_private_tree

    def cancel_after_profile_stage(source, destination, *, allowed_root):
        real_move(source, destination, allowed_root=allowed_root)
        if source == paths.account_root(account.provider, account.id):
            cancel_event.set()

    monkeypatch.setattr(
        deletion_module,
        "move_private_tree",
        cancel_after_profile_stage,
    )

    service.delete_account(
        account.id,
        force_local=False,
        cancel_event=cancel_event,
    )

    assert cancel_event.is_set()
    assert provider.events == [("logout", account.id)]
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    assert not _deletion_root(paths, account.id).exists()


def test_delete_marks_point_of_no_return_before_local_staging(
    service, accounts, cache, account, paths
):
    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    observed = []

    def mark_point_of_no_return():
        observed.append(
            (
                accounts.get(account.id),
                profile.read_bytes(),
                cache.load(account.id),
                _deletion_root(paths, account.id).exists(),
            )
        )

    service.delete_account(
        account.id,
        force_local=False,
        mark_point_of_no_return=mark_point_of_no_return,
    )

    assert observed == [(ready, b"profile-secret-bytes", cached, False)]
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)


def test_delete_cancellation_after_complete_staging_finishes_metadata_commit(
    service, provider, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import AccountDeletion

    accounts.set_state(account.id, "ready")
    cache.save(_snapshot(account.id))
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    cancel_event = threading.Event()
    real_stage = AccountDeletion.stage

    def cancel_before_metadata_commit(deletion):
        real_stage(deletion)
        assert deletion.staged_profile.exists()
        assert deletion.staged_cache.exists()
        assert accounts.get(account.id).state == "ready"
        cancel_event.set()

    monkeypatch.setattr(AccountDeletion, "stage", cancel_before_metadata_commit)

    service.delete_account(
        account.id,
        force_local=False,
        cancel_event=cancel_event,
    )

    assert cancel_event.is_set()
    assert provider.events == [("logout", account.id)]
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    assert not _deletion_root(paths, account.id).exists()


def test_delete_real_failure_after_late_cancellation_stays_recoverable(
    service, provider, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionRecoveryError
    import dotsync.usage.deletion as deletion_module

    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    cancel_event = threading.Event()
    real_move = deletion_module.move_private_tree

    def cancel_then_fail_cache_stage(source, destination, *, allowed_root):
        if source == paths.account_root(account.provider, account.id):
            real_move(source, destination, allowed_root=allowed_root)
            cancel_event.set()
            return
        if source == _cache_root(paths, account.id):
            raise OSError("cache-stage-sentinel-must-not-escape")
        real_move(source, destination, allowed_root=allowed_root)

    monkeypatch.setattr(
        deletion_module,
        "move_private_tree",
        cancel_then_fail_cache_stage,
    )

    with pytest.raises(DeletionRecoveryError, match="quarantined") as captured:
        service.delete_account(
            account.id,
            force_local=False,
            cancel_event=cancel_event,
        )

    assert "cache-stage-sentinel" not in str(captured.value)
    assert cancel_event.is_set()
    assert provider.events == [("logout", account.id)]
    assert accounts.get(account.id) == ready
    assert not paths.account_root(account.provider, account.id).exists()
    assert (
        _deletion_root(paths, account.id) / "profile" / "home" / "auth.json"
    ).read_bytes() == b"profile-secret-bytes"
    assert cache.load(account.id) == cached
    assert (_deletion_root(paths, account.id) / "manifest.json").is_file()


def test_delete_prevalidates_cache_before_mutating_profile_or_metadata(
    service, provider, accounts, cache, account, paths, tmp_path
):
    ready = accounts.set_state(account.id, "ready")
    cache.save(_snapshot(account.id))
    profile_sentinel = paths.account_home(account.provider, account.id) / "auth.json"
    profile_sentinel.write_bytes(b"profile-secret-bytes")
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    outside_sentinel = outside / "keep"
    outside_sentinel.write_text("keep")
    (_cache_root(paths, account.id) / "link").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        service.delete_account(account.id, force_local=False)

    assert provider.events == []
    assert accounts.get(account.id) == ready
    assert profile_sentinel.read_bytes() == b"profile-secret-bytes"
    assert outside_sentinel.read_text() == "keep"
    assert _cache_root(paths, account.id).exists()


def test_delete_prevalidates_staging_before_provider_logout(
    service, provider, accounts, cache, account, paths, tmp_path
):
    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    outside_sentinel = outside / "keep"
    outside_sentinel.write_text("keep")
    staging = _deletion_root(paths, account.id)
    staging.mkdir(parents=True)
    (staging / "profile").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        service.delete_account(account.id, force_local=False)

    assert provider.events == []
    assert accounts.get(account.id) == ready
    assert paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) == cached
    assert outside_sentinel.read_text() == "keep"


def test_delete_cache_staging_failure_quarantines_profile_and_retry_resumes(
    service, provider, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionRecoveryError
    import dotsync.usage.deletion as deletion_module

    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile_sentinel = paths.account_home(account.provider, account.id) / "auth.json"
    profile_sentinel.write_bytes(b"profile-secret-bytes")
    real_move = deletion_module.move_private_tree

    def fail_cache_stage(source, destination, *, allowed_root):
        if source == _cache_root(paths, account.id):
            raise OSError("cache staging interrupted")
        return real_move(source, destination, allowed_root=allowed_root)

    monkeypatch.setattr(deletion_module, "move_private_tree", fail_cache_stage)

    with pytest.raises(DeletionRecoveryError, match="quarantined") as captured:
        service.delete_account(account.id, force_local=False)

    assert "cache staging interrupted" not in str(captured.value)
    assert accounts.get(account.id) == ready
    assert not paths.account_root(account.provider, account.id).exists()
    assert (
        _deletion_root(paths, account.id) / "profile" / "home" / "auth.json"
    ).read_bytes() == b"profile-secret-bytes"
    assert cache.load(account.id) == cached
    assert (_deletion_root(paths, account.id) / "manifest.json").is_file()
    assert provider.events == [("logout", account.id)]

    monkeypatch.setattr(deletion_module, "move_private_tree", real_move)
    service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not _deletion_root(paths, account.id).exists()
    assert cache.load(account.id) is None
    assert provider.events == [("logout", account.id)]


def test_delete_post_move_unsafe_profile_stays_quarantined(
    service, provider, accounts, cache, account, paths, tmp_path, monkeypatch
):
    from dotsync.usage.deletion import DeletionRecoveryError
    import dotsync.private_fs as private_fs

    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    outside = tmp_path / "outside-injected"
    outside.mkdir()
    outside_sentinel = outside / "keep.txt"
    outside_sentinel.write_text("keep")
    staged_profile = _deletion_root(paths, account.id) / "profile"
    real_rename = private_fs.os.rename
    injected = False

    def inject_after_profile_move(src, dst, **kwargs):
        nonlocal injected
        real_rename(src, dst, **kwargs)
        if not injected and src == account.id and dst == "profile":
            injected = True
            (staged_profile / "link").symlink_to(
                outside,
                target_is_directory=True,
            )

    monkeypatch.setattr(private_fs.os, "rename", inject_after_profile_move)

    with pytest.raises(DeletionRecoveryError, match="quarantined"):
        service.delete_account(account.id, force_local=False)

    assert provider.events == [("logout", account.id)]
    assert accounts.get(account.id) == ready
    assert not paths.account_root(account.provider, account.id).exists()
    assert staged_profile.exists()
    assert (staged_profile / "link").is_symlink()
    assert (_deletion_root(paths, account.id) / "manifest.json").is_file()
    assert cache.load(account.id) == cached
    assert outside_sentinel.read_text() == "keep"


def test_delete_metadata_save_failure_quarantines_staged_trees_and_retry_resumes(
    service, provider, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionRecoveryError

    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    profile_sentinel = paths.account_home(account.provider, account.id) / "auth.json"
    profile_sentinel.write_bytes(b"profile-secret-bytes")
    real_save = accounts._save

    def fail_delete(records):
        if records == []:
            raise OSError("metadata save interrupted")
        return real_save(records)

    monkeypatch.setattr(accounts, "_save", fail_delete)

    with pytest.raises(DeletionRecoveryError, match="quarantined") as captured:
        service.delete_account(account.id, force_local=False)

    assert "metadata save interrupted" not in str(captured.value)
    assert accounts.get(account.id) == ready
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    assert (
        _deletion_root(paths, account.id) / "profile" / "home" / "auth.json"
    ).read_bytes() == b"profile-secret-bytes"
    assert (_deletion_root(paths, account.id) / "cache").exists()
    assert (_deletion_root(paths, account.id) / "manifest.json").is_file()
    assert provider.events == [("logout", account.id)]

    monkeypatch.setattr(accounts, "_save", real_save)
    service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not _deletion_root(paths, account.id).exists()
    assert provider.events == [("logout", account.id)]


def test_delete_uses_metadata_oracle_after_ambiguous_commit_error(
    service, accounts, cache, account, paths, monkeypatch
):
    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    real_delete_metadata = accounts.delete_metadata

    def commit_then_report_io_error(account_id):
        real_delete_metadata(account_id)
        raise OSError("metadata fsync outcome was ambiguous")

    monkeypatch.setattr(accounts, "delete_metadata", commit_then_report_io_error)

    service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    assert not _deletion_root(paths, account.id).exists()


def test_delete_directory_fsync_failure_leaves_commit_oracle_recoverable(
    service, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionCleanupPending
    import dotsync.private_fs as private_fs

    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    real_replace = private_fs.os.replace
    real_fsync = private_fs.os.fsync
    registry_replaced = False

    def observe_registry_replace(*args, **kwargs):
        nonlocal registry_replaced
        real_replace(*args, **kwargs)
        if len(args) >= 2 and args[1] == "accounts.json":
            registry_replaced = True

    def fail_registry_directory_fsync(descriptor):
        if registry_replaced and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("registry directory fsync interrupted")
        real_fsync(descriptor)

    monkeypatch.setattr(private_fs.os, "replace", observe_registry_replace)
    monkeypatch.setattr(private_fs.os, "fsync", fail_registry_directory_fsync)

    with pytest.raises(DeletionCleanupPending, match="uncertain"):
        service.delete_account(account.id, force_local=False)

    assert (_deletion_root(paths, account.id) / "manifest.json").is_file()
    assert (_deletion_root(paths, account.id) / "profile").exists()
    assert (_deletion_root(paths, account.id) / "cache").exists()

    monkeypatch.setattr(private_fs.os, "replace", real_replace)
    monkeypatch.setattr(private_fs.os, "fsync", real_fsync)
    service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not _deletion_root(paths, account.id).exists()


def test_delete_retry_commits_precommit_staging_without_restoring_or_logout(
    service, provider, accounts, cache, account, paths
):
    from dotsync.usage.deletion import AccountDeletion

    cached = _snapshot(account.id)
    cache.save(cached)
    profile = paths.account_home(account.provider, account.id) / "auth.json"
    profile.write_bytes(b"profile-secret-bytes")
    interrupted = AccountDeletion.begin(paths, account)
    interrupted.stage()
    assert not profile.exists()
    assert cache.load(account.id) is None
    cancel_event = threading.Event()
    cancel_event.set()
    point_of_no_return_calls = []

    service.delete_account(
        account.id,
        force_local=False,
        cancel_event=cancel_event,
        mark_point_of_no_return=lambda: point_of_no_return_calls.append(True),
    )

    assert provider.events == []
    assert point_of_no_return_calls == [True]
    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not profile.exists()
    assert cache.load(account.id) is None
    assert not _deletion_root(paths, account.id).exists()


def test_delete_cleanup_failure_is_committed_and_retry_scrubs_staging(
    service, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionCleanupPending
    import dotsync.usage.deletion as deletion_module

    cache.save(_snapshot(account.id))
    real_remove = deletion_module.remove_private_tree

    def fail_staged_cleanup(path, *, allowed_root):
        if path == _deletion_root(paths, account.id):
            raise OSError("staged cleanup interrupted")
        return real_remove(path, allowed_root=allowed_root)

    monkeypatch.setattr(deletion_module, "remove_private_tree", fail_staged_cleanup)

    with pytest.raises(DeletionCleanupPending, match="committed"):
        service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not paths.account_root(account.provider, account.id).exists()
    assert cache.load(account.id) is None
    assert _deletion_root(paths, account.id).exists()

    monkeypatch.setattr(deletion_module, "remove_private_tree", real_remove)
    service.delete_account(account.id, force_local=False)

    assert not _deletion_root(paths, account.id).exists()


def test_delete_partial_committed_cleanup_retains_manifest_for_retry(
    service, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionCleanupPending
    import dotsync.usage.deletion as deletion_module

    cache.save(_snapshot(account.id))
    staged_profile = _deletion_root(paths, account.id) / "profile"
    staged_cache = _deletion_root(paths, account.id) / "cache"
    manifest = _deletion_root(paths, account.id) / "manifest.json"
    real_remove = deletion_module.remove_private_tree

    def fail_after_removing_profile(path, *, allowed_root):
        if path == staged_profile:
            real_remove(path, allowed_root=allowed_root)
            raise OSError("profile payload removed before interruption")
        return real_remove(path, allowed_root=allowed_root)

    monkeypatch.setattr(
        deletion_module,
        "remove_private_tree",
        fail_after_removing_profile,
    )

    with pytest.raises(DeletionCleanupPending, match="committed"):
        service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert not staged_profile.exists()
    assert staged_cache.exists()
    assert manifest.is_file()

    monkeypatch.setattr(deletion_module, "remove_private_tree", real_remove)
    service.delete_account(account.id, force_local=False)

    assert not _deletion_root(paths, account.id).exists()


def test_delete_retry_removes_empty_staging_after_final_rmdir_failure(
    service, accounts, cache, account, paths, monkeypatch
):
    from dotsync.usage.deletion import DeletionCleanupPending
    import dotsync.private_fs as private_fs

    cache.save(_snapshot(account.id))
    deletion_root = _deletion_root(paths, account.id)
    real_rmdir = private_fs.os.rmdir
    failed = False

    def fail_final_transaction_rmdir(name, *, dir_fd=None):
        nonlocal failed
        if name == account.id and not failed:
            failed = True
            raise OSError("final transaction rmdir interrupted")
        real_rmdir(name, dir_fd=dir_fd)

    monkeypatch.setattr(private_fs.os, "rmdir", fail_final_transaction_rmdir)

    with pytest.raises(DeletionCleanupPending, match="committed"):
        service.delete_account(account.id, force_local=False)

    with pytest.raises(AccountNotFound):
        accounts.get(account.id)
    assert deletion_root.is_dir()
    assert list(deletion_root.iterdir()) == []

    monkeypatch.setattr(private_fs.os, "rmdir", real_rmdir)
    service.delete_account(account.id, force_local=False)

    assert not deletion_root.exists()


def test_delete_rejects_nonempty_manifestless_staging_before_provider_logout(
    service, provider, accounts, cache, account, paths
):
    from dotsync.usage.deletion import DeletionRecoveryError

    ready = accounts.set_state(account.id, "ready")
    cached = _snapshot(account.id)
    cache.save(cached)
    deletion_root = _deletion_root(paths, account.id)
    deletion_root.mkdir(parents=True)
    unexpected = deletion_root / "unexpected"
    unexpected.write_text("do-not-trust")

    with pytest.raises(DeletionRecoveryError, match="missing its manifest"):
        service.delete_account(account.id, force_local=False)

    assert provider.events == []
    assert accounts.get(account.id) == ready
    assert cache.load(account.id) == cached
    assert unexpected.read_text() == "do-not-trust"


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
    assert accounts.get(account.id).state == "ready"
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


def _cache_root(paths: AppPaths, account_id: str):
    return paths.usage / account_id


def _deletion_root(paths: AppPaths, account_id: str):
    return paths.root / ".deletions" / account_id


def _case_alias_paths(tmp_path) -> tuple[AppPaths, AppPaths]:
    root = tmp_path / "DotSyncIdentity"
    root.mkdir(mode=0o700)
    alias = tmp_path / "dotsyncidentity"
    if not alias.exists() or not os.path.samestat(root.stat(), alias.stat()):
        pytest.skip("requires a case-insensitive filesystem alias")
    return AppPaths(root), AppPaths(alias)
