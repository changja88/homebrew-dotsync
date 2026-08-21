"""Strict, JSON-only routing for the authenticated local web boundary."""

from __future__ import annotations

import copy
import json
import os
import re
import stat
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
from dotsync.apps.base import AppStatus
from dotsync.config import Config, ConfigError, load_config_from, save_config
from dotsync.jobs import (
    JobContext,
    JobNotFound,
    JobRegistry,
    JobView,
    RegistryClosed,
    UnknownJobKind,
)
from dotsync.providers import LoginProgress
from dotsync.plan import path_fingerprint
from dotsync.sync_service import StaleSyncPlan, SyncAppStatus, SyncService, SyncStatus
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
_SYNC_STATUS_STATES = frozenset({"clean", "dirty", "missing", "unknown"})
_SYNC_STATUS_DIRECTIONS = frozenset(
    {"", "local-newer", "folder-newer", "diverged"}
)
_API_JOB_KINDS = frozenset(
    {
        "account_login",
        "account_refresh",
        "account_logout",
        "account_delete",
        "account_delete_force_local",
        "sync_execute",
    }
)
_ACCOUNT_JOB_KINDS = frozenset(
    {
        "account_login",
        "account_refresh",
        "account_logout",
        "account_delete",
        "account_delete_force_local",
    }
)
_JOB_STATES = frozenset(
    {"queued", "running", "waiting_for_user", "succeeded", "failed"}
)
_LOGIN_PROGRESS_STATES = frozenset(
    {"starting", "waiting_for_browser", "waiting_for_user", "done"}
)
_JOB_ERROR_CODES = frozenset(
    {
        "cancelled",
        "cli_missing",
        "invalid_job_child",
        "invalid_job_json",
        "invalid_job_progress",
        "invalid_job_result",
        "invalid_job_state",
        "job_failed",
        "login_cancelled",
        "logout_cancelled",
        "logout_failed",
        "provider_unavailable",
        "reauth_required",
        "refresh_cancelled",
        "refresh_timeout",
        "unsafe_account_path",
        "unsupported_cli_version",
        "unsupported_usage_layout",
    }
)
_ACCOUNT_STATES = frozenset(
    {"logged_out", "ready", "reauth_required", "unsupported", "error"}
)
_SYNC_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
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


