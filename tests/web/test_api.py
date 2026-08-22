from __future__ import annotations

import hashlib
import http.client
import json
import socket
import stat
import threading
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import dotsync.config as config_module
import dotsync.private_fs as private_fs_module
import dotsync.web.api as api_module
from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState, AppStateStore
from dotsync.apps import build_app
from dotsync.apps.base import App, AppStatus, FilePair
from dotsync.config import Config, save_config
from dotsync.jobs import JobContext, JobView, RegistryClosed
from dotsync.providers import LoginProgress
from dotsync.sync_service import StaleSyncPlan, SyncAppStatus, SyncService, SyncStatus
from dotsync.usage import UsageResult, UsageSnapshot, UsageWindow
from dotsync.web import WebApplication, run_ui_server


_UNSET = object()
_DEEPLY_PERCENT_ENCODED_LOCATOR = (
    "https%2525253A%2525252F%2525252Foauth.invalid%2525252Ftoken"
)


class _StateStore:
    def __init__(self, events: list[str]) -> None:
        self.state = AppState()
        self.events = events

    def load(self) -> AppState:
        return self.state

    def save(self, state: AppState) -> None:
        self.events.append("state_saved")
        self.state = state


class _DurableStateStore:
    def __init__(self, store: AppStateStore, events: list[str]) -> None:
        self._store = store
        self.events = events

    @property
    def state(self) -> AppState:
        return self.load()

    def load(self) -> AppState:
        return self._store.load()

    def save(self, state: AppState) -> None:
        self.events.append("state_saved")
        self._store.save(state)


class _AccountStore:
    def __init__(self) -> None:
        self.accounts: dict[str, ManagedAccount] = {}
        self.get_calls: list[str] = []

    def get(self, account_id: str) -> ManagedAccount:
        self.get_calls.append(account_id)
        return self.accounts[account_id]


class _UsageService:
    def __init__(self, accounts: _AccountStore) -> None:
        self.accounts = accounts
        self.calls: list[tuple[object, ...]] = []
        self.delete_kwargs: dict[str, object] | None = None
        self.raise_on_create: BaseException | None = None
        self.refresh_error_code: str | None = None
        self.cached_snapshots: dict[str, UsageSnapshot] = {}
        self.login_gate: threading.Event | None = None
        self.login_started = 0
        self.login_condition = threading.Condition()

    def create_account(self, provider: str, label: str) -> ManagedAccount:
        self.calls.append(("create", provider, label))
        if self.raise_on_create is not None:
            raise self.raise_on_create
        account = _account(provider=provider, label=label)
        self.accounts.accounts[account.id] = account
        return account

    def list_accounts(self) -> list[ManagedAccount]:
        self.calls.append(("list",))
        return list(self.accounts.accounts.values())

    def cached_usage(self, account_id: str) -> UsageSnapshot | None:
        self.calls.append(("cached_usage", account_id))
        return self.cached_snapshots.get(account_id)

    def rename_account(self, account_id: str, label: str) -> ManagedAccount:
        self.calls.append(("rename", account_id, label))
        current = self.accounts.accounts[account_id]
        renamed = ManagedAccount(
            id=current.id,
            provider=current.provider,
            label=label,
            state=current.state,
            identity=current.identity,
            created_at=current.created_at,
        )
        self.accounts.accounts[account_id] = renamed
        return renamed

    def login(self, account_id: str, report, *, cancel_event=None) -> ManagedAccount:
        self.calls.append(("login", account_id, cancel_event))
        if self.login_gate is not None:
            with self.login_condition:
                self.login_started += 1
                self.login_condition.notify_all()
            assert self.login_gate.wait(timeout=1.0)
        report(LoginProgress("starting"))
        report(
            LoginProgress(
                "waiting_for_browser",
                "https://oauth.invalid/sentinel-callback",
                "secret-code",
            )
        )
        report(LoginProgress("done"))
        current = self.accounts.accounts[account_id]
        ready = ManagedAccount(
            id=current.id,
            provider=current.provider,
            label=current.label,
            state="ready",
            identity=ProviderIdentity("A User", "a@example.test", "plus"),
            created_at=current.created_at,
        )
        self.accounts.accounts[account_id] = ready
        return ready

    def refresh(self, account_id: str, *, cancel_event=None) -> UsageResult:
        self.calls.append(("refresh", account_id, cancel_event))
        return UsageResult(
            snapshot=_snapshot(account_id),
            stale=self.refresh_error_code is not None,
            error_code=self.refresh_error_code,
        )

    def logout(self, account_id: str, *, cancel_event=None) -> ManagedAccount:
        self.calls.append(("logout", account_id, cancel_event))
        current = self.accounts.accounts[account_id]
        logged_out = ManagedAccount(
            id=current.id,
            provider=current.provider,
            label=current.label,
            state="logged_out",
            identity=current.identity,
            created_at=current.created_at,
        )
        self.accounts.accounts[account_id] = logged_out
        return logged_out

    def delete_account(self, account_id: str, **kwargs) -> None:
        self.calls.append(("delete", account_id))
        self.delete_kwargs = dict(kwargs)
        self.accounts.accounts.pop(account_id)


class _SyncPreview:
    def __init__(self, digest: str, direction: str, apps: tuple[str, ...]) -> None:
        self.digest = digest
        self.direction = direction
        self.apps = apps

    def to_dict(self) -> dict[str, object]:
        return {
            "direction": self.direction,
            "apps": list(self.apps),
            "plans": [],
            "sync_dir": {"scope": "sync-root", "id": "sha256:safe"},
            "digest": self.digest,
        }


class _SyncResult:
    direction = "backup"
    changed = ("zsh",)
    unchanged: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    duration_ms = 7


class _SyncService:
    def __init__(self, sync_dir: Path) -> None:
        self.config = Config(dir=sync_dir, apps=["zsh"])
        self.calls: list[tuple[object, ...]] = []
        self.next_digest = "a" * 64
        self.stale = False
        self.preview_error: BaseException | None = None
        self.status_result: SyncStatus | None = None
        self.preview_entered = threading.Event()
        self.preview_release: threading.Event | None = None
        self.update_entered = threading.Event()
        self.update_release: threading.Event | None = None
        self.factory_error: BaseException | None = None
        self.candidates: list[_SyncService] = []

    def status(self) -> SyncStatus:
        self.calls.append(("status",))
        if self.status_result is not None:
            return self.status_result
        return SyncStatus(sync_dir=self.config.dir, apps=())

    def update_apps(self, apps: tuple[str, ...]):
        self.calls.append(("update_apps", apps))
        self.update_entered.set()
        if self.update_release is not None:
            assert self.update_release.wait(timeout=2.0)
        self.config.apps = list(apps)
        return self.config

    def with_config(self, config: Config) -> _SyncService:
        self.calls.append(("with_config", tuple(config.apps)))
        self.update_entered.set()
        if self.update_release is not None:
            assert self.update_release.wait(timeout=2.0)
        if self.factory_error is not None:
            raise self.factory_error
        candidate = _SyncService(config.dir)
        candidate.config = config
        self.candidates.append(candidate)
        return candidate

    def validate_config(self) -> None:
        self.calls.append(("validate_config", tuple(self.config.apps)))
        for name in self.config.apps:
            build_app(name, self.config)

    def preview(self, direction: str, apps: tuple[str, ...]) -> _SyncPreview:
        self.calls.append(("preview", direction, apps))
        self.preview_entered.set()
        if self.preview_release is not None:
            assert self.preview_release.wait(timeout=2.0)
        if self.preview_error is not None:
            raise self.preview_error
        return _SyncPreview(self.next_digest, direction, apps)

    def execute(self, digest: str) -> _SyncResult:
        self.calls.append(("execute", digest))
        if self.stale:
            raise StaleSyncPlan("private stale details")
        return _SyncResult()


@dataclass
class _Picker:
    selected: Path | None = None
    calls: int = 0

    def __call__(self) -> Path | None:
        self.calls += 1
        return self.selected


class _Initializer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.services: list[_SyncService] = []
        self.effects: list[object] = []
        self.arguments: list[tuple[object, ...]] = []

    def __call__(self, *arguments: object) -> _SyncService:
        self.arguments.append(arguments)
        effect = self.effects.pop(0) if self.effects else None
        if (
            type(effect) is tuple
            and len(effect) == 2
            and all(isinstance(item, threading.Event) for item in effect)
        ):
            gate = effect
            entered, release = gate
            entered.set()
            assert release.wait(timeout=2.0)
        elif isinstance(effect, BaseException):
            raise effect
        elif callable(effect):
            effect(*arguments)
        service = _SyncService(Path("/dev/null"))
        self.services.append(service)
        self.events.append("folder_candidate_built")
        return service


class _Response:
    def __init__(self, response: http.client.HTTPResponse) -> None:
        self.status = response.status
        self.headers = response.getheaders()
        self.body = response.read()

    def json(self) -> dict[str, object]:
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        values = [value for key, value in self.headers if key.lower() == name.lower()]
        assert len(values) <= 1
        return values[0] if values else None


@dataclass
class _PendingResponse:
    thread: threading.Thread
    done: threading.Event
    responses: list[_Response]
    errors: list[BaseException]

    def finish(self) -> _Response:
        assert self.done.wait(timeout=2.0)
        self.thread.join(timeout=2.0)
        assert not self.thread.is_alive()
        if self.errors:
            raise self.errors[0]
        assert len(self.responses) == 1
        return self.responses[0]


def _start_request(call: Callable[[], _Response]) -> _PendingResponse:
    done = threading.Event()
    responses: list[_Response] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            responses.append(call())
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return _PendingResponse(thread, done, responses, errors)


class _Client:
    def __init__(self, server, token: str) -> None:
        self.server = server
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: object = _UNSET,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Response:
        request_headers = {
            "Host": f"127.0.0.1:{self.server.server_address[1]}",
            "X-DotSync-Token": self.token,
            **(headers or {}),
        }
        if json_body is not _UNSET:
            body = json.dumps(
                json_body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1]
        )
        connection.request(method, path, body=body, headers=request_headers)
        response = _Response(connection.getresponse())
        connection.close()
        return response


@dataclass
class _Stack:
    application: WebApplication
    client: _Client
    server: object
    paths: AppPaths
    state: _StateStore
    accounts: _AccountStore
    usage: _UsageService
    sync: _SyncService
    picker: _Picker
    revealed: list[Path]
    opened_urls: list[str]
    initialized_services: list[_SyncService]
    initializer: _Initializer


