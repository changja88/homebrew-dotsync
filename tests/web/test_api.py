from __future__ import annotations

import http.client
import json
import socket
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState
from dotsync.jobs import JobContext
from dotsync.providers import LoginProgress
from dotsync.sync_service import StaleSyncPlan
from dotsync.usage import UsageResult, UsageSnapshot, UsageWindow
from dotsync.web import WebApplication, run_ui_server


_UNSET = object()


class _StateStore:
    def __init__(self, events: list[str]) -> None:
        self.state = AppState()
        self.events = events

    def load(self) -> AppState:
        return self.state

    def save(self, state: AppState) -> None:
        self.events.append("state_saved")
        self.state = state


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


class _SyncStatus:
    def to_dict(self) -> dict[str, object]:
        return {
            "sync_dir": {"scope": "sync-root", "id": "sha256:safe"},
            "apps": [],
        }


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
        self.config = SimpleNamespace(dir=sync_dir, apps=["zsh"])
        self.calls: list[tuple[object, ...]] = []
        self.next_digest = "a" * 64
        self.stale = False
        self.preview_error: BaseException | None = None

    def status(self) -> _SyncStatus:
        self.calls.append(("status",))
        return _SyncStatus()

    def update_apps(self, apps: tuple[str, ...]):
        self.calls.append(("update_apps", apps))
        self.config.apps = list(apps)
        return self.config

    def preview(self, direction: str, apps: tuple[str, ...]) -> _SyncPreview:
        self.calls.append(("preview", direction, apps))
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
    initialized: list[Path]
    revealed: list[Path]
    opened_urls: list[str]
    initialized_services: list[_SyncService]


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
    initialized: list[Path] = []
    revealed: list[Path] = []
    opened_urls: list[str] = []
    initialized_services: list[_SyncService] = []

    def initialize(path: Path) -> _SyncService:
        initialized.append(path)
        events.append("folder_initialized")
        (path / "dotsync.toml").write_text("apps = []\n")
        service = _SyncService(path)
        initialized_services.append(service)
        return service

    application = WebApplication(
        paths=paths,
        state_store=state,
        account_store=accounts,
        usage_service=usage,
        sync_service=sync,
        folder_picker=picker,
        sync_folder_initializer=initialize,
        reveal_app_data=revealed.append,
        open_provider_url=opened_urls.append,
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
        initialized=initialized,
        revealed=revealed,
        opened_urls=opened_urls,
        initialized_services=initialized_services,
    )
    try:
        yield value
    finally:
        server.close()


def _account(
    *,
    provider: str = "codex",
    label: str = "Personal",
) -> ManagedAccount:
    return ManagedAccount(
        id=str(uuid.uuid4()),
        provider=provider,
        label=label,
        state="logged_out",
        identity=ProviderIdentity(None, None, None),
        created_at="2026-08-21T00:00:00+00:00",
    )


def _snapshot(account_id: str) -> UsageSnapshot:
    return UsageSnapshot(
        account_id=account_id,
        provider="codex",
        windows=(
            UsageWindow(
                name="five_hour",
                limit_id="codex",
                label=None,
                used_percent=42.0,
                duration_minutes=300,
                resets_at="2026-08-21T05:00:00Z",
            ),
        ),
        observed_at="2026-08-21T00:00:00Z",
        source="codex_app_server",
        provider_version="1.0.0",
    )


def _wait_for_job(stack: _Stack, job_id: str):
    return stack.application.jobs.wait(job_id, timeout=1.0)


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

    assert accepted.status == 202
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
    assert status.json() == {"sync": _SyncStatus().to_dict()}
    assert updated.status == 200
    assert updated.json() == {"apps": ["ghostty", "zsh"]}
    assert preview.status == 200
    assert preview.json()["preview"]["digest"] == "a" * 64


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

    assert accepted.status == 202
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
    assert stack.initialized == []
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
    assert stack.initialized == [selected]
    assert stack.state.events[-2:] == ["folder_initialized", "state_saved"]
    assert stack.state.state == AppState(sync_dir=str(selected))
    assert (selected / "dotsync.toml").is_file()


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
