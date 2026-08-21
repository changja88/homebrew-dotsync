from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState
from dotsync.jobs import Job, JobView, RegistryClosed
from dotsync.web import WebApplication, run_ui_server
from dotsync.web.server import CONTENT_SECURITY_POLICY


@dataclass
class _Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


class _StateStore:
    def __init__(self) -> None:
        self.state = AppState()

    def load(self) -> AppState:
        return self.state

    def save(self, state: AppState) -> None:
        self.state = state


class _Jobs:
    def __init__(self) -> None:
        self.views: list[JobView] = []
        self.shutdown_called = threading.Event()

    def list_jobs(self) -> list[JobView]:
        return list(self.views)

    def shutdown(self) -> None:
        self.shutdown_called.set()


class _BlockingJobs(_Jobs):
    def __init__(self) -> None:
        super().__init__()
        self.list_entered = threading.Event()
        self.list_release = threading.Event()

    def list_jobs(self) -> list[JobView]:
        self.list_entered.set()
        assert self.list_release.wait(timeout=1.0)
        return super().list_jobs()


class _SubmittingJobs(_Jobs):
    def __init__(self) -> None:
        super().__init__()
        self.submit_entered = threading.Event()
        self.submit_release = threading.Event()
        self.closed = False

    def submit(self, kind: str, *, account_id: str | None = None) -> Job:
        self.submit_entered.set()
        assert self.submit_release.wait(timeout=1.0)
        if self.closed:
            raise RegistryClosed("closed")
        job = Job(id=str(uuid.uuid4()), kind=kind, account_id=account_id)
        self.views.append(
            JobView(
                id=job.id,
                kind=job.kind,
                state="queued",
                account_id=job.account_id,
                progress={},
                result=None,
                error_code=None,
            )
        )
        return job

    def shutdown(self) -> None:
        self.closed = True
        super().shutdown()


class _OneAccountStore:
    def __init__(self, account: ManagedAccount) -> None:
        self.account = account

    def get(self, account_id: str) -> ManagedAccount:
        assert account_id == self.account.id
        return self.account


def _application(
    tmp_path: Path,
    *,
    jobs: _Jobs | None = None,
    clock: _Clock | None = None,
    static_asset_loader=None,
    account_store=None,
) -> WebApplication:
    paths = AppPaths(tmp_path / "app-data")
    return WebApplication(
        paths=paths,
        state_store=_StateStore(),
        account_store=account_store or object(),
        usage_service=object(),
        sync_service=None,
        folder_picker=lambda: None,
        sync_folder_initializer=lambda path: None,
        reveal_app_data=lambda path: None,
        open_provider_url=lambda url: None,
        job_registry=jobs or _Jobs(),
        static_asset_loader=static_asset_loader,
        monotonic=clock or time.monotonic,
    )


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


def _request(
    server,
    method: str,
    path: str,
    *,
    token: str | None,
    host: str | None = None,
    json_body: dict[str, object] | None = None,
) -> _Response:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    headers: dict[str, str] = {}
    if token is not None:
        headers["X-DotSync-Token"] = token
    if host is not None:
        headers["Host"] = host
    body = None
    if json_body is not None:
        body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = _Response(connection.getresponse())
    connection.close()
    return response


def _raw_request(server, request: bytes) -> _Response:
    with socket.create_connection(server.server_address, timeout=2.0) as connection:
        connection.sendall(request)
        response = http.client.HTTPResponse(connection)
        response.begin()
        return _Response(response)


def _assert_security_headers(response: _Response) -> None:
    assert response.header("Content-Security-Policy") == CONTENT_SECURITY_POLICY
    assert response.header("Referrer-Policy") == "no-referrer"
    assert response.header("X-Content-Type-Options") == "nosniff"
    assert response.header("Cache-Control") == "no-store"
    assert all(not key.lower().startswith("access-control-") for key, _ in response.headers)


def test_server_binds_loopback_with_ephemeral_port(tmp_path):
    application = _application(tmp_path)

    with run_ui_server(application, poll_interval=0.01) as server:
        host, port = server.server_address

        assert host == "127.0.0.1"
        assert port > 0


def test_each_application_generates_a_fresh_256_bit_capability(tmp_path):
    first = _application(tmp_path / "first")
    second = _application(tmp_path / "second")

    assert first.token != second.token
    assert len(first.token) >= 43
    assert len(second.token) >= 43


