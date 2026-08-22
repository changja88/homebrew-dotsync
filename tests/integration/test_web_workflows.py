from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dotsync.accounts import AccountStore, ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState, AppStateStore
from dotsync.config import Config, save_config
from dotsync.providers import LoginProgress, ProviderError
from dotsync.sync_service import SyncService
from dotsync.usage import UsageCache, UsageService, UsageSnapshot, UsageWindow
from dotsync.web import WebApplication, run_ui_server


_OBSERVED_AT = "2026-08-21T12:00:00Z"
_SECRET_SENTINELS = (
    "/Users/fixture/.codex/auth.json",
    "oauth-access-secret",
    "http://127.0.0.1:43199/callback?code=secret",
)


@dataclass(frozen=True)
class Response:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def json(self) -> dict[str, object]:
        return json.loads(self.body.decode("utf-8"))


class LoopbackClient:
    def __init__(self, server, token: str) -> None:
        self._server = server
        self._token = token

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        body: bytes | None = None,
        token: str | None | object = ...,
        host: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2.0)
        request_headers = dict(headers or {})
        effective_token = self._token if token is ... else token
        if effective_token is not None:
            request_headers["X-DotSync-Token"] = str(effective_token)
        if host is not None:
            request_headers["Host"] = host
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=request_headers)
        raw = connection.getresponse()
        response = Response(raw.status, tuple(raw.getheaders()), raw.read())
        connection.close()
        return response

    def raw(self, source: bytes) -> Response:
        with socket.create_connection(("127.0.0.1", self.port), timeout=2.0) as stream:
            stream.sendall(source)
            raw = http.client.HTTPResponse(stream)
            raw.begin()
            return Response(raw.status, tuple(raw.getheaders()), raw.read())


class BarrierProvider:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.calls: list[tuple[str, str]] = []
        self.refresh_started: dict[str, threading.Event] = {}
        self.refresh_release: dict[str, threading.Event] = {}
        self.refresh_cancel: dict[str, threading.Event | None] = {}
        self.percentages: dict[str, float] = {}
        self.refresh_errors: dict[str, BaseException] = {}

    def login(
        self,
        account: ManagedAccount,
        report,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ProviderIdentity:
        with self._condition:
            self.calls.append(("login", account.id))
            self._condition.notify_all()
        report(LoginProgress("done"))
        return ProviderIdentity(None, f"{account.id[:8]}@example.invalid", "plus")

    def refresh_usage(
        self,
        account: ManagedAccount,
        *,
        cancel_event: threading.Event | None = None,
    ) -> UsageSnapshot:
        started = self.refresh_started.setdefault(account.id, threading.Event())
        release = self.refresh_release.setdefault(account.id, threading.Event())
        with self._condition:
            self.calls.append(("refresh", account.id))
            self.refresh_cancel[account.id] = cancel_event
            started.set()
            self._condition.notify_all()
        assert release.wait(timeout=2.0), "fixture refresh was never released"
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderError("refresh_cancelled", "Refresh was cancelled.")
        error = self.refresh_errors.get(account.id)
        if error is not None:
            raise error
        percentage = self.percentages[account.id]
        return UsageSnapshot(
            account_id=account.id,
            provider="codex",
            windows=(
                UsageWindow(
                    name="five_hour",
                    limit_id="codex",
                    label="Codex",
                    used_percent=percentage,
                    duration_minutes=300,
                    resets_at="2026-08-21T13:00:00Z",
                ),
            ),
            observed_at=_OBSERVED_AT,
            source="codex_app_server",
            provider_version="fixture-1.0.0",
        )

    def logout(
        self,
        account: ManagedAccount,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        with self._condition:
            self.calls.append(("logout", account.id))
            self._condition.notify_all()

    def prepare_refresh(self, account_id: str, percentage: float) -> None:
        self.percentages[account_id] = percentage
        self.refresh_started[account_id] = threading.Event()
        self.refresh_release[account_id] = threading.Event()

    def wait_for_call_count(self, operation: str, count: int) -> None:
        with self._condition:
            assert self._condition.wait_for(
                lambda: sum(call[0] == operation for call in self.calls) >= count,
                timeout=2.0,
            )


class RejectingClaudeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _unexpected(self, operation: str):
        self.calls.append(operation)
        raise AssertionError("public Claude policy reached its provider")

    def login(self, *args, **kwargs):
        return self._unexpected("login")

    def refresh_usage(self, *args, **kwargs):
        return self._unexpected("refresh")

    def logout(self, *args, **kwargs):
        return self._unexpected("logout")


class SelectedFolder:
    def __init__(self) -> None:
        self.value: Path | None = None

    def __call__(self) -> Path | None:
        return self.value


class BlockingSyncService(SyncService):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.block_preview = False
        self.preview_entered = threading.Event()
        self.preview_release = threading.Event()

    def preview(self, direction, apps):
        if self.block_preview:
            self.preview_entered.set()
            assert self.preview_release.wait(timeout=2.0)
        return super().preview(direction, apps)


@dataclass
class WebStack:
    paths: AppPaths
    accounts: AccountStore
    cache: UsageCache
    usage: UsageService
    codex: BarrierProvider
    claude: RejectingClaudeProvider
    picker: SelectedFolder
    sync: BlockingSyncService
    application: WebApplication

    def create_account(self, label: str, percentage: float) -> ManagedAccount:
        account = self.usage.create_account("codex", label)
        self.codex.prepare_refresh(account.id, percentage)
        return account


@pytest.fixture
def web_stack(fake_home: Path) -> WebStack:
    paths = AppPaths.for_home(fake_home)
    accounts = AccountStore(paths)
    cache = UsageCache(paths)
    codex = BarrierProvider()
    claude = RejectingClaudeProvider()
    usage = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={"codex": codex, "claude": claude},
    )
    sync_dir = fake_home / "sync-one"
    sync_dir.mkdir()
    config = Config(dir=sync_dir, apps=["ghostty"])
    save_config(config)
    local = (
        fake_home
        / "Library"
        / "Application Support"
        / "com.mitchellh.ghostty"
        / "config.ghostty"
    )
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("local-v1\n", encoding="utf-8")
    stored = sync_dir / "ghostty" / "config.ghostty"
    stored.parent.mkdir()
    stored.write_text("stored-v1\n", encoding="utf-8")
    sync = BlockingSyncService(config)
    state_store = AppStateStore(paths)
    state_store.save(AppState(sync_dir=str(sync_dir)))
    picker = SelectedFolder()
    application = WebApplication(
        paths=paths,
        state_store=state_store,
        account_store=accounts,
        usage_service=usage,
        sync_service=sync,
        folder_picker=picker,
        sync_folder_initializer=lambda: SyncService(Config(dir=Path("/dev/null"), apps=[])),
        reveal_app_data=lambda path: None,
        open_provider_url=lambda url: None,
        utc_clock=lambda: datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc),
        idle_shutdown_enabled=False,
    )
    return WebStack(paths, accounts, cache, usage, codex, claude, picker, sync, application)