@pytest.fixture
def stack(tmp_path):
    events: list[str] = []
    paths = AppPaths(tmp_path / "app-data")
    state = _StateStore(events)
    accounts = _AccountStore()
    usage = _UsageService(accounts)
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    sync = _SyncService(sync_dir)
    picker = _Picker()
    revealed: list[Path] = []
    opened_urls: list[str] = []
    initializer = _Initializer(events)
    observed_now = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
    utc_clock = lambda: observed_now

    application = WebApplication(
        paths=paths,
        state_store=state,
        account_store=accounts,
        usage_service=usage,
        sync_service=sync,
        folder_picker=picker,
        sync_folder_initializer=initializer,
        reveal_app_data=revealed.append,
        open_provider_url=opened_urls.append,
        utc_clock=utc_clock,
    )
    server = run_ui_server(application, poll_interval=0.01)
    value = _Stack(
        application=application,
        client=_Client(server, application.token),
        server=server,
        paths=paths,
        state=state,
        accounts=accounts,
        usage=usage,
        sync=sync,
        picker=picker,
        revealed=revealed,
        opened_urls=opened_urls,
        initialized_services=initializer.services,
        initializer=initializer,
    )
    try:
        yield value
    finally:
        server.close()


@pytest.fixture
def durable_stack(tmp_path):
    events: list[str] = []
    paths = AppPaths(tmp_path / "app-data")
    state = _DurableStateStore(AppStateStore(paths), events)
    accounts = _AccountStore()
    usage = _UsageService(accounts)
    sync_dir = tmp_path / "old-sync"
    sync = _SyncService(sync_dir)
    save_config(sync.config)
    state.save(AppState(sync_dir=str(sync_dir)))
    events.clear()
    picker = _Picker()
    revealed: list[Path] = []
    opened_urls: list[str] = []
    initializer = _Initializer(events)

    application = WebApplication(
        paths=paths,
        state_store=state,
        account_store=accounts,
        usage_service=usage,
        sync_service=sync,
        folder_picker=picker,
        sync_folder_initializer=initializer,
        reveal_app_data=revealed.append,
        open_provider_url=opened_urls.append,
    )
    server = run_ui_server(application, poll_interval=0.01)
    value = _Stack(
        application=application,
        client=_Client(server, application.token),
        server=server,
        paths=paths,
        state=state,  # type: ignore[arg-type]
        accounts=accounts,
        usage=usage,
        sync=sync,
        picker=picker,
        revealed=revealed,
        opened_urls=opened_urls,
        initialized_services=initializer.services,
        initializer=initializer,
    )
    try:
        yield value
    finally:
        server.close()


def _account(
    *,
    provider: str = "codex",
    label: str = "Personal",
    identity: ProviderIdentity | None = None,
) -> ManagedAccount:
    return ManagedAccount(
        id=str(uuid.uuid4()),
        provider=provider,
        label=label,
        state="logged_out",
        identity=identity or ProviderIdentity(None, None, None),
        created_at="2026-08-21T00:00:00+00:00",
    )


def _snapshot(
    account_id: str,
    *,
    used_percent: float = 42.0,
    observed_at: str = "2026-08-21T00:00:00Z",
    provider: str = "codex",
) -> UsageSnapshot:
    return UsageSnapshot(
        account_id=account_id,
        provider=provider,
        windows=(
            UsageWindow(
                name="five_hour",
                limit_id="codex",
                label=None,
                used_percent=used_percent,
                duration_minutes=300,
                resets_at="2026-08-21T05:00:00Z",
            ),
        ),
        observed_at=observed_at,
        source="codex_app_server" if provider == "codex" else "claude_usage",
        provider_version="1.0.0",
    )


def _wait_for_job(stack: _Stack, job_id: str):
    return stack.application.jobs.wait(job_id, timeout=1.0)


def _poll_injected_job(stack: _Stack, monkeypatch, view: JobView) -> _Response:
    monkeypatch.setattr(stack.application.jobs, "get", lambda job_id: view)
    return stack.client.request("GET", f"/api/jobs/{view.id}")


def _assert_fixed_internal_error(response: _Response, *sentinels: str) -> None:
    assert response.status == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "DotSync could not complete the request.",
        }
    }
    for sentinel in sentinels:
        assert sentinel.encode() not in response.body


def _issue_sync_digest(stack: _Stack) -> str:
    response = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    assert response.status == 200
    return response.json()["preview"]["digest"]


def _disk_config_apps(sync_dir: Path) -> list[str]:
    with (sync_dir / "dotsync.toml").open("rb") as config_file:
        data = tomllib.load(config_file)
    return data["apps"]


def _assert_live_sync(
    stack: _Stack,
    *,
    sync_dir: Path,
    apps: list[str],
) -> None:
    status = stack.client.request("GET", "/api/sync/status")
    expected_id = "sha256:" + hashlib.sha256(
        str(sync_dir.absolute()).encode("utf-8")
    ).hexdigest()
    assert status.status == 200
    assert status.json()["sync"]["sync_dir"]["id"] == expected_id
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": apps},
    )
    assert preview.status == 200
    excluded = "ghostty" if apps == ["zsh"] else "zsh"
    rejected = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": [excluded]},
    )
    assert rejected.status == 400


def _set_sync_status(stack: _Stack, *states: str) -> None:
    names = ("zsh", "ghostty", "codex", "herdr")
    stack.sync.status_result = SyncStatus(
        sync_dir=stack.sync.config.dir,
        apps=tuple(
            SyncAppStatus(
                name=name,
                status=AppStatus(
                    state=state,
                    direction="local-newer" if state == "dirty" else "",
                ),
            )
            for name, state in zip(names[: len(states)], states, strict=True)
        ),
    )


def _add_account_with_snapshot(
    stack: _Stack,
    *,
    provider: str = "codex",
    label: str = "Personal",
    used_percent: float = 42.0,
    observed_at: str = "2026-08-21T09:00:00Z",
) -> ManagedAccount:
    account = _account(provider=provider, label=label)
    stack.accounts.accounts[account.id] = account
    stack.usage.cached_snapshots[account.id] = _snapshot(
        account.id,
        used_percent=used_percent,
        observed_at=observed_at,
        provider=provider,
    )
    return account


def test_menu_summary_reads_cache_without_provider_work_or_identity_leakage(stack):
    account = _add_account_with_snapshot(
        stack,
        label="Codex Personal",
        used_percent=72.0,
    )
    _set_sync_status(stack, "dirty")
    assert stack.client.request("GET", "/api/sync/status").status == 200
    stack.usage.calls.clear()
    stack.sync.calls.clear()
    prior_state_events = list(stack.state.events)

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json() == {
        "usage": {"state": "fresh", "highest_percent": 72.0},
        "sync": {"state": "fresh", "attention_count": 1},
        "observed_at": "2026-08-21T09:00:00Z",
    }
    encoded = response.body.decode()
    assert account.id not in encoded
    assert account.label not in encoded
    assert str(stack.paths.root) not in encoded
    assert stack.usage.calls == [("list",), ("cached_usage", account.id)]
    assert stack.sync.calls == []
    assert stack.application.jobs.list_jobs() == []
    assert stack.state.events == prior_state_events


def test_menu_summary_fails_closed_for_cache_exception_without_error_detail(
    stack, monkeypatch
):
    _add_account_with_snapshot(stack, label="No cache")

    def fail_cache(account_id: str):
        raise ValueError("secret-cache-detail")

    monkeypatch.setattr(stack.usage, "cached_usage", fail_cache)

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "unknown",
        "highest_percent": None,
    }
    assert "secret-cache-detail" not in response.body.decode()


def test_menu_summary_reports_unknown_without_a_managed_codex_account(stack):
    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json() == {
        "usage": {"state": "unknown", "highest_percent": None},
        "sync": {"state": "unknown", "attention_count": None},
        "observed_at": None,
    }


def test_menu_summary_marks_an_old_cached_snapshot_stale(stack):
    _add_account_with_snapshot(
        stack,
        used_percent=64.0,
        observed_at="2026-08-21T08:44:59Z",
    )

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json() == {
        "usage": {"state": "stale", "highest_percent": 64.0},
        "sync": {"state": "unknown", "attention_count": None},
        "observed_at": "2026-08-21T08:44:59Z",
    }


def test_menu_summary_marks_mixed_fresh_and_missing_accounts_stale(stack):
    cached = _add_account_with_snapshot(stack, used_percent=35.0)
    missing = _account(label="Missing")
    stack.accounts.accounts[missing.id] = missing

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "stale",
        "highest_percent": 35.0,
    }
    assert stack.usage.calls == [
        ("list",),
        ("cached_usage", cached.id),
        ("cached_usage", missing.id),
    ]


@pytest.mark.parametrize("used_percent", [0.0, 100.0])
def test_menu_summary_preserves_valid_percentage_boundaries(stack, used_percent):
    _add_account_with_snapshot(stack, used_percent=used_percent)

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "fresh",
        "highest_percent": used_percent,
    }


@pytest.mark.parametrize("invalid_percentage", [float("nan"), float("inf")])
def test_menu_summary_fails_closed_for_non_finite_cached_percentages(
    stack, invalid_percentage
):
    account = _add_account_with_snapshot(stack)
    snapshot = stack.usage.cached_snapshots[account.id]
    object.__setattr__(snapshot.windows[0], "used_percent", invalid_percentage)

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "unknown",
        "highest_percent": None,
    }


@pytest.mark.parametrize("invalid_cache", [object(), {"used_percent": 72.0}])
def test_menu_summary_fails_closed_for_wrong_cached_dto_types(stack, invalid_cache):
    account = _account()
    stack.accounts.accounts[account.id] = account
    stack.usage.cached_snapshots[account.id] = invalid_cache

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "unknown",
        "highest_percent": None,
    }


def test_menu_summary_ignores_synthetic_claude_records(stack):
    claude = _add_account_with_snapshot(
        stack,
        provider="claude",
        label="Claude Secret",
        used_percent=99.0,
    )
    codex = _add_account_with_snapshot(stack, used_percent=18.0)

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "fresh",
        "highest_percent": 18.0,
    }
    assert stack.usage.calls == [("list",), ("cached_usage", codex.id)]
    assert claude.id not in response.body.decode()
    assert claude.label not in response.body.decode()