def test_api_rejects_missing_or_incorrect_capability_token(tmp_path):
    application = _application(tmp_path)

    with run_ui_server(application, poll_interval=0.01) as server:
        missing = _request(server, "GET", "/api/health", token=None)
        incorrect = _request(server, "GET", "/api/health", token="incorrect")

    assert missing.status == 403
    assert missing.json() == {
        "error": {
            "code": "forbidden",
            "message": "A valid DotSync capability token is required.",
        }
    }
    assert incorrect.status == 403
    _assert_security_headers(missing)
    _assert_security_headers(incorrect)


def test_api_rejects_duplicate_capability_headers(tmp_path):
    application = _application(tmp_path)

    with run_ui_server(application, poll_interval=0.01) as server:
        port = server.server_address[1]
        response = _raw_request(
            server,
            (
                f"GET /api/health HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"X-DotSync-Token: {application.token}\r\n"
                f"X-DotSync-Token: {application.token}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"),
        )

    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden"
    _assert_security_headers(response)


def test_api_rejects_dns_rebinding_and_duplicate_host_headers(tmp_path):
    application = _application(tmp_path)

    with run_ui_server(application, poll_interval=0.01) as server:
        rebound = _request(
            server,
            "GET",
            "/api/health",
            token=application.token,
            host="evil.test",
        )
        port = server.server_address[1]
        duplicate = _raw_request(
            server,
            (
                "GET /api/health HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Host: localhost:{port}\r\n"
                f"X-DotSync-Token: {application.token}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"),
        )

    for response in (rebound, duplicate):
        assert response.status == 421
        assert response.json()["error"]["code"] == "misdirected_request"
        _assert_security_headers(response)


def test_loopback_host_aliases_with_the_actual_port_are_accepted(tmp_path):
    application = _application(tmp_path)

    with run_ui_server(application, poll_interval=0.01) as server:
        port = server.server_address[1]
        numeric = _request(
            server,
            "GET",
            "/api/health",
            token=application.token,
            host=f"127.0.0.1:{port}",
        )
        localhost = _request(
            server,
            "GET",
            "/api/health",
            token=application.token,
            host=f"localhost:{port}",
        )

    assert numeric.status == 200
    assert localhost.status == 200


def test_security_headers_cover_success_error_and_static_responses(tmp_path):
    loaded: list[str] = []

    def load_asset(name: str) -> bytes:
        loaded.append(name)
        return b"<!doctype html><title>DotSync</title>"

    application = _application(tmp_path, static_asset_loader=load_asset)

    with run_ui_server(application, poll_interval=0.01) as server:
        success = _request(server, "GET", "/api/health", token=application.token)
        error = _request(server, "GET", "/api/missing", token=application.token)
        static = _request(server, "GET", "/", token=None)

    assert loaded == ["index.html"]
    assert static.status == 200
    assert static.header("Content-Type") == "text/html; charset=utf-8"
    for response in (success, error, static):
        _assert_security_headers(response)


def test_static_loader_failures_never_disconnect_or_emit_a_traceback(tmp_path):
    def fail_loader(name: str) -> bytes:
        raise KeyboardInterrupt("raw-static-sentinel")

    application = _application(tmp_path, static_asset_loader=fail_loader)

    with run_ui_server(application, poll_interval=0.01) as server:
        response = _request(server, "GET", "/", token=None)

    assert response.status == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert b"raw-static-sentinel" not in response.body
    _assert_security_headers(response)


def test_static_loader_receives_only_fixed_package_resource_names(tmp_path):
    loaded: list[str] = []

    def load_asset(name: str) -> bytes:
        loaded.append(name)
        return b"asset"

    application = _application(tmp_path, static_asset_loader=load_asset)

    with run_ui_server(application, poll_interval=0.01) as server:
        root = _request(server, "GET", "/?token=launch-value", token=None)
        traversal = _request(server, "GET", "/../../state.json", token=None)
        encoded = _request(server, "GET", "/%2e%2e/state.json", token=None)

    assert root.status == 200
    assert traversal.status == 404
    assert encoded.status == 404
    assert loaded == ["index.html"]


