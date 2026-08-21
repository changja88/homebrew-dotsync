"""Strict, JSON-only routing for the authenticated local web boundary."""

from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import BinaryIO, Literal, cast

from dotsync.accounts import (
    AccountConflict,
    AccountNotFound,
    AccountStore,
    AccountStoreError,
    ManagedAccount,
    ProviderName,
)
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState, AppStateStore
from dotsync.apps import APP_NAMES
from dotsync.config import ConfigError, folder_config_path
from dotsync.jobs import (
    JobContext,
    JobNotFound,
    JobRegistry,
    JobView,
    RegistryClosed,
    UnknownJobKind,
)
from dotsync.providers import LoginProgress
from dotsync.sync_service import StaleSyncPlan, SyncService
from dotsync.usage import (
    OperationConflict,
    UsageResult,
    UsageService,
    UsageSnapshot,
    UsageWindow,
)


MAX_REQUEST_BYTES = 65_536
_CONTENT_LENGTH = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_DANGEROUS_BIDI_CLASSES = frozenset(
    {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
)
_PROVIDERS = frozenset({"claude", "codex"})
_DELETE_ACTIONS = frozenset(
    {"logout_and_delete", "remove_local_profile_anyway"}
)
_DIRECTIONS = frozenset({"backup", "apply"})
_SAFE_USAGE_ERROR_CODES = frozenset(
    {
        "cli_missing",
        "not_logged_in",
        "provider_unavailable",
        "reauth_required",
        "refresh_cancelled",
        "refresh_timeout",
        "unsafe_account_path",
        "unsupported_cli_version",
        "unsupported_usage_layout",
    }
)
_CLAUDE_POLICY_MESSAGE = "Claude account management is disabled by current policy."


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    query: str
    headers: Message
    stream: BinaryIO


@dataclass(frozen=True)
class _Route:
    shape: tuple[str, ...]
    handlers: Mapping[str, str]


class _ApiProblem(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


_ROUTES = (
    _Route(("api", "bootstrap"), {"GET": "_bootstrap"}),
    _Route(("api", "health"), {"GET": "_health"}),
    _Route(("api", "accounts"), {"GET": "_list_accounts", "POST": "_create_account"}),
    _Route(
        ("api", "accounts", ":account_id"),
        {"PATCH": "_rename_account", "DELETE": "_delete_account"},
    ),
    _Route(("api", "accounts", ":account_id", "login"), {"POST": "_login"}),
    _Route(("api", "accounts", ":account_id", "refresh"), {"POST": "_refresh"}),
    _Route(("api", "accounts", ":account_id", "logout"), {"POST": "_logout"}),
    _Route(("api", "jobs", ":job_id"), {"GET": "_get_job"}),
    _Route(("api", "sync", "status"), {"GET": "_sync_status"}),
    _Route(("api", "sync", "apps"), {"PATCH": "_update_sync_apps"}),
    _Route(("api", "sync", "preview"), {"POST": "_sync_preview"}),
    _Route(("api", "sync", "execute"), {"POST": "_sync_execute"}),
    _Route(
        ("api", "settings", "sync-folder", "select"),
        {"POST": "_select_sync_folder"},
    ),
    _Route(
        ("api", "settings", "app-data", "reveal"),
        {"POST": "_reveal_app_data"},
    ),
    _Route(("api", "heartbeat"), {"POST": "_heartbeat"}),
)


def json_response(status: int, payload: dict[str, object]) -> HttpResponse:
    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return internal_error_response()
    return HttpResponse(status=status, body=body)


def error_response(status: int, code: str, message: str) -> HttpResponse:
    return json_response(
        status,
        {"error": {"code": code, "message": message}},
    )


def internal_error_response() -> HttpResponse:
    return HttpResponse(
        status=500,
        body=(
            b'{"error":{"code":"internal_error",'
            b'"message":"DotSync could not complete the request."}}'
        ),
    )


class ApiController:
    """Dispatch exact routes onto UI-neutral services and safe DTOs."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        state_store: AppStateStore,
        account_store: AccountStore,
        usage_service: UsageService,
        sync_service: SyncService | None,
        folder_picker: Callable[[], Path | None],
        sync_folder_initializer: Callable[[Path], SyncService],
        reveal_app_data: Callable[[Path], object],
        heartbeat: Callable[[], None],
        open_provider_url: Callable[[str], object],
        job_lifecycle_lock: threading.RLock,
        job_registry: JobRegistry | None = None,
    ) -> None:
        self._paths = paths
        self._state_store = state_store
        self._account_store = account_store
        self._usage = usage_service
        self._sync = sync_service
        self._folder_picker = folder_picker
        self._sync_folder_initializer = sync_folder_initializer
        self._reveal_app_data_callback = reveal_app_data
        self._heartbeat_callback = heartbeat
        self._open_provider_url = open_provider_url
        self._job_lifecycle_lock = job_lifecycle_lock
        self._sync_lock = threading.RLock()
        self._issued_sync_digests: dict[
            str, tuple[SyncService, Literal["backup", "apply"], tuple[str, ...]]
        ] = {}
        self._pending_sync_services: dict[str, SyncService] = {}
        self.jobs = (
            job_registry
            if job_registry is not None
            else JobRegistry(self._job_operations())
        )

    def dispatch(self, request: ApiRequest) -> HttpResponse:
        try:
            route, params = _match_route(request.path, request.query)
            if route is None:
                raise _ApiProblem(404, "not_found", "The requested route does not exist.")
            handler_name = route.handlers.get(request.method)
            if handler_name is None:
                allow = ", ".join(route.handlers)
                response = error_response(
                    405,
                    "method_not_allowed",
                    "The request method is not allowed for this route.",
                )
                return HttpResponse(
                    status=response.status,
                    body=response.body,
                    content_type=response.content_type,
                    headers=(("Allow", allow),),
                )
            handler = cast(
                Callable[[ApiRequest, dict[str, str]], HttpResponse],
                getattr(self, handler_name),
            )
            return handler(request, params)
        except _ApiProblem as error:
            return error_response(error.status, error.code, error.message)
        except AccountConflict:
            return error_response(
                409,
                "account_conflict",
                "An account with that label already exists for the provider.",
            )
        except (AccountNotFound, JobNotFound, KeyError):
            return error_response(404, "not_found", "The requested item does not exist.")
        except (RegistryClosed, UnknownJobKind):
            return error_response(
                503,
                "service_unavailable",
                "DotSync is shutting down; retry after reopening the app.",
            )
        except StaleSyncPlan:
            return _stale_sync_response()
        except (AccountStoreError, ConfigError):
            return error_response(
                400,
                "invalid_request",
                "The request could not be accepted.",
            )
        except BaseException:
            return internal_error_response()

    def _bootstrap(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        _require_no_body(request)
        with self._sync_lock:
            sync_configured = self._sync is not None
        return json_response(
            200,
            {
                "providers": {
                    "claude": {
                        "enabled": False,
                        "status": "policy_disabled",
                        "message": _CLAUDE_POLICY_MESSAGE,
                    },
                    "codex": {
                        "enabled": True,
                        "status": "available",
                        "message": None,
                    },
                },
                "sync_configured": sync_configured,
            },
        )

    def _health(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        _require_no_body(request)
        return json_response(200, {"status": "ok"})

    def _list_accounts(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        _require_no_body(request)
        accounts = self._usage.list_accounts()
        account_views: list[dict[str, object]] = []
        for account in accounts:
            try:
                snapshot = self._usage.cached_usage(account.id)
            except (AccountNotFound, OperationConflict):
                snapshot = None
            account_views.append(_account_to_dict(account, usage=snapshot))
        return json_response(
            200,
            {"accounts": account_views},
        )

    def _create_account(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"provider", "label"})
        provider = _provider(body["provider"])
        label = _label(body["label"])
        _enforce_provider_policy(provider)
        account = self._usage.create_account(provider, label)
        return json_response(201, {"account": _account_to_dict(account)})

    def _rename_account(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"label"})
        label = _label(body["label"])
        account_id = _canonical_uuid(params["account_id"])
        account = self._usage.rename_account(account_id, label)
        return json_response(200, {"account": _account_to_dict(account)})

    def _login(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        account_id, provider = self._account_action_request(request, params)
        self._require_account_provider(account_id, provider)
        return self._accepted_job("account_login", account_id=account_id)

    def _refresh(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        account_id, provider = self._account_action_request(request, params)
        self._require_account_provider(account_id, provider)
        return self._accepted_job("account_refresh", account_id=account_id)

    def _logout(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        account_id, provider = self._account_action_request(request, params)
        self._require_account_provider(account_id, provider)
        return self._accepted_job("account_logout", account_id=account_id)

    def _delete_account(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"provider", "action"})
        account_id = _canonical_uuid(params["account_id"])
        provider = _provider(body["provider"])
        action = body["action"]
        if type(action) is not str or action not in _DELETE_ACTIONS:
            raise _invalid_request()
        _enforce_provider_policy(provider)
        self._require_account_provider(account_id, provider)
        kind = (
            "account_delete"
            if action == "logout_and_delete"
            else "account_delete_force_local"
        )
        return self._accepted_job(kind, account_id=account_id)

    def _get_job(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        _require_no_body(request)
        job_id = _canonical_uuid(params["job_id"])
        view = self.jobs.get(job_id)
        return json_response(200, {"job": _job_view_to_dict(view)})

    def _sync_status(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        _require_no_body(request)
        sync = self._require_sync_service()
        return json_response(200, {"sync": sync.status().to_dict()})

    def _update_sync_apps(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"apps"})
        apps = _apps(body["apps"], allow_empty=True)
        sync = self._require_sync_service()
        config = sync.update_apps(apps)
        with self._sync_lock:
            self._issued_sync_digests.clear()
        return json_response(200, {"apps": list(config.apps)})

    def _sync_preview(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"direction", "apps"})
        direction = body["direction"]
        if type(direction) is not str or direction not in _DIRECTIONS:
            raise _invalid_request()
        apps = _apps(body["apps"], allow_empty=False)
        sync = self._require_sync_service()
        configured_apps = getattr(sync.config, "apps", None)
        if type(configured_apps) is not list or any(
            app not in configured_apps for app in apps
        ):
            raise _invalid_request()
        preview = sync.preview(cast(Literal["backup", "apply"], direction), apps)
        digest = preview.digest
        if type(digest) is not str or not digest:
            return internal_error_response()
        typed_direction = cast(Literal["backup", "apply"], direction)
        with self._sync_lock:
            self._issued_sync_digests[digest] = (sync, typed_direction, apps)
        return json_response(200, {"preview": preview.to_dict()})

    def _sync_execute(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"digest"})
        digest = body["digest"]
        if type(digest) is not str:
            raise _invalid_request()
        with self._sync_lock:
            issued = self._issued_sync_digests.pop(digest, None)
        if issued is None:
            return _stale_sync_response()
        sync, direction, apps = issued
        try:
            current = sync.preview(direction, apps)
        except (KeyError, OSError, RuntimeError, ValueError):
            return _stale_sync_response()
        if current.digest != digest:
            return _stale_sync_response()
        with self._sync_lock:
            self._pending_sync_services[digest] = sync
        try:
            return self._accepted_job("sync_execute", account_id=digest)
        except BaseException:
            with self._sync_lock:
                self._pending_sync_services.pop(digest, None)
                self._issued_sync_digests[digest] = issued
            raise

    def _select_sync_folder(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _optional_empty_json_object(request)
        _require_exact_keys(body, set())
        selected = self._folder_picker()
        if selected is None:
            return json_response(200, {"selected": False})
        if (
            not isinstance(selected, Path)
            or not selected.is_absolute()
            or selected.is_symlink()
            or not selected.exists()
            or not selected.is_dir()
        ):
            raise _ApiProblem(
                422,
                "invalid_sync_folder",
                "The selected folder is not a safe DotSync folder.",
            )
        try:
            new_sync = self._sync_folder_initializer(selected)
        except Exception:
            raise _ApiProblem(
                422,
                "invalid_sync_folder",
                "The selected folder could not be initialized for DotSync.",
            ) from None
        config_path = folder_config_path(selected)
        if (
            new_sync is None
            or not config_path.is_file()
            or config_path.is_symlink()
            or getattr(getattr(new_sync, "config", None), "dir", None) != selected
        ):
            raise _ApiProblem(
                422,
                "invalid_sync_folder",
                "The selected folder could not be initialized for DotSync.",
            )
        self._state_store.save(AppState(sync_dir=str(selected)))
        with self._sync_lock:
            self._sync = new_sync
            self._issued_sync_digests.clear()
        return json_response(200, {"selected": True})

    def _reveal_app_data(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _optional_empty_json_object(request)
        _require_exact_keys(body, set())
        self._reveal_app_data_callback(self._paths.root)
        return json_response(200, {"revealed": True})

    def _heartbeat(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        body = _optional_empty_json_object(request)
        _require_exact_keys(body, set())
        self._heartbeat_callback()
        return json_response(200, {"status": "ok"})

    def _account_action_request(
        self,
        request: ApiRequest,
        params: dict[str, str],
    ) -> tuple[str, ProviderName]:
        body = _required_json_object(request)
        _require_exact_keys(body, {"provider"})
        account_id = _canonical_uuid(params["account_id"])
        provider = _provider(body["provider"])
        _enforce_provider_policy(provider)
        return account_id, provider

    def _require_account_provider(
        self,
        account_id: str,
        provider: ProviderName,
    ) -> ManagedAccount:
        account = self._account_store.get(account_id)
        if account.provider != provider:
            raise AccountNotFound("account provider mismatch")
        return account

    def _require_sync_service(self) -> SyncService:
        with self._sync_lock:
            sync = self._sync
        if sync is None:
            raise _ApiProblem(
                409,
                "sync_not_configured",
                "Select a DotSync folder before using sync operations.",
            )
        return sync

    def _accepted_job(self, kind: str, *, account_id: str) -> HttpResponse:
        with self._job_lifecycle_lock:
            job = self.jobs.submit(kind, account_id=account_id)
        return json_response(202, {"job_id": job.id})

    def _job_operations(self):
        return {
            "account_login": self._login_job,
            "account_refresh": self._refresh_job,
            "account_logout": self._logout_job,
            "account_delete": self._delete_job,
            "account_delete_force_local": self._force_delete_job,
            "sync_execute": self._sync_execute_job,
        }

    def _login_job(self, context: JobContext) -> dict[str, object]:
        account_id = _context_value(context)

        def report(progress: LoginProgress) -> None:
            state = progress.state
            if state not in {
                "starting",
                "waiting_for_browser",
                "waiting_for_user",
                "done",
            }:
                state = "starting"
            if progress.verification_url is not None:
                self._open_provider_url(progress.verification_url)
            safe = {"state": state}
            if state in {"waiting_for_browser", "waiting_for_user"}:
                context.waiting_for_user(safe)
            else:
                context.report(safe)

        account = self._usage.login(
            account_id,
            report,
            cancel_event=context.cancel_event,
        )
        return {"account": _account_to_dict(account)}

    def _refresh_job(self, context: JobContext) -> dict[str, object]:
        result = self._usage.refresh(
            _context_value(context),
            cancel_event=context.cancel_event,
        )
        return _usage_result_to_dict(result)

    def _logout_job(self, context: JobContext) -> dict[str, object]:
        account = self._usage.logout(
            _context_value(context),
            cancel_event=context.cancel_event,
        )
        return {"account": _account_to_dict(account)}

    def _delete_job(self, context: JobContext) -> dict[str, object]:
        self._usage.delete_account(
            _context_value(context),
            force_local=False,
            job_context=context,
        )
        return {"deleted": True}

    def _force_delete_job(self, context: JobContext) -> dict[str, object]:
        self._usage.delete_account(
            _context_value(context),
            force_local=True,
            job_context=context,
        )
        return {"deleted": True}

    def _sync_execute_job(self, context: JobContext) -> dict[str, object]:
        digest = _context_value(context)
        with self._sync_lock:
            sync = self._pending_sync_services.pop(digest, None)
        if sync is None:
            raise RuntimeError("sync job is missing its issuing service")
        result = sync.execute(digest)
        return {
            "direction": result.direction,
            "changed": list(result.changed),
            "unchanged": list(result.unchanged),
            "failed": list(result.failed),
            "duration_ms": result.duration_ms,
        }


def _match_route(path: str, query: str) -> tuple[_Route | None, dict[str, str]]:
    if query or not path.startswith("/"):
        return None, {}
    segments = tuple(path[1:].split("/"))
    for route in _ROUTES:
        if len(segments) != len(route.shape):
            continue
        params: dict[str, str] = {}
        matched = True
        for actual, expected in zip(segments, route.shape, strict=True):
            if expected.startswith(":"):
                if not actual:
                    matched = False
                    break
                params[expected[1:]] = actual
            elif actual != expected:
                matched = False
                break
        if matched:
            return route, params
    return None, {}


def _required_json_object(request: ApiRequest) -> dict[str, object]:
    lengths = _content_lengths(request.headers)
    if len(lengths) != 1:
        raise _invalid_request()
    length = _parse_content_length(lengths[0])
    if length == 0:
        raise _invalid_request()
    return _read_json_object(request, length)


def _optional_empty_json_object(request: ApiRequest) -> dict[str, object]:
    _reject_transfer_encoding(request.headers)
    content_types = request.headers.get_all("Content-Type", failobj=[])
    if content_types and (
        len(content_types) != 1 or not _is_json_content_type(content_types[0])
    ):
        raise _ApiProblem(
            415,
            "unsupported_media_type",
            "Request bodies must use UTF-8 application/json.",
        )
    lengths = request.headers.get_all("Content-Length", failobj=[])
    if not lengths:
        return {}
    if len(lengths) != 1:
        raise _invalid_request()
    length = _parse_content_length(lengths[0])
    if length == 0:
        return {}
    return _read_json_object(request, length, transfer_encoding_checked=True)


def _read_json_object(
    request: ApiRequest,
    length: int,
    *,
    transfer_encoding_checked: bool = False,
) -> dict[str, object]:
    if not transfer_encoding_checked:
        _reject_transfer_encoding(request.headers)
    if length > MAX_REQUEST_BYTES:
        raise _ApiProblem(
            413,
            "request_too_large",
            "JSON request bodies must not exceed 65536 bytes.",
        )
    content_types = request.headers.get_all("Content-Type", failobj=[])
    if len(content_types) != 1 or not _is_json_content_type(content_types[0]):
        raise _ApiProblem(
            415,
            "unsupported_media_type",
            "Request bodies must use UTF-8 application/json.",
        )
    raw = request.stream.read(length)
    if len(raw) != length:
        raise _invalid_request()
    try:
        text = raw.decode("utf-8", errors="strict")
        data = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except (RecursionError, UnicodeError, ValueError):
        raise _invalid_request() from None
    if type(data) is not dict:
        raise _invalid_request()
    return cast(dict[str, object], data)


def _require_no_body(request: ApiRequest) -> None:
    _reject_transfer_encoding(request.headers)
    lengths = request.headers.get_all("Content-Length", failobj=[])
    if not lengths:
        return
    if len(lengths) != 1 or _parse_content_length(lengths[0]) != 0:
        raise _invalid_request()


def _content_lengths(headers: Message) -> list[str]:
    _reject_transfer_encoding(headers)
    return cast(list[str], headers.get_all("Content-Length", failobj=[]))


def _reject_transfer_encoding(headers: Message) -> None:
    if headers.get_all("Transfer-Encoding", failobj=[]):
        raise _invalid_request()


def _parse_content_length(value: object) -> int:
    if type(value) is not str or _CONTENT_LENGTH.fullmatch(value) is None:
        raise _invalid_request()
    if len(value) > 5:
        raise _ApiProblem(
            413,
            "request_too_large",
            "JSON request bodies must not exceed 65536 bytes.",
        )
    length = int(value)
    if length > MAX_REQUEST_BYTES:
        raise _ApiProblem(
            413,
            "request_too_large",
            "JSON request bodies must not exceed 65536 bytes.",
        )
    return length


def _is_json_content_type(value: object) -> bool:
    if type(value) is not str:
        return False
    parts = [part.strip().lower() for part in value.split(";")]
    if not parts or parts[0] != "application/json":
        return False
    if len(parts) == 1:
        return True
    return len(parts) == 2 and parts[1] in {"charset=utf-8", 'charset="utf-8"'}


def _reject_json_constant(value: str) -> object:
    raise ValueError("non-finite JSON number")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _require_exact_keys(data: dict[str, object], expected: set[str]) -> None:
    if set(data) != expected:
        raise _invalid_request()


def _provider(value: object) -> ProviderName:
    if type(value) is not str or value not in _PROVIDERS:
        raise _invalid_request()
    return cast(ProviderName, value)


def _label(value: object) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 80:
        raise _invalid_request()
    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        or unicodedata.bidirectional(character) in _DANGEROUS_BIDI_CLASSES
        for character in value
    ):
        raise _invalid_request()
    return value


def _canonical_uuid(value: object) -> str:
    if type(value) is not str:
        raise _invalid_request()
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise _invalid_request() from None
    if str(parsed) != value:
        raise _invalid_request()
    return value


def _apps(value: object, *, allow_empty: bool) -> tuple[str, ...]:
    if type(value) is not list or (not allow_empty and not value):
        raise _invalid_request()
    apps = cast(list[object], value)
    if any(type(app) is not str or app not in APP_NAMES for app in apps):
        raise _invalid_request()
    selected = cast(tuple[str, ...], tuple(apps))
    if len(set(selected)) != len(selected):
        raise _invalid_request()
    return selected


def _enforce_provider_policy(provider: ProviderName) -> None:
    if provider == "claude":
        raise _ApiProblem(
            403,
            "provider_policy_disabled",
            _CLAUDE_POLICY_MESSAGE,
        )


def _context_value(context: JobContext) -> str:
    if type(context.account_id) is not str or not context.account_id:
        raise RuntimeError("job context is missing its operation identifier")
    return context.account_id


def _account_to_dict(
    account: ManagedAccount,
    *,
    usage: UsageSnapshot | None = None,
) -> dict[str, object]:
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
        "usage": _usage_snapshot_to_dict(usage) if usage is not None else None,
    }


def _usage_window_to_dict(window: UsageWindow) -> dict[str, object]:
    return {
        "name": window.name,
        "limit_id": window.limit_id,
        "label": window.label,
        "used_percent": window.used_percent,
        "duration_minutes": window.duration_minutes,
        "resets_at": window.resets_at,
    }


def _usage_snapshot_to_dict(snapshot: UsageSnapshot) -> dict[str, object]:
    return {
        "account_id": snapshot.account_id,
        "provider": snapshot.provider,
        "windows": [_usage_window_to_dict(window) for window in snapshot.windows],
        "observed_at": snapshot.observed_at,
        "source": snapshot.source,
        "provider_version": snapshot.provider_version,
    }


def _usage_result_to_dict(result: UsageResult) -> dict[str, object]:
    error_code = result.error_code
    if error_code is not None and error_code not in _SAFE_USAGE_ERROR_CODES:
        error_code = "provider_unavailable"
    return {
        "usage": (
            _usage_snapshot_to_dict(result.snapshot)
            if result.snapshot is not None
            else None
        ),
        "stale": result.stale,
        "error_code": error_code,
    }


def _job_view_to_dict(view: JobView) -> dict[str, object]:
    return {
        "id": view.id,
        "kind": view.kind,
        "state": view.state,
        "account_id": view.account_id,
        "progress": view.progress,
        "result": view.result,
        "error_code": view.error_code,
    }


def _stale_sync_response() -> HttpResponse:
    return error_response(
        409,
        "stale_sync_plan",
        "Create a new sync preview before executing.",
    )


def _invalid_request() -> _ApiProblem:
    return _ApiProblem(
        400,
        "invalid_request",
        "The request body or route identifier is invalid.",
    )