def test_menu_summary_counts_non_clean_apps_without_retaining_names(stack):
    _set_sync_status(stack, "clean", "dirty", "missing", "unknown")
    assert stack.client.request("GET", "/api/sync/status").status == 200

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["sync"] == {
        "state": "fresh",
        "attention_count": 3,
    }
    encoded = response.body.decode()
    for name in ("zsh", "ghostty", "codex", "herdr"):
        assert name not in encoded


def test_menu_summary_keeps_newest_concurrent_explicit_sync_observation(
    stack, monkeypatch
):
    older_clock_entered = threading.Event()
    release_older_clock = threading.Event()
    call_lock = threading.Lock()
    status_calls = 0
    clock_calls = 0

    def status():
        nonlocal status_calls
        with call_lock:
            status_calls += 1
            call = status_calls
        state = "dirty" if call == 1 else "clean"
        return SyncStatus(
            sync_dir=stack.sync.config.dir,
            apps=(
                SyncAppStatus(
                    name="zsh",
                    status=AppStatus(
                        state=state,
                        direction="local-newer" if state == "dirty" else "",
                    ),
                ),
            ),
        )

    def clock():
        nonlocal clock_calls
        with call_lock:
            clock_calls += 1
            call = clock_calls
        if call == 1:
            older_clock_entered.set()
            assert release_older_clock.wait(timeout=2.0)
            return datetime(2026, 8, 21, 8, 59, tzinfo=timezone.utc)
        return datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(stack.sync, "status", status)
    stack.application._api._clock = clock
    older = _start_request(
        lambda: stack.client.request("GET", "/api/sync/status")
    )
    assert older_clock_entered.wait(timeout=2.0)

    newer = stack.client.request("GET", "/api/sync/status")
    release_older_clock.set()
    older_response = older.finish()
    summary = stack.client.request("GET", "/api/menu-summary")

    assert newer.status == 200
    assert older_response.status == 200
    assert summary.json()["sync"] == {
        "state": "fresh",
        "attention_count": 0,
    }
    assert summary.json()["observed_at"] == "2026-08-21T09:00:00Z"


def test_menu_summary_marks_a_sync_observation_older_than_fifteen_minutes_stale(
    stack,
):
    _set_sync_status(stack, "clean")
    assert stack.client.request("GET", "/api/sync/status").status == 200
    stack.application._api._clock = lambda: datetime(
        2026, 8, 21, 9, 15, 1, tzinfo=timezone.utc
    )

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["sync"] == {
        "state": "stale",
        "attention_count": 0,
    }


def test_menu_summary_does_not_record_an_invalid_sync_status(stack):
    stack.sync.status_result = object()

    status = stack.client.request("GET", "/api/sync/status")
    summary = stack.client.request("GET", "/api/menu-summary")

    assert status.status == 500
    assert summary.status == 200
    assert summary.json()["sync"] == {
        "state": "unknown",
        "attention_count": None,
    }


def test_menu_summary_fails_closed_when_sync_observation_read_raises(
    stack, monkeypatch
):
    _set_sync_status(stack, "dirty")
    assert stack.client.request("GET", "/api/sync/status").status == 200

    def fail_observation():
        raise RuntimeError("private-sync-detail")

    monkeypatch.setattr(
        stack.application._api,
        "_safe_sync_attention_observation",
        fail_observation,
        raising=False,
    )

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["sync"] == {
        "state": "unknown",
        "attention_count": None,
    }
    assert "private-sync-detail" not in response.body.decode()


def test_menu_summary_fails_closed_when_utc_clock_raises(stack):
    _add_account_with_snapshot(stack, used_percent=88.0)

    def fail_clock():
        raise RuntimeError("private-clock-detail")

    stack.application._api._clock = fail_clock

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json() == {
        "usage": {"state": "unknown", "highest_percent": None},
        "sync": {"state": "unknown", "attention_count": None},
        "observed_at": None,
    }
    assert "private-clock-detail" not in response.body.decode()


def test_menu_summary_invalidates_sync_observation_after_app_transition(stack):
    _set_sync_status(stack, "clean")
    assert stack.client.request("GET", "/api/sync/status").status == 200

    updated = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["zsh", "ghostty"]},
    )
    summary = stack.client.request("GET", "/api/menu-summary")

    assert updated.status == 200
    assert summary.json()["sync"] == {
        "state": "unknown",
        "attention_count": None,
    }


def test_menu_summary_invalidates_sync_observation_after_folder_transition(
    stack, tmp_path
):
    _set_sync_status(stack, "clean")
    assert stack.client.request("GET", "/api/sync/status").status == 200
    selected_dir = tmp_path / "replacement-sync"
    selected_dir.mkdir()
    stack.picker.selected = selected_dir

    selected = stack.client.request("POST", "/api/settings/sync-folder/select")
    summary = stack.client.request("GET", "/api/menu-summary")

    assert selected.status == 200
    assert summary.json()["sync"] == {
        "state": "unknown",
        "attention_count": None,
    }


def test_menu_summary_invalidates_sync_observation_after_execute_transition(stack):
    _set_sync_status(stack, "clean")
    assert stack.client.request("GET", "/api/sync/status").status == 200
    digest = _issue_sync_digest(stack)

    accepted = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": digest},
    )
    summary = stack.client.request("GET", "/api/menu-summary")

    assert accepted.status == 202
    assert summary.json()["sync"] == {
        "state": "unknown",
        "attention_count": None,
    }


def test_bootstrap_reports_claude_policy_disabled_and_codex_available(stack):
    response = stack.client.request("GET", "/api/bootstrap")

    assert response.status == 200
    assert response.json() == {
        "providers": {
            "claude": {
                "enabled": False,
                "status": "policy_disabled",
                "message": "Claude account management is disabled by current policy.",
            },
            "codex": {
                "enabled": True,
                "status": "available",
                "message": None,
            },
        },
        "sync_configured": True,
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/no-such-route"),
        ("GET", "/api/accounts/"),
        ("GET", "/api/health?probe=1"),
    ],
)
def test_absent_or_nonexact_routes_return_404(stack, method, path):
    response = stack.client.request(method, path)

    assert response.status == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/health"),
        ("PUT", "/api/accounts"),
        ("OPTIONS", "/api/sync/status"),
    ],
)
def test_known_routes_with_unsupported_methods_return_405(stack, method, path):
    response = stack.client.request(method, path)

    assert response.status == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_json_mutations_require_a_single_bounded_content_length(stack):
    oversized = b'{"label":"' + (b"x" * (65_536)) + b'"}'

    response = stack.client.request(
        "PATCH",
        f"/api/accounts/{uuid.uuid4()}",
        body=oversized,
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_duplicate_content_length_is_rejected_before_body_parsing(stack):
    port = stack.server.server_address[1]
    body = b'{"provider":"codex","label":"Personal"}'
    request = (
        "POST /api/accounts HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"X-DotSync-Token: {stack.application.token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body

    with socket.create_connection(stack.server.server_address, timeout=2.0) as sock:
        sock.sendall(request)
        response = http.client.HTTPResponse(sock)
        response.begin()
        result = _Response(response)

    assert result.status == 400
    assert result.json()["error"]["code"] == "invalid_request"
    assert stack.usage.calls == []


def test_arbitrarily_long_content_length_is_rejected_without_integer_conversion(stack):
    port = stack.server.server_address[1]
    request = (
        "POST /api/accounts HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"X-DotSync-Token: {stack.application.token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {'9' * 5_000}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")

    with socket.create_connection(stack.server.server_address, timeout=2.0) as sock:
        sock.sendall(request)
        response = http.client.HTTPResponse(sock)
        response.begin()
        result = _Response(response)

    assert result.status == 413
    assert result.json()["error"]["code"] == "request_too_large"
    assert stack.usage.calls == []


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b'\xff', "application/json"),
        (b"[]", "application/json"),
        (b"{}", "text/plain"),
        (b'{"provider":NaN,"label":"x"}', "application/json"),
    ],
)
def test_json_body_requires_utf8_object_json(stack, body, content_type):
    response = stack.client.request(
        "POST",
        "/api/accounts",
        body=body,
        headers={"Content-Type": content_type},
    )

    assert response.status in {400, 415}
    assert response.json()["error"]["code"] in {
        "invalid_request",
        "unsupported_media_type",
    }
    assert stack.usage.calls == []


def test_json_objects_with_duplicate_keys_are_rejected(stack):
    response = stack.client.request(
        "POST",
        "/api/accounts",
        body=(
            b'{"provider":"claude","provider":"codex",'
            b'"label":"Personal"}'
        ),
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert stack.usage.calls == []


def test_create_account_rejects_extra_keys_before_claude_policy(stack):
    response = stack.client.request(
        "POST",
        "/api/accounts",
        json_body={
            "provider": "claude",
            "label": "Personal",
            "path": "/Users/me/.claude",
        },
    )

    assert response.status == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert stack.usage.calls == []


def test_valid_claude_account_create_is_policy_disabled_before_service_calls(stack):
    response = stack.client.request(
        "POST",
        "/api/accounts",
        json_body={"provider": "claude", "label": "Personal"},
    )

    assert response.status == 403
    assert response.json()["error"] == {
        "code": "provider_policy_disabled",
        "message": "Claude account management is disabled by current policy.",
    }
    assert stack.usage.calls == []


@pytest.mark.parametrize("provider", ["Claude", "CODEX", "unknown", 1, None])
def test_provider_values_are_canonical_enums(stack, provider):
    response = stack.client.request(
        "POST",
        "/api/accounts",
        json_body={"provider": provider, "label": "Personal"},
    )

    assert response.status == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_codex_account_create_and_list_use_nonsecret_exact_dtos(stack):
    created = stack.client.request(
        "POST",
        "/api/accounts",
        json_body={"provider": "codex", "label": "Personal"},
    )
    listed = stack.client.request("GET", "/api/accounts")

    assert created.status == 201
    account = created.json()["account"]
    assert set(account) == {
        "id",
        "provider",
        "label",
        "state",
        "identity",
        "created_at",
        "usage",
    }
    assert account["provider"] == "codex"
    assert account["label"] == "Personal"
    assert account["identity"] == {
        "display_name": None,
        "email": None,
        "plan": None,
    }
    assert account["usage"] is None
    assert listed.json() == {"accounts": [account]}
    assert "/Users/" not in listed.body.decode("utf-8")


def test_account_list_includes_only_the_sanitized_cached_usage_snapshot(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account
    stack.usage.cached_snapshots[account.id] = _snapshot(account.id)

    response = stack.client.request("GET", "/api/accounts")

    usage = response.json()["accounts"][0]["usage"]
    assert usage == {
        "account_id": account.id,
        "provider": "codex",
        "windows": [
            {
                "name": "five_hour",
                "limit_id": "codex",
                "label": None,
                "used_percent": 42.0,
                "duration_minutes": 300,
                "resets_at": "2026-08-21T05:00:00Z",
            }
        ],
        "observed_at": "2026-08-21T00:00:00Z",
        "source": "codex_app_server",
        "provider_version": "1.0.0",
    }
    serialized = response.body.decode("utf-8")
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized


@pytest.mark.parametrize(
    "display_name",
    [
        "~/Library/Application Support/Codex/auth.json",
        "localhost:1455/callback?code=oauth-secret",
        "127.0.0.2",
        "127.255.255.254",
        "127.1",
        "0x7f000001",
        "::1",
        "[::1]",
        "LOCALHOST.",
        "profile.localhost",
        "oauth.invalid:443",
        "?code=oauth-secret",
        "profiles/codex",
        "https%3A%2F%2Foauth.invalid%2Faccess-token",
        _DEEPLY_PERCENT_ENCODED_LOCATOR,
    ],
)
def test_account_list_redacts_locator_like_display_names(stack, display_name):
    account = _account(
        identity=ProviderIdentity(
            display_name,
            "person@example.com",
            "Team",
        )
    )
    stack.accounts.accounts[account.id] = account

    response = stack.client.request("GET", "/api/accounts")

    assert response.status == 200
    assert response.json()["accounts"][0]["identity"] == {
        "display_name": None,
        "email": "person@example.com",
        "plan": "Team",
    }
    assert display_name.encode() not in response.body


@pytest.mark.parametrize("route", ["create", "rename"])
def test_account_mutation_responses_use_the_same_identity_sanitizer(
    stack, monkeypatch, route
):
    unsafe_name = "~/Library/Application Support/Codex/auth.json"
    unsafe_email = "user@127.0.0.1"
    unsafe_plan = "localhost:1455/callback?code=oauth-secret"
    account = _account(
        identity=ProviderIdentity(unsafe_name, unsafe_email, unsafe_plan)
    )
    if route == "create":
        monkeypatch.setattr(
            stack.usage,
            "create_account",
            lambda provider, label: account,
        )
        response = stack.client.request(
            "POST",
            "/api/accounts",
            json_body={"provider": "codex", "label": "Personal"},
        )
    else:
        monkeypatch.setattr(
            stack.usage,
            "rename_account",
            lambda account_id, label: account,
        )
        response = stack.client.request(
            "PATCH",
            f"/api/accounts/{account.id}",
            json_body={"label": "Personal"},
        )

    assert response.status in {200, 201}
    assert response.json()["account"]["identity"] == {
        "display_name": None,
        "email": None,
        "plan": None,
    }
    for sentinel in (unsafe_name, unsafe_email, unsafe_plan):
        assert sentinel.encode() not in response.body


def test_account_dto_preserves_safe_unicode_identity_fields(stack):
    account = _account(
        label="개인 계정 — 업무",
        identity=ProviderIdentity(
            "Jean–Luc Picard",
            "person.name+codex@example.com",
            "Pro — Annual",
        ),
    )
    stack.accounts.accounts[account.id] = account

    response = stack.client.request("GET", "/api/accounts")

    assert response.status == 200
    assert response.json()["accounts"][0] == {
        "id": account.id,
        "provider": "codex",
        "label": "개인 계정 — 업무",
        "state": "logged_out",
        "identity": {
            "display_name": "Jean–Luc Picard",
            "email": "person.name+codex@example.com",
            "plan": "Pro — Annual",
        },
        "created_at": "2026-08-21T00:00:00+00:00",
        "usage": None,
    }


@pytest.mark.parametrize("route", ["create", "rename"])
@pytest.mark.parametrize(
    "unsafe_label",
    [
        "https://oauth.invalid/access-token",
        _DEEPLY_PERCENT_ENCODED_LOCATOR,
    ],
)
def test_account_mutations_reject_locator_shaped_required_labels(
    stack, route, unsafe_label
):
    if route == "create":
        response = stack.client.request(
            "POST",
            "/api/accounts",
            json_body={"provider": "codex", "label": unsafe_label},
        )
    else:
        account = _account()
        stack.accounts.accounts[account.id] = account
        response = stack.client.request(
            "PATCH",
            f"/api/accounts/{account.id}",
            json_body={"label": unsafe_label},
        )

    assert response.status == 400
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request body or route identifier is invalid.",
        }
    }
    assert stack.usage.calls == []


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "https://oauth.invalid/access-token",
        _DEEPLY_PERCENT_ENCODED_LOCATOR,
    ],
)
def test_account_list_rejects_locator_shaped_required_label_without_echo(
    stack, unsafe_label
):
    account = _account(label=unsafe_label)
    stack.accounts.accounts[account.id] = account

    response = stack.client.request("GET", "/api/accounts")

    _assert_fixed_internal_error(response, unsafe_label)