@dataclass(frozen=True)
class _ApiJobExpectation:
    kind: str
    operation_id: str
    provider: ProviderName | None = None
    sync_direction: Literal["backup", "apply"] | None = None
    sync_apps: tuple[str, ...] = ()


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
        heartbeat: Callable[[], bool],
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
        self._sync_generation = 0
        self._issued_sync_digests: dict[
            str,
            tuple[
                int,
                SyncService,
                Literal["backup", "apply"],
                tuple[str, ...],
            ],
        ] = {}
        self._pending_sync_services: dict[str, SyncService] = {}
        self._api_job_expectations: dict[str, _ApiJobExpectation] = {}
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
        return self._accepted_job(
            "account_login", account_id=account_id, provider=provider
        )

    def _refresh(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        account_id, provider = self._account_action_request(request, params)
        self._require_account_provider(account_id, provider)
        return self._accepted_job(
            "account_refresh", account_id=account_id, provider=provider
        )

    def _logout(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        account_id, provider = self._account_action_request(request, params)
        self._require_account_provider(account_id, provider)
        return self._accepted_job(
            "account_logout", account_id=account_id, provider=provider
        )

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
        return self._accepted_job(kind, account_id=account_id, provider=provider)

    def _get_job(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
        _require_no_body(request)
        job_id = _canonical_uuid(params["job_id"])
        with self._job_lifecycle_lock:
            view = self.jobs.get(job_id)
            expectation = self._api_job_expectations.get(job_id)
        if expectation is None:
            raise TypeError("job was not issued by the API")
        return json_response(
            200,
            {
                "job": _job_view_to_dict(
                    view,
                    requested_job_id=job_id,
                    expectation=expectation,
                )
            },
        )

    def _sync_status(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        _require_no_body(request)
        sync, generation = self._capture_sync_service()
        status = sync.status()
        with self._sync_lock:
            if not self._sync_is_current_locked(sync, generation):
                return _stale_sync_response()
        return json_response(200, {"sync": _sync_status_to_dict(status)})

    def _update_sync_apps(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"apps"})
        apps = _apps(body["apps"], allow_empty=True)
        with self._sync_lock:
            sync, generation = self._capture_sync_service_locked()
        try:
            candidate_config = _config_with_apps(sync.config, apps)
            candidate = sync.with_config(candidate_config)
            _validate_sync_candidate(candidate, candidate_config)
        except BaseException:
            with self._sync_lock:
                if self._sync_is_current_locked(sync, generation):
                    self._reconcile_config_locked(sync, candidate=None)
            raise
        with self._sync_lock:
            if not self._sync_is_current_locked(sync, generation):
                return _stale_sync_response()
            try:
                save_config(candidate_config)
            except BaseException:
                self._reconcile_config_locked(sync, candidate=candidate)
                raise
            self._publish_sync_locked(candidate)
        return json_response(200, {"apps": list(candidate_config.apps)})

    def _sync_preview(
        self, request: ApiRequest, params: dict[str, str]
    ) -> HttpResponse:
        body = _required_json_object(request)
        _require_exact_keys(body, {"direction", "apps"})
        direction = body["direction"]
        if type(direction) is not str or direction not in _DIRECTIONS:
            raise _invalid_request()
        apps = _apps(body["apps"], allow_empty=False)
        sync, generation = self._capture_sync_service()
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
            if not self._sync_is_current_locked(sync, generation):
                return _stale_sync_response()
            self._issued_sync_digests[digest] = (
                generation,
                sync,
                typed_direction,
                apps,
            )
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
        generation, sync, direction, apps = issued
        with self._sync_lock:
            if not self._sync_is_current_locked(sync, generation):
                return _stale_sync_response()
        try:
            current = sync.preview(direction, apps)
        except (KeyError, OSError, RuntimeError, ValueError):
            return _stale_sync_response()
        if current.digest != digest:
            return _stale_sync_response()
        with self._sync_lock:
            if not self._sync_is_current_locked(sync, generation):
                return _stale_sync_response()
            self._pending_sync_services[digest] = sync
        try:
            return self._accepted_job(
                "sync_execute",
                account_id=digest,
                sync_direction=direction,
                sync_apps=apps,
            )
        except BaseException:
            with self._sync_lock:
                self._pending_sync_services.pop(digest, None)
                if self._sync_is_current_locked(sync, generation):
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
        try:
            canonical = _canonical_safe_directory(selected)
            directory_fd = _open_directory_no_follow(canonical)
        except (OSError, TypeError, ValueError):
            raise _ApiProblem(
                422,
                "invalid_sync_folder",
                "The selected folder is not a safe DotSync folder.",
            ) from None
        revalidated_fd: int | None = None
        try:
            try:
                initial_identity = _directory_identity(directory_fd)
                _verify_config_file_no_follow(directory_fd, allow_missing=True)
            except (OSError, TypeError, ValueError):
                raise _ApiProblem(
                    422,
                    "invalid_sync_folder",
                    "The selected folder could not be initialized for DotSync.",
                ) from None
            with self._sync_lock:
                current = self._sync
                generation = self._sync_generation
            try:
                new_sync = self._sync_folder_initializer(canonical)
                revalidated_fd = _open_directory_no_follow(canonical)
                if _directory_identity(revalidated_fd) != initial_identity:
                    raise OSError("selected folder identity changed")
                _verify_config_file_no_follow(revalidated_fd, allow_missing=False)
                _validate_sync_directory_candidate(new_sync, canonical)
            except BaseException as error:
                with self._sync_lock:
                    if (
                        self._sync is current
                        and self._sync_generation == generation
                    ):
                        self._reconcile_state_locked(current, candidate=None)
                if not isinstance(error, Exception):
                    raise
                raise _ApiProblem(
                    422,
                    "invalid_sync_folder",
                    "The selected folder could not be initialized for DotSync.",
                ) from None
            with self._sync_lock:
                if generation != self._sync_generation:
                    return _stale_sync_response()
                try:
                    self._state_store.save(AppState(sync_dir=str(canonical)))
                except BaseException:
                    self._reconcile_state_locked(current, candidate=new_sync)
                    raise
                self._publish_sync_locked(new_sync)
            return json_response(200, {"selected": True})
        finally:
            if revalidated_fd is not None:
                os.close(revalidated_fd)
            os.close(directory_fd)

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
        if not self._heartbeat_callback():
            raise RegistryClosed("DotSync is closing")
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

    def _capture_sync_service(self) -> tuple[SyncService, int]:
        with self._sync_lock:
            return self._capture_sync_service_locked()

    def _capture_sync_service_locked(self) -> tuple[SyncService, int]:
        sync = self._sync
        generation = self._sync_generation
        if sync is None:
            raise _ApiProblem(
                409,
                "sync_not_configured",
                "Select a DotSync folder before using sync operations.",
            )
        return sync, generation

    def _sync_is_current_locked(
        self,
        sync: SyncService,
        generation: int,
    ) -> bool:
        return self._sync is sync and self._sync_generation == generation

    def _publish_sync_locked(self, sync: SyncService | None) -> None:
        self._sync = sync
        self._sync_generation += 1
        self._issued_sync_digests.clear()

    def _reconcile_config_locked(
        self,
        current: SyncService,
        *,
        candidate: SyncService | None,
    ) -> None:
        disk_config = load_config_from(current.config.dir)
        if candidate is not None and _same_config(
            disk_config, candidate.config
        ):
            authoritative = candidate
        elif _same_config(disk_config, current.config):
            authoritative = current
        else:
            authoritative = current.with_config(disk_config)
            _validate_sync_candidate(authoritative, disk_config)
        self._publish_sync_locked(authoritative)

    def _reconcile_state_locked(
        self,
        current: SyncService | None,
        *,
        candidate: SyncService | None,
    ) -> None:
        state = self._state_store.load()
        if type(state) is not AppState:
            raise TypeError("app state has an unsupported shape")
        if state.sync_dir is None:
            authoritative = None
        elif candidate is not None and _service_uses_directory(
            candidate, state.sync_dir
        ):
            authoritative = candidate
        elif current is not None and _service_uses_directory(
            current, state.sync_dir
        ):
            authoritative = current
        else:
            authoritative_dir = _canonical_safe_directory(Path(state.sync_dir))
            authoritative = self._sync_folder_initializer(authoritative_dir)
            _validate_sync_directory_candidate(authoritative, authoritative_dir)
        self._publish_sync_locked(authoritative)

    def _accepted_job(
        self,
        kind: str,
        *,
        account_id: str,
        provider: ProviderName | None = None,
        sync_direction: Literal["backup", "apply"] | None = None,
        sync_apps: tuple[str, ...] = (),
    ) -> HttpResponse:
        expectation = _ApiJobExpectation(
            kind=kind,
            operation_id=account_id,
            provider=provider,
            sync_direction=sync_direction,
            sync_apps=sync_apps,
        )
        with self._job_lifecycle_lock:
            job = self.jobs.submit(kind, account_id=account_id)
            if (
                not _is_canonical_uuid(job.id)
                or job.kind != kind
                or job.account_id != account_id
            ):
                raise TypeError("job registry returned an unsupported job")
            self._api_job_expectations[job.id] = expectation
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


def _canonical_safe_directory(selected: object) -> Path:
    if not isinstance(selected, Path) or not selected.is_absolute():
        raise TypeError("folder picker returned an unsupported path")
    canonical = Path(os.path.abspath(os.fspath(selected)))
    if canonical == Path(canonical.anchor):
        raise ValueError("the filesystem root cannot be a sync folder")
    return canonical


def _config_with_apps(config: object, apps: tuple[str, ...]) -> Config:
    if type(config) is not Config:
        raise TypeError("sync service has an unsupported config")
    candidate = copy.deepcopy(config)
    candidate.apps = list(apps)
    return candidate


def _same_config(first: object, second: object) -> bool:
    return type(first) is Config and type(second) is Config and first == second


def _validate_sync_candidate(candidate: object, config: Config) -> None:
    if candidate is None or getattr(candidate, "config", None) is not config:
        raise TypeError("sync service factory returned an unsupported candidate")
    for method in ("status", "preview", "execute", "with_config"):
        if not callable(getattr(candidate, method, None)):
            raise TypeError("sync service factory returned an unsupported candidate")


def _validate_sync_directory_candidate(candidate: object, sync_dir: Path) -> None:
    config = getattr(candidate, "config", None)
    if type(config) is not Config or config.dir != sync_dir:
        raise TypeError("sync folder initializer returned an unsupported candidate")
    _validate_sync_candidate(candidate, config)


def _service_uses_directory(service: object, value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and getattr(getattr(service, "config", None), "dir", None) == Path(value)
    )


def _open_directory_no_follow(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    current_fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            try:
                if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                    raise NotADirectoryError(component)
            except BaseException:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _directory_identity(directory_fd: int) -> tuple[int, int]:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError("selected path is not a directory")
    return metadata.st_dev, metadata.st_ino


def _verify_config_file_no_follow(
    directory_fd: int,
    *,
    allow_missing: bool,
) -> None:
    try:
        path_metadata = os.stat(
            "dotsync.toml",
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    if not stat.S_ISREG(path_metadata.st_mode):
        raise OSError("dotsync.toml is not a regular file")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    config_fd = os.open("dotsync.toml", flags, dir_fd=directory_fd)
    try:
        opened_metadata = os.fstat(config_fd)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_dev != path_metadata.st_dev
            or opened_metadata.st_ino != path_metadata.st_ino
        ):
            raise OSError("dotsync.toml changed during validation")
    finally:
        os.close(config_fd)


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


def _sync_status_to_dict(status: SyncStatus) -> dict[str, object]:
    if type(status) is not SyncStatus or not isinstance(status.sync_dir, Path):
        raise TypeError("sync status has an unsupported shape")
    apps: list[dict[str, object]] = []
    seen: set[str] = set()
    for app in status.apps:
        if (
            type(app) is not SyncAppStatus
            or app.name not in APP_NAMES
            or app.name in seen
            or type(app.status) is not AppStatus
            or app.status.state not in _SYNC_STATUS_STATES
            or type(app.status.direction) is not str
            or app.status.direction not in _SYNC_STATUS_DIRECTIONS
            or (app.status.state != "dirty" and app.status.direction)
        ):
            raise TypeError("sync status has an unsupported shape")
        seen.add(app.name)
        apps.append(
            {
                "name": app.name,
                "state": app.status.state,
                "direction": app.status.direction or None,
            }
        )
    return {
        "sync_dir": {
            "scope": "sync-root",
            "id": path_fingerprint(status.sync_dir),
        },
        "apps": apps,
    }


def _job_view_to_dict(
    view: JobView,
    *,
    requested_job_id: str,
    expectation: _ApiJobExpectation,
) -> dict[str, object]:
    if (
        type(view) is not JobView
        or not _is_canonical_uuid(view.id)
        or view.id != requested_job_id
        or view.kind not in _API_JOB_KINDS
        or view.kind != expectation.kind
        or view.account_id != expectation.operation_id
        or view.state not in _JOB_STATES
    ):
        raise TypeError("job view has an unsupported shape")

    if view.kind in _ACCOUNT_JOB_KINDS:
        if not _is_canonical_uuid(view.account_id):
            raise TypeError("job view has an unsupported account")
        if expectation.provider not in _PROVIDERS:
            raise TypeError("job view provider correlation failed")
        browser_account_id: str | None = cast(str, view.account_id)
    else:
        if (
            type(view.account_id) is not str
            or _SYNC_DIGEST.fullmatch(view.account_id) is None
        ):
            raise TypeError("sync job view has an unsupported digest")
        browser_account_id = None

    progress = _job_progress_to_dict(view)
    result = _job_result_to_dict(view, expectation)
    if view.state == "failed":
        if (
            view.result is not None
            or type(view.error_code) is not str
            or view.error_code not in _JOB_ERROR_CODES
        ):
            raise TypeError("failed job view has an unsupported shape")
        error_code: str | None = view.error_code
    else:
        if view.error_code is not None:
            raise TypeError("job view has an unexpected error code")
        error_code = None
        if view.state != "succeeded" and view.result is not None:
            raise TypeError("active job view has an unexpected result")

    return {
        "id": view.id,
        "kind": view.kind,
        "state": view.state,
        "account_id": browser_account_id,
        "progress": progress,
        "result": result,
        "error_code": error_code,
    }


def _job_progress_to_dict(view: JobView) -> dict[str, str]:
    if type(view.progress) is not dict:
        raise TypeError("job progress has an unsupported shape")
    if view.kind != "account_login":
        if view.progress or view.state == "waiting_for_user":
            raise TypeError("job progress has an unsupported shape")
        return {}
    if not view.progress:
        if view.state == "waiting_for_user":
            raise TypeError("login progress is missing its state")
        return {}
    if set(view.progress) != {"state"}:
        raise TypeError("login progress has an unsupported shape")
    progress_state = view.progress["state"]
    if progress_state not in _LOGIN_PROGRESS_STATES:
        raise TypeError("login progress has an unsupported state")
    is_waiting = progress_state in {"waiting_for_browser", "waiting_for_user"}
    if view.state == "waiting_for_user" and not is_waiting:
        raise TypeError("login progress does not match the job state")
    if view.state == "running" and is_waiting:
        raise TypeError("login progress does not match the job state")
    if view.state == "queued":
        raise TypeError("queued login has unexpected progress")
    return {"state": progress_state}


def _job_result_to_dict(
    view: JobView,
    expectation: _ApiJobExpectation,
) -> dict[str, object] | None:
    if view.state != "succeeded":
        if view.result is not None:
            raise TypeError("non-successful job has an unexpected result")
        return None
    if type(view.result) is not dict:
        raise TypeError("successful job is missing its result")
    if view.kind in {"account_login", "account_logout"}:
        if set(view.result) != {"account"}:
            raise TypeError("account job result has an unsupported shape")
        return {
            "account": _validated_account_result(
                view.result["account"], expectation
            )
        }
    if view.kind == "account_refresh":
        return _validated_refresh_result(view.result, expectation)
    if view.kind in {"account_delete", "account_delete_force_local"}:
        if view.result != {"deleted": True}:
            raise TypeError("delete job result has an unsupported shape")
        return {"deleted": True}
    return _validated_sync_result(view.result, expectation)


def _validated_account_result(
    value: object,
    expectation: _ApiJobExpectation,
) -> dict[str, object]:
    if (
        type(value) is not dict
        or set(value)
        != {"id", "provider", "label", "state", "identity", "created_at", "usage"}
    ):
        raise TypeError("account job result has an unsupported shape")
    account = cast(dict[str, object], value)
    if (
        account["id"] != expectation.operation_id
        or not _is_canonical_uuid(account["id"])
        or account["provider"] != expectation.provider
        or account["provider"] not in _PROVIDERS
        or account["state"] not in _ACCOUNT_STATES
        or account["usage"] is not None
    ):
        raise TypeError("account job result does not match its submission")
    label = account["label"]
    _safe_job_text(label, maximum=80)
    if cast(str, label) != cast(str, label).strip():
        raise TypeError("account job label is not canonical")
    _safe_job_text(account["created_at"], maximum=64)
    identity_value = account["identity"]
    if type(identity_value) is not dict or set(identity_value) != {
        "display_name",
        "email",
        "plan",
    }:
        raise TypeError("account job identity has an unsupported shape")
    identity = cast(dict[str, object], identity_value)
    _safe_job_text(identity["display_name"], maximum=256, optional=True)
    _safe_job_text(identity["email"], maximum=320, optional=True)
    _safe_job_text(identity["plan"], maximum=256, optional=True)
    return {
        "id": account["id"],
        "provider": account["provider"],
        "label": label,
        "state": account["state"],
        "identity": {
            "display_name": identity["display_name"],
            "email": identity["email"],
            "plan": identity["plan"],
        },
        "created_at": account["created_at"],
        "usage": None,
    }


def _validated_refresh_result(
    result: dict[str, object],
    expectation: _ApiJobExpectation,
) -> dict[str, object]:
    if set(result) != {"usage", "stale", "error_code"}:
        raise TypeError("refresh job result has an unsupported shape")
    stale = result["stale"]
    error_code = result["error_code"]
    if type(stale) is not bool or (
        error_code is not None
        and (type(error_code) is not str or error_code not in _SAFE_USAGE_ERROR_CODES)
    ):
        raise TypeError("refresh job result has an unsupported state")
    if (error_code is None and stale) or (error_code is not None and not stale):
        raise TypeError("refresh job result has inconsistent stale state")
    usage_value = result["usage"]
    if usage_value is None:
        if error_code is None:
            raise TypeError("successful refresh is missing usage")
        usage = None
    else:
        usage = _validated_usage_dict(usage_value, expectation)
    return {"usage": usage, "stale": stale, "error_code": error_code}


def _validated_usage_dict(
    value: object,
    expectation: _ApiJobExpectation,
) -> dict[str, object]:
    expected_keys = {
        "account_id",
        "provider",
        "windows",
        "observed_at",
        "source",
        "provider_version",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise TypeError("usage job result has an unsupported shape")
    raw = cast(dict[str, object], value)
    windows_value = raw["windows"]
    if type(windows_value) is not list:
        raise TypeError("usage job windows have an unsupported shape")
    windows: list[UsageWindow] = []
    for window_value in windows_value:
        if type(window_value) is not dict or set(window_value) != {
            "name",
            "limit_id",
            "label",
            "used_percent",
            "duration_minutes",
            "resets_at",
        }:
            raise TypeError("usage job window has an unsupported shape")
        window = cast(dict[str, object], window_value)
        try:
            parsed_window = UsageWindow(
                name=cast(object, window["name"]),
                limit_id=cast(object, window["limit_id"]),
                label=cast(object, window["label"]),
                used_percent=cast(object, window["used_percent"]),
                duration_minutes=cast(object, window["duration_minutes"]),
                resets_at=cast(object, window["resets_at"]),
            )
        except (TypeError, ValueError):
            raise TypeError("usage job window has invalid values") from None
        _safe_job_text(parsed_window.limit_id, maximum=256)
        _safe_job_text(
            parsed_window.label,
            maximum=256,
            optional=True,
            allow_empty=True,
        )
        windows.append(parsed_window)
    try:
        snapshot = UsageSnapshot(
            account_id=cast(object, raw["account_id"]),
            provider=cast(object, raw["provider"]),
            windows=tuple(windows),
            observed_at=cast(object, raw["observed_at"]),
            source=cast(object, raw["source"]),
            provider_version=cast(object, raw["provider_version"]),
        )
    except (TypeError, ValueError):
        raise TypeError("usage job result has invalid values") from None
    if (
        snapshot.account_id != expectation.operation_id
        or snapshot.provider != expectation.provider
    ):
        raise TypeError("usage job result does not match its account")
    _safe_job_text(snapshot.provider_version, maximum=256)
    return _usage_snapshot_to_dict(snapshot)


def _validated_sync_result(
    result: dict[str, object],
    expectation: _ApiJobExpectation,
) -> dict[str, object]:
    if set(result) != {
        "direction",
        "changed",
        "unchanged",
        "failed",
        "duration_ms",
    }:
        raise TypeError("sync job result has an unsupported shape")
    direction = result["direction"]
    duration_ms = result["duration_ms"]
    if (
        direction not in _DIRECTIONS
        or direction != expectation.sync_direction
        or type(duration_ms) is not int
        or duration_ms < 0
    ):
        raise TypeError("sync job result has invalid values")
    groups: list[list[str]] = []
    all_apps: list[str] = []
    for field in ("changed", "unchanged", "failed"):
        value = result[field]
        if type(value) is not list or any(
            type(app) is not str or app not in APP_NAMES
            for app in cast(list[object], value)
        ):
            raise TypeError("sync job result has invalid app names")
        apps = cast(list[str], value)
        if len(set(apps)) != len(apps):
            raise TypeError("sync job result has duplicate app names")
        groups.append(list(apps))
        all_apps.extend(apps)
    if len(set(all_apps)) != len(all_apps):
        raise TypeError("sync job result has overlapping app names")
    if set(all_apps) != set(expectation.sync_apps):
        raise TypeError("sync job result does not match its issued apps")
    return {
        "direction": direction,
        "changed": groups[0],
        "unchanged": groups[1],
        "failed": groups[2],
        "duration_ms": duration_ms,
    }


def _safe_job_text(
    value: object,
    *,
    maximum: int,
    optional: bool = False,
    allow_empty: bool = False,
) -> None:
    if value is None:
        if optional:
            return
        raise TypeError("job text is missing")
    if (
        type(value) is not str
        or len(value) > maximum
        or (not allow_empty and not value)
    ):
        raise TypeError("job text has an unsupported shape")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        or unicodedata.bidirectional(character) in _DANGEROUS_BIDI_CLASSES
        for character in value
    ):
        raise TypeError("job text has unsafe characters")
    folded = value.casefold()
    if (
        "://" in folded
        or folded.startswith(("file:", "data:", "javascript:", "oauth:", "urn:"))
        or value.startswith(("/", "\\"))
    ):
        raise TypeError("job text contains a locator")


def _is_canonical_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


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
