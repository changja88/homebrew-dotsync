"""Secured loopback HTTP transport for DotSync's local web UI."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit

from dotsync.accounts import AccountStore
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppStateStore
from dotsync.jobs import JobRegistry
from dotsync.sync_service import SyncService
from dotsync.usage import UsageService

from .api import (
    ApiController,
    ApiRequest,
    HttpResponse,
    error_response,
    internal_error_response,
)


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)
IDLE_TIMEOUT_SECONDS = 30 * 60


@dataclass(frozen=True)
class _StaticResource:
    package_name: str
    content_type: str


# Request paths are never transformed into package or filesystem paths. Only
# these fixed resource names can reach the package-resource loader.
_STATIC_ROUTES = {
    "/": _StaticResource("index.html", "text/html; charset=utf-8"),
    "/app.js": _StaticResource("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": _StaticResource("styles.css", "text/css; charset=utf-8"),
}


class WebApplication:
    """Composition boundary shared by the HTTP server and the future UI CLI."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        state_store: AppStateStore,
        account_store: AccountStore,
        usage_service: UsageService,
        sync_service: SyncService | None,
        folder_picker: Callable[[], Path | None],
        sync_folder_initializer: Callable[[], SyncService],
        reveal_app_data: Callable[[Path], object],
        open_provider_url: Callable[[str], object],
        job_registry: JobRegistry | None = None,
        static_asset_loader: Callable[[str], bytes] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token = secrets.token_urlsafe(32)
        self._monotonic = monotonic
        self._heartbeat_lock = threading.Lock()
        self._last_heartbeat = monotonic()
        self._shutdown_lock = threading.Lock()
        self._job_lifecycle_lock = threading.RLock()
        self._shutdown_complete = False
        self._static_asset_loader = static_asset_loader or _load_packaged_asset
        self._api = ApiController(
            paths=paths,
            state_store=state_store,
            account_store=account_store,
            usage_service=usage_service,
            sync_service=sync_service,
            folder_picker=folder_picker,
            sync_folder_initializer=sync_folder_initializer,
            reveal_app_data=reveal_app_data,
            heartbeat=self.record_heartbeat,
            open_provider_url=open_provider_url,
            job_lifecycle_lock=self._job_lifecycle_lock,
            job_registry=job_registry,
        )

    @property
    def jobs(self) -> JobRegistry:
        return self._api.jobs

    def create_server(self) -> "_DotSyncHTTPServer":
        return _DotSyncHTTPServer(("127.0.0.1", 0), self)

    def record_heartbeat(self) -> bool:
        with self._job_lifecycle_lock:
            if self._shutdown_complete:
                return False
            now = self._monotonic()
            with self._heartbeat_lock:
                self._last_heartbeat = now
            return True

    def should_idle_shutdown(self) -> bool:
        now = self._monotonic()
        with self._heartbeat_lock:
            idle_for = now - self._last_heartbeat
        if idle_for < IDLE_TIMEOUT_SECONDS:
            return False
        try:
            views = self.jobs.list_jobs()
        except Exception:
            return False
        if any(view.state not in {"succeeded", "failed"} for view in views):
            return False
        now = self._monotonic()
        with self._heartbeat_lock:
            return now - self._last_heartbeat >= IDLE_TIMEOUT_SECONDS

    def handle_api(self, request: ApiRequest) -> HttpResponse:
        return self._api.dispatch(request)

    def static_response(self, path: str) -> HttpResponse:
        resource = _STATIC_ROUTES.get(path)
        if resource is None:
            return error_response(
                404,
                "not_found",
                "The requested route does not exist.",
            )
        try:
            body = self._static_asset_loader(resource.package_name)
        except (FileNotFoundError, ModuleNotFoundError):
            return error_response(
                404,
                "not_found",
                "The requested route does not exist.",
            )
        except BaseException:
            return internal_error_response()
        if type(body) is not bytes:
            return internal_error_response()
        return HttpResponse(
            status=200,
            body=body,
            content_type=resource.content_type,
        )

    def shutdown(self) -> None:
        with self._shutdown_lock:
            with self._job_lifecycle_lock:
                if self._shutdown_complete:
                    return
                self._shutdown_complete = True
                self.jobs.shutdown()

    def shutdown_if_idle(self) -> bool:
        """Close only after an atomic heartbeat and active-job recheck."""
        with self._shutdown_lock:
            with self._job_lifecycle_lock:
                if self._shutdown_complete:
                    return True
            now = self._monotonic()
            with self._heartbeat_lock:
                if now - self._last_heartbeat < IDLE_TIMEOUT_SECONDS:
                    return False
            try:
                views = self.jobs.list_jobs()
            except Exception:
                return False
            if any(view.state not in {"succeeded", "failed"} for view in views):
                return False
            with self._job_lifecycle_lock:
                if self._shutdown_complete:
                    return True
                now = self._monotonic()
                with self._heartbeat_lock:
                    if now - self._last_heartbeat < IDLE_TIMEOUT_SECONDS:
                        return False
                try:
                    views = self.jobs.list_jobs()
                except Exception:
                    return False
                if any(
                    view.state not in {"succeeded", "failed"} for view in views
                ):
                    return False
                now = self._monotonic()
                with self._heartbeat_lock:
                    if now - self._last_heartbeat < IDLE_TIMEOUT_SECONDS:
                        return False
                self._shutdown_complete = True
                self.jobs.shutdown()
                return True