def test_account_dto_redacts_email_with_noncanonical_local_part(stack):
    unsafe_email = "person.@example.com"
    account = _account(
        identity=ProviderIdentity("Safe Name", unsafe_email, "Team")
    )
    stack.accounts.accounts[account.id] = account

    response = stack.client.request("GET", "/api/accounts")

    assert response.status == 200
    assert response.json()["accounts"][0]["identity"] == {
        "display_name": "Safe Name",
        "email": None,
        "plan": "Team",
    }
    assert unsafe_email.encode() not in response.body


@pytest.mark.parametrize(
    "created_at",
    [
        "~/Library/Application Support/Codex/auth.json",
        "localhost:1455/callback?code=oauth-secret",
    ],
)
def test_account_dto_rejects_non_timestamp_created_at_without_echo(
    stack, created_at
):
    original = _account()
    account = ManagedAccount(
        id=original.id,
        provider=original.provider,
        label=original.label,
        state=original.state,
        identity=original.identity,
        created_at=created_at,
    )
    stack.accounts.accounts[account.id] = account

    response = stack.client.request("GET", "/api/accounts")

    _assert_fixed_internal_error(response, created_at)


def test_account_rename_accepts_only_a_label(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account

    extra = stack.client.request(
        "PATCH",
        f"/api/accounts/{account.id}",
        json_body={"label": "Work", "provider": "codex"},
    )
    renamed = stack.client.request(
        "PATCH",
        f"/api/accounts/{account.id}",
        json_body={"label": "Work"},
    )

    assert extra.status == 400
    assert renamed.status == 200
    assert renamed.json()["account"]["label"] == "Work"


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "login", {"provider": "claude"}),
        ("POST", "refresh", {"provider": "claude"}),
        ("POST", "logout", {"provider": "claude"}),
        (
            "DELETE",
            "",
            {"provider": "claude", "action": "logout_and_delete"},
        ),
    ],
)
def test_valid_claude_lifecycle_requests_are_rejected_before_account_or_job_calls(
    stack,
    method,
    suffix,
    body,
):
    account_id = str(uuid.uuid4())
    path = f"/api/accounts/{account_id}" + (f"/{suffix}" if suffix else "")

    response = stack.client.request(method, path, json_body=body)

    assert response.status == 403
    assert response.json()["error"]["code"] == "provider_policy_disabled"
    assert stack.accounts.get_calls == []
    assert stack.application.jobs.list_jobs() == []


def test_claude_lifecycle_extra_keys_are_invalid_before_policy(stack):
    account_id = str(uuid.uuid4())

    response = stack.client.request(
        "POST",
        f"/api/accounts/{account_id}/login",
        json_body={"provider": "claude", "path": "/Users/me/.claude"},
    )

    assert response.status == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert stack.accounts.get_calls == []
    assert stack.application.jobs.list_jobs() == []