def _accepted_job(client: LoopbackClient, response: Response) -> str:
    assert response.status == 202
    job_id = response.json()["job_id"]
    assert isinstance(job_id, str)
    return job_id


def _job_response(stack: WebStack, client: LoopbackClient, job_id: str) -> Response:
    stack.application.jobs.wait(job_id, timeout=2.0)
    response = client.request("GET", f"/api/jobs/{job_id}")
    assert response.status == 200
    return response


def _start_request(operation):
    result: dict[str, object] = {}
    finished = threading.Event()

    def run() -> None:
        try:
            result["response"] = operation()
        except BaseException as error:
            result["error"] = error
        finally:
            finished.set()

    thread = threading.Thread(target=run)
    thread.start()

    def finish() -> Response:
        assert finished.wait(timeout=2.0)
        thread.join(timeout=0)
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        return result["response"]  # type: ignore[return-value]

    return finish


def test_browser_bootstrap_erases_token_and_surfaces_do_not_start_provider_work(
    web_stack: WebStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        launch_urls = [
            server.launch_url_for(surface="popover", destination="overview"),
            server.launch_url_for(surface="manager", destination="accounts"),
        ]
        for launch_url in launch_urls:
            page = client.request("GET", launch_url.removeprefix(server.origin))
            assert page.status == 200
        assert client.request("GET", "/api/bootstrap").status == 200
        assert client.request("GET", "/api/accounts").status == 200
        assert client.request("GET", "/api/menu-summary").status == 200

        node_source = r'''
import { readLaunchContext } from "./lib/dotsync/web/static/api-client.mjs";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const fixture = JSON.parse(input);
const url = new URL(fixture.url);
let visible = null;
const context = readLaunchContext(
  { search: url.search, pathname: url.pathname },
  { replaceState(_state, _title, value) { visible = value; } },
);
process.stdout.write(JSON.stringify({
  visible,
  surface: context.surface,
  destination: context.destination,
  capabilityRetainedOnlyInMemory: context.token === fixture.capability,
}));
'''
        process = subprocess.Popen(
            ["node", "--input-type=module", "--eval", node_source],
            cwd=Path(__file__).parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate(
            json.dumps(
                {"url": launch_urls[0], "capability": web_stack.application.token}
            ).encode("utf-8"),
            timeout=3.0,
        )

    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert json.loads(stdout) == {
        "visible": "/",
        "surface": "popover",
        "destination": "overview",
        "capabilityRetainedOnlyInMemory": True,
    }
    assert web_stack.codex.calls == []
    assert web_stack.claude.calls == []
    assert web_stack.application.jobs.list_jobs() == []
    assert web_stack.application.token not in capsys.readouterr().out
    for path in web_stack.paths.root.rglob("*"):
        if path.is_file():
            assert web_stack.application.token.encode("utf-8") not in path.read_bytes()


def test_public_claude_operations_stop_before_account_provider_or_job_work(
    web_stack: WebStack,
) -> None:
    account_id = str(uuid.uuid4())
    requests = [
        ("POST", "/api/accounts", {"provider": "claude", "label": "Claude"}),
        ("POST", f"/api/accounts/{account_id}/login", {"provider": "claude"}),
        ("POST", f"/api/accounts/{account_id}/refresh", {"provider": "claude"}),
        ("POST", f"/api/accounts/{account_id}/logout", {"provider": "claude"}),
        (
            "DELETE",
            f"/api/accounts/{account_id}",
            {"provider": "claude", "action": "logout_and_delete"},
        ),
    ]

    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        responses = [
            client.request(method, path, json_body=body)
            for method, path, body in requests
        ]

    assert [response.status for response in responses] == [403] * len(requests)
    assert {
        response.json()["error"]["code"] for response in responses
    } == {"provider_policy_disabled"}
    assert web_stack.claude.calls == []
    assert web_stack.codex.calls == []
    assert web_stack.accounts.list() == []
    assert web_stack.application.jobs.list_jobs() == []


def test_simultaneous_refreshes_remain_correlated_over_real_loopback(
    web_stack: WebStack,
) -> None:
    personal = web_stack.create_account("Personal", 31.0)
    work = web_stack.create_account("Work", 79.0)

    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        first = _accepted_job(
            client,
            client.request(
                "POST",
                f"/api/accounts/{personal.id}/refresh",
                json_body={"provider": "codex"},
            ),
        )
        second = _accepted_job(
            client,
            client.request(
                "POST",
                f"/api/accounts/{work.id}/refresh",
                json_body={"provider": "codex"},
            ),
        )
        assert web_stack.codex.refresh_started[personal.id].wait(timeout=2.0)
        assert web_stack.codex.refresh_started[work.id].wait(timeout=2.0)
        web_stack.codex.refresh_release[work.id].set()
        web_stack.codex.refresh_release[personal.id].set()
        responses = {
            personal.id: _job_response(web_stack, client, first),
            work.id: _job_response(web_stack, client, second),
        }

    assert responses[personal.id].json()["job"]["result"]["usage"][
        "account_id"
    ] == personal.id
    assert responses[work.id].json()["job"]["result"]["usage"][
        "account_id"
    ] == work.id
    assert responses[personal.id].json()["job"]["result"]["usage"]["windows"][0][
        "used_percent"
    ] == 31.0
    assert responses[work.id].json()["job"]["result"]["usage"]["windows"][0][
        "used_percent"
    ] == 79.0


def test_duplicate_delete_cancel_and_shutdown_reconcile_blocked_refreshes(
    web_stack: WebStack,
) -> None:
    duplicate = web_stack.create_account("Duplicate", 42.0)

    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        running_id = _accepted_job(
            client,
            client.request(
                "POST",
                f"/api/accounts/{duplicate.id}/refresh",
                json_body={"provider": "codex"},
            ),
        )
        assert web_stack.codex.refresh_started[duplicate.id].wait(timeout=2.0)
        duplicate_id = _accepted_job(
            client,
            client.request(
                "POST",
                f"/api/accounts/{duplicate.id}/refresh",
                json_body={"provider": "codex"},
            ),
        )
        delete_id = _accepted_job(
            client,
            client.request(
                "DELETE",
                f"/api/accounts/{duplicate.id}",
                json_body={"provider": "codex", "action": "logout_and_delete"},
            ),
        )
        duplicate_response = _job_response(web_stack, client, duplicate_id)
        delete_response = _job_response(web_stack, client, delete_id)
        web_stack.application.jobs.cancel(running_id)
        cancel_event = web_stack.codex.refresh_cancel[duplicate.id]
        assert cancel_event is not None and cancel_event.wait(timeout=2.0)
        web_stack.codex.refresh_release[duplicate.id].set()
        cancelled = _job_response(web_stack, client, running_id)

    assert duplicate_response.json()["job"]["state"] == "failed"
    assert duplicate_response.json()["job"]["error_code"] == "job_failed"
    assert delete_response.json()["job"]["state"] == "failed"
    assert delete_response.json()["job"]["error_code"] == "job_failed"
    assert cancelled.json()["job"]["state"] == "failed"
    assert cancelled.json()["job"]["error_code"] == "cancelled"
    assert web_stack.accounts.get(duplicate.id).id == duplicate.id
    assert web_stack.codex.calls.count(("refresh", duplicate.id)) == 1

    shutting_down = web_stack.create_account("Shutdown", 55.0)
    web_stack.codex.prepare_refresh(shutting_down.id, 55.0)
    second_application = WebApplication(
        paths=web_stack.paths,
        state_store=AppStateStore(web_stack.paths),
        account_store=web_stack.accounts,
        usage_service=web_stack.usage,
        sync_service=web_stack.sync,
        folder_picker=web_stack.picker,
        sync_folder_initializer=lambda: SyncService(Config(dir=Path("/dev/null"), apps=[])),
        reveal_app_data=lambda path: None,
        open_provider_url=lambda url: None,
        idle_shutdown_enabled=False,
    )
    with run_ui_server(second_application, poll_interval=0.01) as server:
        client = LoopbackClient(server, second_application.token)
        job_id = _accepted_job(
            client,
            client.request(
                "POST",
                f"/api/accounts/{shutting_down.id}/refresh",
                json_body={"provider": "codex"},
            ),
        )
        assert web_stack.codex.refresh_started[shutting_down.id].wait(timeout=2.0)
        finish_shutdown = _start_request(
            lambda: (
                second_application.shutdown(),
                Response(0, (), b""),
            )[1]
        )
        cancel_event = web_stack.codex.refresh_cancel[shutting_down.id]
        assert cancel_event is not None and cancel_event.wait(timeout=2.0)
        web_stack.codex.refresh_release[shutting_down.id].set()
        finish_shutdown()
        view = second_application.jobs.get(job_id)

    assert view.state == "failed"
    assert view.error_code == "cancelled"


def test_stale_apply_folder_and_app_selection_races_fail_closed(
    web_stack: WebStack,
    fake_home: Path,
) -> None:
    local = (
        fake_home
        / "Library"
        / "Application Support"
        / "com.mitchellh.ghostty"
        / "config.ghostty"
    )
    stored = web_stack.sync.config.dir / "ghostty" / "config.ghostty"

    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        preview = client.request(
            "POST",
            "/api/sync/preview",
            json_body={"direction": "apply", "apps": ["ghostty"]},
        )
        digest = preview.json()["preview"]["digest"]
        stored.write_text("changed-after-preview\n", encoding="utf-8")
        stale_file = client.request(
            "POST", "/api/sync/execute", json_body={"digest": digest}
        )

        fresh = client.request(
            "POST",
            "/api/sync/preview",
            json_body={"direction": "apply", "apps": ["ghostty"]},
        )
        folder_digest = fresh.json()["preview"]["digest"]
        replacement = fake_home / "sync-two"
        replacement.mkdir()
        web_stack.picker.value = replacement
        selected = client.request("POST", "/api/settings/sync-folder/select", json_body={})
        stale_folder = client.request(
            "POST", "/api/sync/execute", json_body={"digest": folder_digest}
        )

    assert stale_file.status == 409
    assert stale_folder.status == 409
    assert selected.status == 200
    assert local.read_text(encoding="utf-8") == "local-v1\n"

    race_stack = web_stack
    race_stack.sync.block_preview = True
    race_stack.sync.preview_entered.clear()
    race_stack.sync.preview_release.clear()
    race_stack.application = WebApplication(
        paths=race_stack.paths,
        state_store=AppStateStore(race_stack.paths),
        account_store=race_stack.accounts,
        usage_service=race_stack.usage,
        sync_service=race_stack.sync,
        folder_picker=race_stack.picker,
        sync_folder_initializer=lambda: SyncService(Config(dir=Path("/dev/null"), apps=[])),
        reveal_app_data=lambda path: None,
        open_provider_url=lambda url: None,
        idle_shutdown_enabled=False,
    )
    with run_ui_server(race_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, race_stack.application.token)
        finish_preview = _start_request(
            lambda: client.request(
                "POST",
                "/api/sync/preview",
                json_body={"direction": "apply", "apps": ["ghostty"]},
            )
        )
        assert race_stack.sync.preview_entered.wait(timeout=2.0)
        updated = client.request("PATCH", "/api/sync/apps", json_body={"apps": []})
        race_stack.sync.preview_release.set()
        raced_preview = finish_preview()

    assert updated.status == 200
    assert raced_preview.status == 409
    assert raced_preview.json()["error"]["code"] == "stale_sync_plan"


def test_host_token_method_query_body_and_label_attacks_fail_closed(
    web_stack: WebStack,
) -> None:
    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        hostile_host = client.request(
            "GET", "/api/health", host="attacker.invalid", token=web_stack.application.token
        )
        missing_token = client.request("GET", "/api/health", token=None)
        duplicate_token = client.raw(
            (
                f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{client.port}\r\n"
                f"X-DotSync-Token: {web_stack.application.token}\r\n"
                f"X-DotSync-Token: {web_stack.application.token}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        oversized = client.raw(
            (
                f"POST /api/accounts HTTP/1.1\r\nHost: 127.0.0.1:{client.port}\r\n"
                f"X-DotSync-Token: {web_stack.application.token}\r\n"
                "Content-Type: application/json\r\nContent-Length: 65537\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        unsupported = client.request("PUT", "/api/accounts")
        confused_query = client.request("GET", "/api/accounts?token=confused")
        labels = [
            client.request(
                "POST",
                "/api/accounts",
                json_body={"provider": "codex", "label": value},
            )
            for value in (
                "https://oauth.invalid/access-token",
                "/Users/fixture/.codex/auth.json",
                "../../.claude.json",
            )
        ]

    assert hostile_host.status == 421
    assert missing_token.status == 403
    assert duplicate_token.status == 403
    assert oversized.status == 413
    assert unsupported.status == 405
    assert confused_query.status == 404
    assert all(response.status == 400 for response in labels)
    assert web_stack.accounts.list() == []
    encoded = b"".join(response.body for response in labels)
    assert b"oauth.invalid" not in encoded
    assert b"/Users/fixture" not in encoded
    assert b".claude.json" not in encoded


def test_menu_summary_is_cached_identifier_free_and_provider_exceptions_are_redacted(
    web_stack: WebStack,
) -> None:
    account = web_stack.create_account("Private Work Label", 63.0)
    web_stack.codex.refresh_release[account.id].set()
    successful = web_stack.usage.refresh(account.id)
    assert successful.snapshot is not None
    calls_before_summary = list(web_stack.codex.calls)

    with run_ui_server(web_stack.application, poll_interval=0.01) as server:
        client = LoopbackClient(server, web_stack.application.token)
        summary = client.request("GET", "/api/menu-summary")
        assert summary.status == 200
        serialized = summary.body.decode("utf-8")
        assert summary.json()["usage"] == {
            "state": "fresh",
            "highest_percent": 63.0,
        }
        assert account.id not in serialized
        assert account.label not in serialized
        assert str(web_stack.sync.config.dir) not in serialized
        assert web_stack.codex.calls == calls_before_summary

        failing = web_stack.create_account("Failure", 51.0)
        web_stack.codex.refresh_errors[failing.id] = RuntimeError(" ".join(_SECRET_SENTINELS))
        web_stack.codex.refresh_release[failing.id].set()
        job_id = _accepted_job(
            client,
            client.request(
                "POST",
                f"/api/accounts/{failing.id}/refresh",
                json_body={"provider": "codex"},
            ),
        )
        failed = _job_response(web_stack, client, job_id)
        native_summary = client.request("GET", "/api/menu-summary")

    assert failed.json()["job"]["state"] == "failed"
    assert failed.json()["job"]["error_code"] == "job_failed"
    combined = failed.body + native_summary.body
    for sentinel in _SECRET_SENTINELS:
        assert sentinel.encode("utf-8") not in combined
    assert failing.id not in native_summary.body.decode("utf-8")
    assert native_summary.json()["usage"] == {
        "state": "stale",
        "highest_percent": 63.0,
    }