class _DotSyncHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], application: WebApplication) -> None:
        self.application = application
        self._idle_lock = threading.Lock()
        self._idle_shutdown_started = False
        super().__init__(address, _DotSyncRequestHandler)

    def service_actions(self) -> None:
        if not self.application.should_idle_shutdown():
            return
        with self._idle_lock:
            if self._idle_shutdown_started:
                return
            self._idle_shutdown_started = True
        threading.Thread(
            target=self._shutdown_for_idle,
            name="dotsync-idle-shutdown",
            daemon=True,
        ).start()

    def _shutdown_for_idle(self) -> None:
        if not self.application.shutdown_if_idle():
            with self._idle_lock:
                self._idle_shutdown_started = False
            return
        self.shutdown()


class _DotSyncRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DotSync"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_TRACE(self) -> None:
        self._dispatch()

    def do_CONNECT(self) -> None:
        self._dispatch()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("do_"):
            return self._dispatch
        raise AttributeError(name)

    @property
    def _dotsync_server(self) -> _DotSyncHTTPServer:
        return cast(_DotSyncHTTPServer, self.server)

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if self.request_version not in {"HTTP/1.0", "HTTP/1.1"}:
            self.request_version = "HTTP/1.0"
            self.send_error(400)
            return False
        return True

    def _dispatch(self) -> None:
        try:
            split = urlsplit(self.path)
        except ValueError:
            self._send(
                error_response(404, "not_found", "The requested route does not exist.")
            )
            return
        if not self._valid_host():
            self._send(
                error_response(
                    421,
                    "misdirected_request",
                    "The request Host does not match this DotSync server.",
                )
            )
            return
        if split.scheme or split.netloc:
            self._send(
                error_response(404, "not_found", "The requested route does not exist.")
            )
            return
        if _is_api_path(split.path):
            if not self._valid_capability_token():
                self._send(
                    error_response(
                        403,
                        "forbidden",
                        "A valid DotSync capability token is required.",
                    )
                )
                return
            response = self._dotsync_server.application.handle_api(
                ApiRequest(
                    method=self.command,
                    path=split.path,
                    query=split.query,
                    headers=self.headers,
                    stream=self.rfile,
                )
            )
            self._send(response)
            return

        resource = _STATIC_ROUTES.get(split.path)
        if resource is not None and self.command != "GET":
            self._send(
                HttpResponse(
                    status=405,
                    body=error_response(
                        405,
                        "method_not_allowed",
                        "The request method is not allowed for this route.",
                    ).body,
                    headers=(("Allow", "GET"),),
                )
            )
            return
        self._send(self._dotsync_server.application.static_response(split.path))

    def _valid_host(self) -> bool:
        values = self.headers.get_all("Host", failobj=[])
        if len(values) != 1:
            return False
        port = self._dotsync_server.server_address[1]
        return values[0] in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _valid_capability_token(self) -> bool:
        values = self.headers.get_all("X-DotSync-Token", failobj=[])
        if len(values) != 1 or type(values[0]) is not str:
            return False
        try:
            return secrets.compare_digest(
                values[0], self._dotsync_server.application.token
            )
        except TypeError:
            return False

    def _send(self, response: HttpResponse) -> None:
        self.send_response(response.status)
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Connection", "close")
        for name, value in response.headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(response.body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.close_connection = True

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        # The stdlib suppresses status and headers for HTTP/0.9, including
        # parser failures raised before it records a modern request version.
        self.request_version = "HTTP/1.0"
        status = code if 400 <= code <= 599 else 400
        self._send(
            error_response(
                status,
                "bad_request",
                "The HTTP request could not be accepted.",
            )
        )

    def log_message(self, format: str, *args: object) -> None:
        # Request targets may contain the one-time launch token. The local
        # boundary therefore stays silent instead of using BaseHTTPRequestHandler's
        # raw request logging.
        return None


class RunningUIServer:
    """Started loopback server handle; browser launch remains a caller decision."""

    def __init__(
        self,
        server: _DotSyncHTTPServer,
        thread: threading.Thread,
        stopped: threading.Event,
    ) -> None:
        self._server = server
        self._thread = thread
        self._stopped = stopped
        self._close_lock = threading.Lock()
        self._closed = False

    @property
    def server_address(self) -> tuple[str, int]:
        return cast(tuple[str, int], self._server.server_address)

    @property
    def launch_url(self) -> str:
        port = self.server_address[1]
        token = quote(self._server.application.token, safe="")
        return f"http://127.0.0.1:{port}/?token={token}"

    def wait(self, *, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout=timeout)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._server.application.shutdown()
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=3.0)
        self._server.server_close()

    def __enter__(self) -> "RunningUIServer":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def run_ui_server(
    application: WebApplication,
    *,
    poll_interval: float = 0.1,
) -> RunningUIServer:
    """Start a loopback server in the background without opening a browser."""
    if type(poll_interval) not in {int, float} or not 0 < poll_interval <= 1:
        raise ValueError("server poll interval must be between zero and one second")
    server = application.create_server()
    stopped = threading.Event()

    def serve() -> None:
        try:
            server.serve_forever(poll_interval=float(poll_interval))
        finally:
            server.server_close()
            stopped.set()

    thread = threading.Thread(
        target=serve,
        name="dotsync-loopback-server",
        daemon=True,
    )
    thread.start()
    return RunningUIServer(server, thread, stopped)


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _load_packaged_asset(name: str) -> bytes:
    static_root = resources.files("dotsync.web").joinpath("static")
    return static_root.joinpath(name).read_bytes()