def test_login_runs_as_job_and_never_exposes_oauth_or_callback_data(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account

    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    _wait_for_job(stack, job_id)
    response = stack.client.request("GET", f"/api/jobs/{job_id}")

    assert accepted.status == 202
    assert response.status == 200
    assert set(response.json()["job"]) == {
        "id",
        "kind",
        "state",
        "account_id",
        "progress",
        "result",
        "error_code",
    }
    serialized = response.body.decode("utf-8")
    assert "sentinel-callback" not in serialized
    assert "secret-code" not in serialized
    assert stack.opened_urls == ["https://oauth.invalid/sentinel-callback"]


def test_retained_login_job_remains_pollable_after_account_rename(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    current = stack.accounts.accounts[account.id]
    stack.accounts.accounts[account.id] = ManagedAccount(
        id=current.id,
        provider=current.provider,
        label="Renamed later",
        state=current.state,
        identity=current.identity,
        created_at=current.created_at,
    )

    response = stack.client.request("GET", f"/api/jobs/{job_id}")

    assert response.status == 200
    assert response.json()["job"]["result"]["account"]["label"] == "Personal"


def test_retained_logout_job_ignores_later_account_state_and_identity(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/logout",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    oauth_sentinel = "https://oauth.invalid/later-account-state"
    stack.accounts.accounts[account.id] = ManagedAccount(
        id=account.id,
        provider=account.provider,
        label=account.label,
        state="error",
        identity=ProviderIdentity(oauth_sentinel, None, None),
        created_at=account.created_at,
    )

    response = stack.client.request("GET", f"/api/jobs/{job_id}")

    assert response.status == 200
    result = response.json()["job"]["result"]["account"]
    assert result["state"] == "logged_out"
    assert result["identity"]["display_name"] is None
    assert oauth_sentinel.encode() not in response.body


def test_retained_refresh_job_remains_pollable_after_account_removal(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/refresh",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    stack.accounts.accounts.pop(account.id)

    response = stack.client.request("GET", f"/api/jobs/{job_id}")

    assert response.status == 200
    usage = response.json()["job"]["result"]["usage"]
    assert usage["account_id"] == account.id
    assert usage["provider"] == "codex"


def test_job_polling_rejects_callback_progress_fields(stack, monkeypatch):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    callback = "https://oauth.invalid/private-callback"
    view = JobView(
        id=job_id,
        kind="account_login",
        state="waiting_for_user",
        account_id=account.id,
        progress={"state": "waiting_for_user", "verification_url": callback},
        result=None,
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    _assert_fixed_internal_error(response, callback, "verification_url")


def test_job_polling_rejects_unknown_kinds(stack, monkeypatch):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    view = JobView(
        id=job_id,
        kind="provider_private_probe",
        state="running",
        account_id=account.id,
        progress={},
        result=None,
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    _assert_fixed_internal_error(response, "provider_private_probe")


def test_job_polling_requires_the_returned_view_to_match_the_requested_job(
    stack, monkeypatch
):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    requested_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, requested_id).state == "succeeded"
    mismatched_id = str(uuid.uuid4())
    view = JobView(
        id=mismatched_id,
        kind="account_login",
        state="running",
        account_id=account.id,
        progress={"state": "starting"},
        result=None,
        error_code=None,
    )
    monkeypatch.setattr(stack.application.jobs, "get", lambda job_id: view)

    response = stack.client.request("GET", f"/api/jobs/{requested_id}")

    _assert_fixed_internal_error(response, mismatched_id)


@pytest.mark.parametrize("mismatch", ["account_id", "provider", "kind"])
def test_job_polling_correlates_views_with_the_original_api_submission(
    stack, monkeypatch, mismatch
):
    submitted_account = _account()
    stack.accounts.accounts[submitted_account.id] = submitted_account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{submitted_account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"

    view_account = submitted_account
    if mismatch == "account_id":
        view_account = _account(label="Another")
        stack.accounts.accounts[view_account.id] = view_account
    kind = "account_logout" if mismatch == "kind" else "account_login"
    state = "succeeded" if mismatch == "provider" else "running"
    result = None
    if mismatch == "provider":
        current = stack.accounts.accounts[submitted_account.id]
        result = {
            "account": {
                "id": current.id,
                "provider": "claude",
                "label": current.label,
                "state": current.state,
                "identity": {
                    "display_name": current.identity.display_name,
                    "email": current.identity.email,
                    "plan": current.identity.plan,
                },
                "created_at": current.created_at,
                "usage": None,
            }
        }
    view = JobView(
        id=job_id,
        kind=kind,
        state=state,
        account_id=view_account.id,
        progress=(
            {}
            if kind == "account_logout"
            else {"state": "done" if state == "succeeded" else "starting"}
        ),
        result=result,
        error_code=None,
    )
    monkeypatch.setattr(stack.application.jobs, "get", lambda requested: view)

    response = stack.client.request("GET", f"/api/jobs/{job_id}")

    _assert_fixed_internal_error(response)


def test_job_polling_rejects_nested_and_path_sync_results(stack, monkeypatch):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]
    accepted = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": digest}
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    private_path = "/Users/private/.codex/auth.json"
    view = JobView(
        id=job_id,
        kind="sync_execute",
        state="succeeded",
        account_id=digest,
        progress={},
        result={
            "direction": "backup",
            "changed": [private_path],
            "unchanged": [],
            "failed": [],
            "duration_ms": 1,
            "provider": {"oauth": "nested-private-token"},
        },
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    _assert_fixed_internal_error(
        response,
        private_path,
        "nested-private-token",
        "oauth",
    )


@pytest.mark.parametrize(
    ("direction", "changed"),
    [("apply", ["zsh"]), ("backup", ["ghostty"])],
)
def test_sync_job_result_correlates_with_its_issued_preview(
    stack, monkeypatch, direction, changed
):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]
    accepted = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": digest}
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    view = JobView(
        id=job_id,
        kind="sync_execute",
        state="succeeded",
        account_id=digest,
        progress={},
        result={
            "direction": direction,
            "changed": changed,
            "unchanged": [],
            "failed": [],
            "duration_ms": 1,
        },
        error_code=None,
    )
    monkeypatch.setattr(stack.application.jobs, "get", lambda requested: view)

    response = stack.client.request("GET", f"/api/jobs/{job_id}")

    _assert_fixed_internal_error(response)


@pytest.mark.parametrize(
    ("mismatch", "sentinel"),
    [
        ("account_id", "00000000-0000-4000-8000-000000000001"),
        ("provider", "claude"),
    ],
)
def test_account_job_results_must_match_the_managed_account(
    stack, monkeypatch, mismatch, sentinel
):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    retained = stack.accounts.accounts[account.id]
    result_id = sentinel if mismatch == "account_id" else retained.id
    provider = sentinel if mismatch == "provider" else retained.provider
    view = JobView(
        id=job_id,
        kind="account_login",
        state="succeeded",
        account_id=retained.id,
        progress={"state": "done"},
        result={
            "account": {
                "id": result_id,
                "provider": provider,
                "label": retained.label,
                "state": retained.state,
                "identity": {
                    "display_name": retained.identity.display_name,
                    "email": retained.identity.email,
                    "plan": retained.identity.plan,
                },
                "created_at": retained.created_at,
                "usage": None,
            }
        },
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    _assert_fixed_internal_error(response, sentinel)


@pytest.mark.parametrize(
    "display_name",
    [
        "~/Library/Application Support/Codex/auth.json",
        "localhost:1455/callback?code=oauth-secret",
        "https://oauth.invalid/access-token",
        "127.0.0.2",
        "127.255.255.254",
        "127.1",
        "0x7f000001",
        "::1",
        "[::1]",
        "LOCALHOST.",
        "profile.localhost",
        "oauth.invalid:443",
        "?code=oauth-secret",
        "profiles/codex",
        "https%3A%2F%2Foauth.invalid%2Faccess-token",
        _DEEPLY_PERCENT_ENCODED_LOCATOR,
    ],
)
def test_account_job_redacts_unsafe_optional_identity_fields(
    stack, monkeypatch, display_name
):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    retained = stack.accounts.accounts[account.id]
    view = JobView(
        id=job_id,
        kind="account_login",
        state="succeeded",
        account_id=retained.id,
        progress={"state": "done"},
        result={
            "account": {
                "id": retained.id,
                "provider": retained.provider,
                "label": retained.label,
                "state": retained.state,
                "identity": {
                    "display_name": display_name,
                    "email": "person@example.com",
                    "plan": "Team",
                },
                "created_at": retained.created_at,
                "usage": None,
            }
        },
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    assert response.status == 200
    assert response.json()["job"]["result"]["account"]["identity"] == {
        "display_name": None,
        "email": "person@example.com",
        "plan": "Team",
    }
    assert display_name.encode() not in response.body


def test_account_job_preserves_safe_unicode_punctuation(stack, monkeypatch):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    retained = stack.accounts.accounts[account.id]
    view = JobView(
        id=job_id,
        kind="account_login",
        state="succeeded",
        account_id=retained.id,
        progress={"state": "done"},
        result={
            "account": {
                "id": retained.id,
                "provider": retained.provider,
                "label": "Work — Personal",
                "state": retained.state,
                "identity": {
                    "display_name": "Jean–Luc Picard",
                    "email": "person@example.com",
                    "plan": "Pro — Annual",
                },
                "created_at": retained.created_at,
                "usage": None,
            }
        },
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    assert response.status == 200
    retained_account = response.json()["job"]["result"]["account"]
    assert retained_account["label"] == "Work — Personal"
    assert retained_account["identity"] == {
        "display_name": "Jean–Luc Picard",
        "email": "person@example.com",
        "plan": "Pro — Annual",
    }


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "https://oauth.invalid/access-token",
        _DEEPLY_PERCENT_ENCODED_LOCATOR,
    ],
)
def test_account_job_rejects_locator_shaped_required_label_without_echo(
    stack, monkeypatch, unsafe_label
):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/login",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    retained = stack.accounts.accounts[account.id]
    view = JobView(
        id=job_id,
        kind="account_login",
        state="succeeded",
        account_id=retained.id,
        progress={"state": "done"},
        result={
            "account": {
                "id": retained.id,
                "provider": retained.provider,
                "label": unsafe_label,
                "state": retained.state,
                "identity": {
                    "display_name": "Safe Name",
                    "email": "person@example.com",
                    "plan": "Team",
                },
                "created_at": retained.created_at,
                "usage": None,
            }
        },
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    _assert_fixed_internal_error(response, unsafe_label)


@pytest.mark.parametrize(
    ("field", "sentinel"),
    [
        ("account_id", "00000000-0000-4000-8000-000000000002"),
        ("provider", "claude"),
    ],
)
def test_refresh_job_usage_must_match_the_managed_account(
    stack, monkeypatch, field, sentinel
):
    account = _account()
    stack.accounts.accounts[account.id] = account
    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/refresh",
        json_body={"provider": "codex"},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    usage_account_id = sentinel if field == "account_id" else account.id
    usage_provider = sentinel if field == "provider" else account.provider
    view = JobView(
        id=job_id,
        kind="account_refresh",
        state="succeeded",
        account_id=account.id,
        progress={},
        result={
            "usage": {
                "account_id": usage_account_id,
                "provider": usage_provider,
                "windows": [],
                "observed_at": "2026-08-21T00:00:00Z",
                "source": "codex_app_server",
                "provider_version": "1.0.0",
            },
            "stale": False,
            "error_code": None,
        },
        error_code=None,
    )

    response = _poll_injected_job(stack, monkeypatch, view)

    _assert_fixed_internal_error(response, sentinel)


@pytest.mark.parametrize("action", ["refresh", "logout"])
def test_refresh_and_logout_return_202_job_ids(stack, action):
    account = _account()
    stack.accounts.accounts[account.id] = account

    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/{action}",
        json_body={"provider": "codex"},
    )

    assert accepted.status == 202
    job_id = accepted.json()["job_id"]
    assert str(uuid.UUID(job_id)) == job_id
    assert _wait_for_job(stack, job_id).state == "succeeded"
    polled = stack.client.request("GET", f"/api/jobs/{job_id}")
    assert polled.status == 200
    assert polled.json()["job"]["kind"] == f"account_{action}"


def test_refresh_job_redacts_unknown_provider_error_codes(stack):
    account = _account()
    stack.accounts.accounts[account.id] = account
    stack.usage.refresh_error_code = "raw_provider_token_sentinel"

    accepted = stack.client.request(
        "POST",
        f"/api/accounts/{account.id}/refresh",
        json_body={"provider": "codex"},
    )
    view = _wait_for_job(stack, accepted.json()["job_id"])

    assert view.state == "succeeded"
    assert view.result["error_code"] == "provider_unavailable"
    assert "raw_provider_token_sentinel" not in json.dumps(view.result)


@pytest.mark.parametrize(
    ("action", "force_local"),
    [
        ("logout_and_delete", False),
        ("remove_local_profile_anyway", True),
    ],
)
def test_delete_job_passes_job_context_without_duplicate_cancel_event(
    stack,
    action,
    force_local,
):
    account = _account()
    stack.accounts.accounts[account.id] = account

    accepted = stack.client.request(
        "DELETE",
        f"/api/accounts/{account.id}",
        json_body={"provider": "codex", "action": action},
    )
    job_id = accepted.json()["job_id"]
    assert _wait_for_job(stack, job_id).state == "succeeded"
    polled = stack.client.request("GET", f"/api/jobs/{job_id}")

    assert accepted.status == 202
    assert polled.status == 200
    assert polled.json()["job"]["result"] == {"deleted": True}
    assert stack.usage.delete_kwargs is not None
    assert stack.usage.delete_kwargs["force_local"] is force_local
    assert isinstance(stack.usage.delete_kwargs["job_context"], JobContext)
    assert "cancel_event" not in stack.usage.delete_kwargs


@pytest.mark.parametrize(
    "action",
    ["delete", "force", "Logout_And_Delete", "", 1, None],
)
def test_delete_action_is_a_canonical_enum(stack, action):
    account = _account()
    stack.accounts.accounts[account.id] = account

    response = stack.client.request(
        "DELETE",
        f"/api/accounts/{account.id}",
        json_body={"provider": "codex", "action": action},
    )

    assert response.status == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert stack.application.jobs.list_jobs() == []


def test_account_and_job_path_ids_must_be_canonical_uuids(stack):
    account = stack.client.request(
        "POST",
        "/api/accounts/AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA/login",
        json_body={"provider": "codex"},
    )
    job = stack.client.request("GET", "/api/jobs/not-a-uuid")

    assert account.status == 400
    assert job.status == 400
    assert account.json()["error"]["code"] == "invalid_request"
    assert job.json()["error"]["code"] == "invalid_request"


def test_sync_status_update_and_preview_use_exact_safe_domain_payloads(stack):
    status = stack.client.request("GET", "/api/sync/status")
    updated = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["ghostty", "zsh"]},
    )
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )

    assert status.status == 200
    sync_status = status.json()["sync"]
    assert set(sync_status) == {"sync_dir", "apps"}
    assert set(sync_status["sync_dir"]) == {"scope", "id"}
    assert sync_status["sync_dir"]["scope"] == "sync-root"
    assert sync_status["sync_dir"]["id"].startswith("sha256:")
    assert sync_status["apps"] == []
    assert updated.status == 200
    assert updated.json() == {"apps": ["ghostty", "zsh"]}
    assert preview.status == 200
    assert preview.json()["preview"]["digest"] == "a" * 64


def test_sync_apps_reconciles_old_disk_state_when_config_replace_fails(
    stack, monkeypatch
):
    save_config(stack.sync.config)
    old_digest = _issue_sync_digest(stack)
    sentinel = "CONFIG_REPLACE_FAILURE_SENTINEL"

    def fail_before_replace(*args, **kwargs):
        raise OSError(sentinel)

    monkeypatch.setattr(config_module.os, "replace", fail_before_replace)

    response = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["ghostty"]},
    )
    stale = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert _disk_config_apps(stack.sync.config.dir) == ["zsh"]
    _assert_live_sync(stack, sync_dir=stack.sync.config.dir, apps=["zsh"])


def test_sync_apps_reconciles_new_disk_state_after_ambiguous_config_fsync(
    stack, monkeypatch
):
    save_config(stack.sync.config)
    old_digest = _issue_sync_digest(stack)
    sentinel = "CONFIG_DIRECTORY_FSYNC_SENTINEL"
    real_fsync = config_module.os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(config_module.os.fstat(descriptor).st_mode):
            raise OSError(sentinel)
        real_fsync(descriptor)

    monkeypatch.setattr(config_module.os, "fsync", fail_directory_fsync)

    response = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["ghostty"]},
    )
    stale = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert _disk_config_apps(stack.sync.config.dir) == ["ghostty"]
    _assert_live_sync(stack, sync_dir=stack.sync.config.dir, apps=["ghostty"])