def test_launch_url_contains_token_but_server_does_not_open_a_browser(tmp_path):
    application = _application(tmp_path, static_asset_loader=lambda name: b"ui")

    with run_ui_server(application, poll_interval=0.01) as server:
        assert server.launch_url == (
            f"http://127.0.0.1:{server.server_address[1]}/?token={application.token}"
        )


def test_heartbeat_resets_idle_deadline(tmp_path):
    clock = _Clock()
    application = _application(tmp_path, clock=clock)

    with run_ui_server(application, poll_interval=0.01) as server:
        clock.value = 1_700.0
        response = _request(
            server,
            "POST",
            "/api/heartbeat",
            token=application.token,
        )
        assert response.status == 200
        clock.value = 3_499.0
        assert application.should_idle_shutdown() is False
        clock.value = 3_500.0
        assert application.should_idle_shutdown() is True


def test_active_jobs_prevent_idle_shutdown_until_they_are_terminal(tmp_path):
    clock = _Clock()
    jobs = _Jobs()
    jobs.views = [
        JobView(
            id="5acbe6bb-6713-4771-82fe-6745c78d6d21",
            kind="account_refresh",
            state="running",
            account_id="c991914c-c20b-4b5a-8923-d0bf0e906b61",
            progress={},
            result=None,
            error_code=None,
        )
    ]
    application = _application(tmp_path, jobs=jobs, clock=clock)

    clock.value = 1_800.0
    assert application.should_idle_shutdown() is False

    jobs.views[0] = JobView(
        id=jobs.views[0].id,
        kind=jobs.views[0].kind,
        state="succeeded",
        account_id=jobs.views[0].account_id,
        progress={},
        result=None,
        error_code=None,
    )
    assert application.should_idle_shutdown() is True


def test_heartbeat_during_job_scan_cancels_the_stale_idle_decision(tmp_path):
    clock = _Clock()
    jobs = _BlockingJobs()
    application = _application(tmp_path, jobs=jobs, clock=clock)
    clock.value = 1_800.0
    result: dict[str, bool] = {}

    checker = threading.Thread(
        target=lambda: result.setdefault("shutdown", application.should_idle_shutdown())
    )
    checker.start()
    assert jobs.list_entered.wait(timeout=1.0)

    application.record_heartbeat()
    jobs.list_release.set()
    checker.join(timeout=1.0)

    assert result == {"shutdown": False}


def test_job_submission_and_idle_shutdown_use_one_atomic_lifecycle_boundary(tmp_path):
    clock = _Clock()
    jobs = _SubmittingJobs()
    account = ManagedAccount(
        id=str(uuid.uuid4()),
        provider="codex",
        label="Personal",
        state="logged_out",
        identity=ProviderIdentity(None, None, None),
        created_at="2026-08-21T00:00:00+00:00",
    )
    application = _application(
        tmp_path,
        jobs=jobs,
        clock=clock,
        account_store=_OneAccountStore(account),
    )

    with run_ui_server(application, poll_interval=1.0) as server:
        clock.value = 1_799.0
        response: dict[str, _Response] = {}
        submitter = threading.Thread(
            target=lambda: response.setdefault(
                "value",
                _request(
                    server,
                    "POST",
                    f"/api/accounts/{account.id}/login",
                    token=application.token,
                    json_body={"provider": "codex"},
                ),
            )
        )
        submitter.start()
        assert jobs.submit_entered.wait(timeout=1.0)
        clock.value = 1_800.0

        shutdown_result: dict[str, bool] = {}
        shutdown = application.shutdown_if_idle
        shutdown_checker = threading.Thread(
            target=lambda: shutdown_result.setdefault("value", shutdown())
        )
        shutdown_checker.start()
        jobs.submit_release.set()
        submitter.join(timeout=1.0)
        shutdown_checker.join(timeout=1.0)

        assert response["value"].status == 202
        assert shutdown_result == {"value": False}
        assert jobs.shutdown_called.is_set() is False


def test_idle_server_shutdown_closes_the_job_registry(tmp_path):
    clock = _Clock()
    jobs = _Jobs()
    application = _application(tmp_path, jobs=jobs, clock=clock)

    server = run_ui_server(application, poll_interval=0.01)
    clock.value = 1_800.0

    assert jobs.shutdown_called.wait(timeout=1.0)
    assert server.wait(timeout=1.0)
    server.close()