def test_sync_apps_builds_candidate_before_persisting(stack):
    save_config(stack.sync.config)
    old_digest = _issue_sync_digest(stack)
    sentinel = "SYNC_CANDIDATE_FACTORY_SENTINEL"
    stack.sync.factory_error = RuntimeError(sentinel)

    response = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["ghostty"]},
    )
    stale = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert _disk_config_apps(stack.sync.config.dir) == ["zsh"]
    _assert_live_sync(stack, sync_dir=stack.sync.config.dir, apps=["zsh"])


def test_sync_apps_validates_candidate_buildability_before_persisting(stack):
    stack.sync.config.app_options = {
        "bettertouchtool": {"presets": ["../escape"]}
    }
    save_config(stack.sync.config)
    old_digest = _issue_sync_digest(stack)
    sentinel = "../escape"

    response = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["bettertouchtool"]},
    )
    stale = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert _disk_config_apps(stack.sync.config.dir) == ["zsh"]
    _assert_live_sync(stack, sync_dir=stack.sync.config.dir, apps=["zsh"])


def test_config_reload_failure_disables_sync_before_old_preview_can_publish(
    stack, monkeypatch
):
    save_config(stack.sync.config)
    old_digest = _issue_sync_digest(stack)
    release_preview = threading.Event()
    stack.sync.preview_entered.clear()
    stack.sync.preview_release = release_preview
    pending_preview = _start_request(
        lambda: stack.client.request(
            "POST",
            "/api/sync/preview",
            json_body={"direction": "backup", "apps": ["zsh"]},
        )
    )
    assert stack.sync.preview_entered.wait(timeout=1.0)
    sentinel = "CONFIG_RELOAD_FAILURE_SENTINEL"

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    def fail_reload(sync_dir: Path) -> Config:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    monkeypatch.setattr(api_module, "load_config_from", fail_reload)

    response = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["ghostty"]},
    )
    release_preview.set()
    preview = pending_preview.finish()
    stale = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )
    status = stack.client.request("GET", "/api/sync/status")

    _assert_fixed_internal_error(response, sentinel)
    assert preview.status == 409
    assert stale.status == 409
    assert status.status == 409
    assert status.json()["error"]["code"] == "sync_not_configured"


def test_config_rebuild_failure_publishes_safe_unavailable(stack, monkeypatch):
    save_config(stack.sync.config)
    old_digest = _issue_sync_digest(stack)
    sentinel = "CONFIG_REBUILD_FAILURE_SENTINEL"
    original_with_config = stack.sync.with_config
    authoritative = Config(
        dir=stack.sync.config.dir,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["../escape"]}},
    )

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    def build_candidate(config: Config):
        if config.apps == ["bettertouchtool"]:
            raise ValueError(sentinel)
        return original_with_config(config)

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    monkeypatch.setattr(
        api_module,
        "load_config_from",
        lambda sync_dir: authoritative,
    )
    monkeypatch.setattr(stack.sync, "with_config", build_candidate)

    response = stack.client.request(
        "PATCH",
        "/api/sync/apps",
        json_body={"apps": ["ghostty"]},
    )
    stale = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )
    status = stack.client.request("GET", "/api/sync/status")

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert status.status == 409
    assert status.json()["error"]["code"] == "sync_not_configured"


def test_sync_status_omits_real_symlink_error_details_and_absolute_paths(
    stack, tmp_path
):
    sentinel = "SYNC_STATUS_PRIVATE_SENTINEL"
    outside = tmp_path / sentinel
    outside.mkdir()
    stored_parent = stack.sync.config.dir / "zsh"
    stored_parent.symlink_to(outside, target_is_directory=True)

    class UnsafeStoredPathApp(App):
        name = "zsh"

        def tracked_files(self, target_dir: Path) -> list[FilePair]:
            return [
                FilePair(
                    local=tmp_path / "local-zshrc",
                    stored=target_dir / "zsh" / ".zshrc",
                    label=".zshrc",
                )
            ]

    real_service = SyncService(
        Config(dir=stack.sync.config.dir, apps=["zsh"]),
        app_factory=lambda name, config: UnsafeStoredPathApp(),
    )
    stack.sync.status_result = real_service.status()

    response = stack.client.request("GET", "/api/sync/status")

    assert response.status == 200
    assert response.json()["sync"]["apps"] == [
        {"name": "zsh", "state": "unknown", "direction": None}
    ]
    assert sentinel.encode() not in response.body
    assert str(tmp_path).encode() not in response.body


@pytest.mark.parametrize("direction", ["from", "to", "Backup", "", 1, None])
def test_sync_preview_direction_is_a_canonical_enum(stack, direction):
    response = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": direction, "apps": ["zsh"]},
    )

    assert response.status == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert ("preview", direction, ("zsh",)) not in stack.sync.calls


def test_sync_execute_rejects_any_digest_not_issued_by_this_service(stack):
    response = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": "invented"},
    )

    assert response.status == 409
    assert response.json()["error"] == {
        "code": "stale_sync_plan",
        "message": "Create a new sync preview before executing.",
    }
    assert all(call[0] != "execute" for call in stack.sync.calls)


def test_sync_execute_enqueues_only_the_service_issued_digest(stack):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]

    accepted = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": digest},
    )
    job_id = accepted.json()["job_id"]
    view = _wait_for_job(stack, job_id)
    polled = stack.client.request("GET", f"/api/jobs/{job_id}")

    assert accepted.status == 202
    assert polled.status == 200
    assert polled.json()["job"]["account_id"] is None
    assert stack.sync.calls[-1] == ("execute", digest)
    assert view.result == {
        "direction": "backup",
        "changed": ["zsh"],
        "unchanged": [],
        "failed": [],
        "duration_ms": 7,
    }


def test_queued_sync_execute_keeps_the_service_that_issued_its_digest(
    stack, tmp_path
):
    account = _account()
    stack.accounts.accounts[account.id] = account
    login_gate = threading.Event()
    stack.usage.login_gate = login_gate

    for _ in range(4):
        response = stack.client.request(
            "POST",
            f"/api/accounts/{account.id}/login",
            json_body={"provider": "codex"},
        )
        assert response.status == 202
    with stack.usage.login_condition:
        assert stack.usage.login_condition.wait_for(
            lambda: stack.usage.login_started == 4,
            timeout=1.0,
        )

    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]
    accepted = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": digest},
    )

    replacement_dir = tmp_path / "replacement"
    replacement_dir.mkdir()
    stack.picker.selected = replacement_dir
    selected = stack.client.request(
        "POST",
        "/api/settings/sync-folder/select",
    )
    replacement = stack.initialized_services[-1]

    login_gate.set()
    view = _wait_for_job(stack, accepted.json()["job_id"])

    assert selected.status == 200
    assert view.state == "succeeded"
    assert ("execute", digest) in stack.sync.calls
    assert all(call[0] != "execute" for call in replacement.calls)


def test_sync_execute_maps_a_changed_service_preview_to_409_before_job_submission(stack):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]
    stack.sync.next_digest = "b" * 64

    response = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": digest},
    )

    assert response.status == 409
    assert response.json()["error"]["code"] == "stale_sync_plan"
    assert stack.application.jobs.list_jobs() == []
    assert all(call[0] != "execute" for call in stack.sync.calls)


def test_sync_execute_maps_revalidation_errors_to_stale_without_job_submission(stack):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]
    stack.sync.preview_error = OSError("private path vanished")

    response = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": digest},
    )

    assert response.status == 409
    assert response.json()["error"]["code"] == "stale_sync_plan"
    assert stack.application.jobs.list_jobs() == []
    assert b"private path" not in response.body


def test_sync_execute_digest_is_single_use(stack):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]

    first = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": digest}
    )
    second = stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": digest}
    )

    assert first.status == 202
    assert second.status == 409


def test_preview_cannot_publish_a_digest_after_folder_transition(stack, tmp_path):
    release_preview = threading.Event()
    stack.sync.preview_release = release_preview
    pending = _start_request(
        lambda: stack.client.request(
            "POST",
            "/api/sync/preview",
            json_body={"direction": "backup", "apps": ["zsh"]},
        )
    )
    assert stack.sync.preview_entered.wait(timeout=1.0)

    replacement = tmp_path / "replacement-preview"
    replacement.mkdir()
    stack.picker.selected = replacement
    selected = stack.client.request("POST", "/api/settings/sync-folder/select")
    release_preview.set()
    response = pending.finish()

    assert selected.status == 200
    assert response.status == 409
    assert response.json()["error"]["code"] == "stale_sync_plan"
    rejected = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": "a" * 64},
    )
    assert rejected.status == 409


def test_execute_failure_cannot_restore_digest_after_folder_transition(
    stack, tmp_path, monkeypatch
):
    preview = stack.client.request(
        "POST",
        "/api/sync/preview",
        json_body={"direction": "backup", "apps": ["zsh"]},
    )
    digest = preview.json()["preview"]["digest"]
    submit_entered = threading.Event()
    release_submit = threading.Event()

    def fail_submit(kind: str, *, account_id: str | None = None):
        submit_entered.set()
        assert release_submit.wait(timeout=2.0)
        raise RegistryClosed("closing sentinel")

    monkeypatch.setattr(stack.application.jobs, "submit", fail_submit)
    pending = _start_request(
        lambda: stack.client.request(
            "POST",
            "/api/sync/execute",
            json_body={"digest": digest},
        )
    )
    assert submit_entered.wait(timeout=1.0)

    replacement = tmp_path / "replacement-submit"
    replacement.mkdir()
    stack.picker.selected = replacement
    selected = stack.client.request("POST", "/api/settings/sync-folder/select")
    release_submit.set()
    failed = pending.finish()
    retried = stack.client.request(
        "POST",
        "/api/sync/execute",
        json_body={"digest": digest},
    )

    assert selected.status == 200
    assert failed.status == 503
    assert retried.status == 409


def test_apps_update_cannot_commit_after_folder_transition(stack, tmp_path):
    release_update = threading.Event()
    stack.sync.update_release = release_update
    pending = _start_request(
        lambda: stack.client.request(
            "PATCH",
            "/api/sync/apps",
            json_body={"apps": ["ghostty"]},
        )
    )
    assert stack.sync.update_entered.wait(timeout=1.0)

    replacement = tmp_path / "replacement-apps"
    replacement.mkdir()
    stack.picker.selected = replacement
    selected = stack.client.request("POST", "/api/settings/sync-folder/select")
    release_update.set()
    response = pending.finish()

    assert selected.status == 200
    assert response.status == 409
    assert stack.state.state == AppState(sync_dir=str(replacement))
    assert stack.initialized_services[-1].config.apps == ["zsh"]


def test_older_concurrent_selection_cannot_overwrite_newer_commit(stack, tmp_path):
    first = tmp_path / "first-selection"
    second = tmp_path / "second-selection"
    first.mkdir()
    second.mkdir()
    first_entered = threading.Event()
    release_first = threading.Event()
    stack.initializer.effects.append((first_entered, release_first))
    stack.picker.selected = first
    pending_first = _start_request(
        lambda: stack.client.request("POST", "/api/settings/sync-folder/select")
    )
    assert first_entered.wait(timeout=1.0)

    stack.picker.selected = second
    second_response = stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )
    release_first.set()
    first_response = pending_first.finish()
    status = stack.client.request("GET", "/api/sync/status")
    expected_id = "sha256:" + hashlib.sha256(
        str(second.absolute()).encode("utf-8")
    ).hexdigest()

    assert second_response.status == 200
    assert first_response.status == 409
    assert stack.state.state == AppState(sync_dir=str(second))
    assert status.json()["sync"]["sync_dir"]["id"] == expected_id


def test_folder_selection_reconciles_old_state_when_app_state_replace_fails(
    durable_stack, tmp_path, monkeypatch
):
    old_dir = durable_stack.sync.config.dir
    selected = tmp_path / "ordinary-state-failure"
    selected.mkdir()
    durable_stack.picker.selected = selected
    old_digest = _issue_sync_digest(durable_stack)
    sentinel = "APP_STATE_REPLACE_FAILURE_SENTINEL"

    def fail_before_replace(*args, **kwargs):
        raise OSError(sentinel)

    monkeypatch.setattr(private_fs_module.os, "replace", fail_before_replace)

    response = durable_stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )
    stale = durable_stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert durable_stack.state.state == AppState(sync_dir=str(old_dir))
    _assert_live_sync(durable_stack, sync_dir=old_dir, apps=["zsh"])


def test_folder_selection_reconciles_new_state_after_ambiguous_app_state_fsync(
    durable_stack, tmp_path, monkeypatch
):
    selected = tmp_path / "ambiguous-state-failure"
    selected.mkdir()
    durable_stack.picker.selected = selected
    old_digest = _issue_sync_digest(durable_stack)
    sentinel = "APP_STATE_DIRECTORY_FSYNC_SENTINEL"
    real_fsync = private_fs_module.os.fsync
    state_directory = durable_stack.paths.root.stat()

    def fail_directory_fsync(descriptor: int) -> None:
        metadata = private_fs_module.os.fstat(descriptor)
        if (
            stat.S_ISDIR(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino)
            == (state_directory.st_dev, state_directory.st_ino)
        ):
            raise OSError(sentinel)
        real_fsync(descriptor)

    monkeypatch.setattr(private_fs_module.os, "fsync", fail_directory_fsync)

    response = durable_stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )
    stale = durable_stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert durable_stack.state.state == AppState(sync_dir=str(selected))
    assert _disk_config_apps(selected) == ["zsh"]
    _assert_live_sync(durable_stack, sync_dir=selected, apps=["zsh"])


def test_folder_selection_builds_candidate_before_persisting_app_state(
    durable_stack, tmp_path
):
    old_dir = durable_stack.sync.config.dir
    selected = tmp_path / "initializer-failure"
    selected.mkdir()
    durable_stack.picker.selected = selected
    old_digest = _issue_sync_digest(durable_stack)
    sentinel = "SYNC_FOLDER_INITIALIZER_SENTINEL"
    durable_stack.initializer.effects.append(RuntimeError(sentinel))

    response = durable_stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )
    stale = durable_stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert sentinel.encode() not in response.body
    assert stale.status == 409
    assert durable_stack.state.state == AppState(sync_dir=str(old_dir))
    _assert_live_sync(durable_stack, sync_dir=old_dir, apps=["zsh"])


def test_app_state_reload_failure_publishes_safe_unavailable(
    durable_stack, tmp_path, monkeypatch
):
    selected = tmp_path / "state-reload-selection"
    selected.mkdir()
    durable_stack.picker.selected = selected
    old_digest = _issue_sync_digest(durable_stack)
    sentinel = "APP_STATE_RELOAD_FAILURE_SENTINEL"

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    def fail_reload() -> AppState:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(private_fs_module.os, "replace", fail_replace)
    monkeypatch.setattr(durable_stack.state, "load", fail_reload)

    response = durable_stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )
    stale = durable_stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )
    status = durable_stack.client.request("GET", "/api/sync/status")

    _assert_fixed_internal_error(response, sentinel)
    assert stale.status == 409
    assert status.status == 409
    assert status.json()["error"]["code"] == "sync_not_configured"


def test_app_state_rebuild_failure_publishes_safe_unavailable(
    durable_stack, tmp_path, monkeypatch
):
    selected = tmp_path / "state-rebuild-selection"
    selected.mkdir()
    authoritative = tmp_path / "authoritative-rebuild"
    authoritative.mkdir()
    save_config(Config(dir=authoritative, apps=["zsh"]))
    durable_stack.picker.selected = selected
    durable_stack.initializer.effects.extend(
        [None, ValueError("APP_STATE_REBUILD_FAILURE_SENTINEL")]
    )
    old_digest = _issue_sync_digest(durable_stack)

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(private_fs_module.os, "replace", fail_replace)
    monkeypatch.setattr(
        durable_stack.state,
        "load",
        lambda: AppState(sync_dir=str(authoritative)),
    )

    response = durable_stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )
    stale = durable_stack.client.request(
        "POST", "/api/sync/execute", json_body={"digest": old_digest}
    )
    status = durable_stack.client.request("GET", "/api/sync/status")

    _assert_fixed_internal_error(
        response,
        "APP_STATE_REBUILD_FAILURE_SENTINEL",
    )
    assert stale.status == 409
    assert status.status == 409
    assert status.json()["error"]["code"] == "sync_not_configured"


def test_folder_selection_accepts_no_http_path_and_cancellation_mutates_nothing(stack):
    injected_path = stack.client.request(
        "POST",
        "/api/settings/sync-folder/select",
        json_body={"path": "/tmp/attacker-controlled"},
    )
    cancelled = stack.client.request(
        "POST",
        "/api/settings/sync-folder/select",
    )

    assert injected_path.status == 400
    assert cancelled.status == 200
    assert cancelled.json() == {"selected": False}
    assert stack.picker.calls == 1
    assert stack.initialized_services == []
    assert stack.state.state == AppState()


def test_folder_selection_initializes_config_before_persisting_backend_path(stack, tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    stack.picker.selected = selected

    response = stack.client.request(
        "POST",
        "/api/settings/sync-folder/select",
    )

    assert response.status == 200
    assert response.json() == {"selected": True}
    assert [
        service.config.dir for service in stack.initialized_services
    ] == [selected]
    assert stack.state.events[-2:] == ["folder_candidate_built", "state_saved"]
    assert stack.state.state == AppState(sync_dir=str(selected))
    assert (selected / "dotsync.toml").is_file()


@pytest.mark.parametrize("failure_kind", ["file", "directory"])
def test_folder_selection_cleans_up_failed_anchored_config_initialization(
    stack, tmp_path, monkeypatch, failure_kind
):
    selected = tmp_path / f"failed-{failure_kind}-initialization"
    selected.mkdir()
    stack.picker.selected = selected
    real_fsync = config_module.os.fsync

    def fail_selected_fsync(descriptor: int) -> None:
        is_directory = stat.S_ISDIR(config_module.os.fstat(descriptor).st_mode)
        if (failure_kind == "directory") == is_directory:
            raise OSError(f"{failure_kind} fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(config_module.os, "fsync", fail_selected_fsync)

    response = stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert not (selected / "dotsync.toml").exists()
    assert stack.initialized_services == []
    assert stack.state.state == AppState()


def test_folder_selection_cleanup_keeps_a_replaced_config_entry(
    stack, tmp_path, monkeypatch
):
    selected = tmp_path / "replaced-config-during-cleanup"
    selected.mkdir()
    config_path = selected / "dotsync.toml"
    displaced = selected / "created-config"
    replacement = "CONCURRENT_REPLACEMENT\n"
    stack.picker.selected = selected
    real_fsync = config_module.os.fsync
    replaced = False

    def replace_config_before_fsync_failure(descriptor: int) -> None:
        nonlocal replaced
        if (
            not replaced
            and stat.S_ISDIR(config_module.os.fstat(descriptor).st_mode)
            and config_path.exists()
        ):
            replaced = True
            config_path.rename(displaced)
            config_path.write_text(replacement)
            raise OSError("directory fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(
        config_module.os,
        "fsync",
        replace_config_before_fsync_failure,
    )

    response = stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert config_path.read_text() == replacement
    assert displaced.is_file()
    assert stack.initialized_services == []
    assert stack.state.state == AppState()


def test_folder_selection_cleanup_never_unlinks_a_last_moment_replacement(
    stack, tmp_path, monkeypatch
):
    selected = tmp_path / "replacement-at-cleanup"
    selected.mkdir()
    config_path = selected / "dotsync.toml"
    displaced = selected / "request-created-config"
    replacement = "LAST_MOMENT_REPLACEMENT\n"
    stack.picker.selected = selected
    real_fsync = config_module.os.fsync
    real_unlink = config_module.os.unlink
    replacement_installed = False

    def fail_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(config_module.os.fstat(descriptor).st_mode):
            raise OSError("file fsync failed")
        real_fsync(descriptor)

    def replace_entry_before_cleanup(
        name: str, *, dir_fd: int | None = None
    ) -> None:
        nonlocal replacement_installed
        if not replacement_installed:
            if config_path.exists():
                config_path.rename(displaced)
            config_path.write_text(replacement)
            replacement_installed = True
        real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(config_module.os, "fsync", fail_file_fsync)
    monkeypatch.setattr(config_module.os, "unlink", replace_entry_before_cleanup)

    response = stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert replacement_installed
    assert config_path.read_text() == replacement
    assert stack.initialized_services == []
    assert stack.state.state == AppState()


def test_folder_selection_rejects_a_symlink_in_any_ancestor(stack, tmp_path):
    real_parent = tmp_path / "real-parent"
    selected = real_parent / "selected"
    selected.mkdir(parents=True)
    alias = tmp_path / "alias-parent"
    alias.symlink_to(real_parent, target_is_directory=True)
    stack.picker.selected = alias / "selected"

    response = stack.client.request("POST", "/api/settings/sync-folder/select")

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert stack.initialized_services == []
    assert stack.state.state == AppState()
    assert not (selected / "dotsync.toml").exists()


def test_folder_selection_does_not_follow_an_existing_config_symlink(
    stack, tmp_path
):
    selected = tmp_path / "config-symlink-selection"
    selected.mkdir()
    sentinel = tmp_path / "outside-config"
    sentinel.write_text("PRIVATE_SENTINEL\n")
    (selected / "dotsync.toml").symlink_to(sentinel)
    stack.picker.selected = selected

    response = stack.client.request("POST", "/api/settings/sync-folder/select")

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert sentinel.read_text() == "PRIVATE_SENTINEL\n"
    assert stack.initialized_services == []
    assert stack.state.state == AppState()


def test_folder_selection_persists_only_the_canonical_validated_path(stack, tmp_path):
    parent = tmp_path / "canonical-parent"
    selected = parent / "selected"
    selected.mkdir(parents=True)
    (parent / "intermediate").mkdir()
    noncanonical = parent / "intermediate" / ".." / "selected"
    stack.picker.selected = noncanonical

    response = stack.client.request("POST", "/api/settings/sync-folder/select")

    assert response.status == 200
    assert [
        service.config.dir for service in stack.initialized_services
    ] == [selected]
    assert stack.state.state == AppState(sync_dir=str(selected))


def test_folder_selection_rejects_directory_identity_replacement(stack, tmp_path):
    selected = tmp_path / "replaceable-selection"
    displaced = tmp_path / "displaced-selection"
    selected.mkdir()
    candidate_entered = threading.Event()
    release_candidate = threading.Event()
    stack.initializer.effects.append((candidate_entered, release_candidate))
    stack.picker.selected = selected

    pending = _start_request(
        lambda: stack.client.request(
            "POST", "/api/settings/sync-folder/select"
        )
    )
    assert candidate_entered.wait(timeout=1.0)
    selected.rename(displaced)
    selected.mkdir()
    release_candidate.set()
    response = pending.finish()

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert stack.state.state == AppState()
    assert stack.sync.config.dir != selected


def test_folder_selection_never_initializes_through_replaced_picker_path(
    stack, tmp_path
):
    selected = tmp_path / "replace-before-initialization"
    displaced = tmp_path / "validated-selection"
    outside = tmp_path / "outside-sentinel"
    selected.mkdir()
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("OUTSIDE_SENTINEL\n")
    initializer_entered = threading.Event()
    release_initializer = threading.Event()
    stack.initializer.effects.append(
        (initializer_entered, release_initializer)
    )
    stack.picker.selected = selected
    pending = _start_request(
        lambda: stack.client.request(
            "POST", "/api/settings/sync-folder/select"
        )
    )
    assert initializer_entered.wait(timeout=1.0)

    selected.rename(displaced)
    selected.symlink_to(outside, target_is_directory=True)
    release_initializer.set()
    response = pending.finish()

    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_sync_folder"
    assert sentinel.read_text() == "OUTSIDE_SENTINEL\n"
    assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]
    assert not (outside / "dotsync.toml").exists()
    assert stack.state.state == AppState()


def test_folder_selection_does_not_expose_picker_path_to_candidate_factory(
    stack, tmp_path
):
    selected = tmp_path / "candidate-input-selection"
    displaced = tmp_path / "candidate-input-displaced"
    outside = tmp_path / "candidate-input-outside"
    selected.mkdir()
    outside.mkdir()
    stack.picker.selected = selected

    def mutate_through_supplied_path(*arguments: object) -> None:
        if len(arguments) != 1 or not isinstance(arguments[0], Path):
            return
        path = arguments[0]
        path.rename(displaced)
        path.symlink_to(outside, target_is_directory=True)
        (path / "factory-write.txt").write_text("OUTSIDE_WRITE\n")

    stack.initializer.effects.append(mutate_through_supplied_path)

    response = stack.client.request(
        "POST", "/api/settings/sync-folder/select"
    )

    assert response.status == 200
    assert response.json() == {"selected": True}
    assert selected.is_dir()
    assert not selected.is_symlink()
    assert list(outside.iterdir()) == []
    assert stack.initializer.arguments == [()]
    assert stack.state.state == AppState(sync_dir=str(selected))


def test_reveal_accepts_no_path_and_uses_only_app_paths_root(stack):
    injected = stack.client.request(
        "POST",
        "/api/settings/app-data/reveal",
        json_body={"path": "/tmp/attacker-controlled"},
    )
    revealed = stack.client.request("POST", "/api/settings/app-data/reveal")

    assert injected.status == 400
    assert revealed.status == 200
    assert revealed.json() == {"revealed": True}
    assert stack.revealed == [stack.paths.root]


def test_empty_body_routes_reject_non_json_content_types(stack):
    response = stack.client.request(
        "POST",
        "/api/heartbeat",
        headers={"Content-Type": "text/plain"},
    )

    assert response.status == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_internal_exceptions_return_fixed_errors_without_repr_or_traceback(stack):
    stack.usage.raise_on_create = RuntimeError(
        "provider raw sentinel https://oauth.invalid/callback token=secret"
    )

    response = stack.client.request(
        "POST",
        "/api/accounts",
        json_body={"provider": "codex", "label": "Personal"},
    )

    serialized = response.body.decode("utf-8")
    assert response.status == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "DotSync could not complete the request.",
        }
    }
    assert "sentinel" not in serialized
    assert "oauth" not in serialized
    assert "traceback" not in serialized.lower()


def test_request_boundary_normalizes_non_exception_failures_without_disconnect(stack):
    stack.usage.raise_on_create = KeyboardInterrupt("raw-provider-sentinel")

    response = stack.client.request(
        "POST",
        "/api/accounts",
        json_body={"provider": "codex", "label": "Personal"},
    )

    assert response.status == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert b"raw-provider-sentinel" not in response.body


def test_api_json_responses_are_utf8_and_never_cached(stack):
    response = stack.client.request("GET", "/api/health")

    assert response.status == 200
    assert response.header("Content-Type") == "application/json; charset=utf-8"
    assert response.header("Cache-Control") == "no-store"
