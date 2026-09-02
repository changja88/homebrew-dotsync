"""Strict, JSON-only routing for the authenticated local web boundary."""

from __future__ import annotations

import copy
import ipaddress
import json
import math
import os
import re
import socket
import stat
import threading
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import BinaryIO, Literal, cast
from urllib.parse import unquote, urlsplit

from dotsync.accounts import (
    AccountConflict,
    AccountNotFound,
    AccountStore,
    AccountStoreError,
    ManagedAccount,
    ProviderIdentity,
    ProviderName,
)
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState, AppStateStore
from dotsync.apps import APP_NAMES
from dotsync.apps.base import AppStatus
from dotsync.config import (
    Config,
    ConfigError,
    initialize_config_file,
    load_config_from,
    save_config,
)
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
        "account_conflict",
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
_SAFE_ACCOUNT_TIMESTAMP = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z"
)
_SAFE_IDENTITY_EMAIL = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"\.[A-Za-z]{2,63}\Z"
)
_SAFE_HUMAN_SYMBOLS = frozenset("+")
_URI_SCHEME_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_HOST_TOKEN = re.compile(r"[A-Za-z0-9.-]+")
# Candidate callbacks receive no picker path. Validation uses this non-directory
# root so even a misbehaving build-time collaborator cannot traverse a selected
# path that may have been concurrently replaced.
_CANDIDATE_BUILD_ROOT = Path("/dev/null")
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
        sync_folder_initializer: Callable[[], SyncService],
        reveal_app_data: Callable[[Path], object],
        heartbeat: Callable[[], bool],
        open_provider_url: Callable[[str], object],
        job_lifecycle_lock: threading.RLock,
        job_registry: JobRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
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
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sync_lock = threading.RLock()
        self._sync_generation = 0
        self._sync_attention_observation: tuple[int, datetime] | None = None
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
                        "enabled": True,
                        "status": "available",
                        "message": None,
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
        self._require_account_provider(account_id, provider)
        if action == "remove_local_profile_anyway":
            self._cancel_active_account_jobs(account_id)
            try:
                self._usage.delete_account(
                    account_id,
                    force_local=True,
                    cancel_event=threading.Event(),
                )
            except BaseException as error:
                Path("/private/tmp/dotsync-delete-debug.log").write_text(
                    f"{type(error).__name__}: {error}\n",
                    encoding="utf-8",
                )
                raise
            return json_response(200, {"deleted": True})
        return self._accepted_job(
            "account_delete",
            account_id=account_id,
            provider=provider,
        )

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
        status_data = _sync_status_to_dict(status)
        try:
            observed_at = _validated_summary_now(self._clock())
        except BaseException:
            observed_at = None
        with self._sync_lock:
            if not self._sync_is_current_locked(sync, generation):
                return _stale_sync_response()
            if observed_at is None:
                self._sync_attention_observation = None
            else:
                current_observation = self._sync_attention_observation
                if (
                    current_observation is None
                    or observed_at >= current_observation[1]
                ):
                    self._sync_attention_observation = (
                        sum(
                            app.status.state != "clean" for app in status.apps
                        ),
                        observed_at,
                    )
        return json_response(200, {"sync": status_data})

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
            self._sync_attention_observation = None
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
                config_exists = _verify_config_file_no_follow(
                    directory_fd,
                    allow_missing=True,
                )
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
                if not config_exists:
                    initialize_config_file(
                        directory_fd,
                        _initial_selected_config(current, canonical),
                    )
                revalidated_fd = _open_revalidated_sync_directory(
                    canonical,
                    initial_identity,
                )
                os.close(revalidated_fd)
                revalidated_fd = None
                new_sync = _build_sync_directory_candidate(
                    self._sync_folder_initializer,
                    canonical,
                )
                revalidated_fd = _open_revalidated_sync_directory(
                    canonical,
                    initial_identity,
                )
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
        self._sync_attention_observation = None

    def _begin_sync_reconciliation_locked(self) -> None:
        self._sync = None
        self._sync_generation += 1
        self._issued_sync_digests.clear()
        self._sync_attention_observation = None

    def _reconcile_config_locked(
        self,
        current: SyncService,
        *,
        candidate: SyncService | None,
    ) -> None:
        self._begin_sync_reconciliation_locked()
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
        self._sync = authoritative

    def _reconcile_state_locked(
        self,
        current: SyncService | None,
        *,
        candidate: SyncService | None,
    ) -> None:
        self._begin_sync_reconciliation_locked()
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
            authoritative = _build_sync_directory_candidate(
                self._sync_folder_initializer,
                authoritative_dir,
            )
        self._sync = authoritative

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

    def _cancel_active_account_jobs(self, account_id: str) -> None:
        with self._job_lifecycle_lock:
            active_job_ids = [
                view.id
                for view in self.jobs.list_jobs()
                if view.account_id == account_id
                and view.state not in {"succeeded", "failed"}
            ]
            for job_id in active_job_ids:
                self.jobs.cancel(job_id)

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


def _validated_summary_now(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise TypeError("summary clock must return an aware datetime")
    try:
        offset = value.utcoffset()
    except BaseException as error:
        raise TypeError("summary clock returned an invalid datetime") from error
    if offset is None:
        raise TypeError("summary clock must return an aware datetime")
    return value.astimezone(timezone.utc)


def _summary_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def load_persisted_sync_service(
    *,
    state_store: AppStateStore,
    factory: Callable[[], SyncService],
) -> SyncService | None:
    """Load a saved SyncService only after no-follow identity revalidation."""
    state = state_store.load()
    if state.sync_dir is None:
        return None
    canonical = _canonical_safe_directory(Path(state.sync_dir))
    directory_fd = _open_directory_no_follow(canonical)
    try:
        _verify_config_file_no_follow(directory_fd, allow_missing=False)
        initial_identity = _directory_identity(directory_fd)
        revalidated_fd = _open_revalidated_sync_directory(
            canonical,
            initial_identity,
        )
        os.close(revalidated_fd)
        candidate = _build_sync_directory_candidate(factory, canonical)
        revalidated_fd = _open_revalidated_sync_directory(
            canonical,
            initial_identity,
        )
        os.close(revalidated_fd)
        return candidate
    finally:
        os.close(directory_fd)


def _config_with_apps(config: object, apps: tuple[str, ...]) -> Config:
    if type(config) is not Config:
        raise TypeError("sync service has an unsupported config")
    candidate = copy.deepcopy(config)
    candidate.apps = list(apps)
    return candidate


def _same_config(first: object, second: object) -> bool:
    return type(first) is Config and type(second) is Config and first == second


def _validate_sync_candidate(candidate: object, config: Config) -> None:
    _validate_sync_candidate_shape(candidate, config)
    candidate.validate_config()


def _validate_sync_candidate_shape(candidate: object, config: Config) -> None:
    if candidate is None or getattr(candidate, "config", None) is not config:
        raise TypeError("sync service factory returned an unsupported candidate")
    for method in (
        "status",
        "preview",
        "execute",
        "with_config",
        "validate_config",
    ):
        if not callable(getattr(candidate, method, None)):
            raise TypeError("sync service factory returned an unsupported candidate")


def _validate_sync_directory_candidate(candidate: object, sync_dir: Path) -> None:
    config = getattr(candidate, "config", None)
    if type(config) is not Config or config.dir != sync_dir:
        raise TypeError("sync folder initializer returned an unsupported candidate")
    _validate_sync_candidate_shape(candidate, config)


def _build_sync_directory_candidate(
    factory: Callable[[], SyncService],
    sync_dir: Path,
) -> SyncService:
    config = load_config_from(sync_dir)
    construction_config = _candidate_construction_config(config)
    candidate = factory()
    candidate.config = construction_config
    _validate_sync_candidate(candidate, construction_config)
    candidate.config = config
    _validate_sync_directory_candidate(candidate, sync_dir)
    return candidate


def _candidate_construction_config(config: Config) -> Config:
    return Config(
        dir=_CANDIDATE_BUILD_ROOT,
        apps=list(config.apps),
        backup_dir=_CANDIDATE_BUILD_ROOT,
        backup_keep=config.backup_keep,
        bettertouchtool_presets=list(config.bettertouchtool_presets),
        app_options=copy.deepcopy(config.app_options),
    )


def _service_uses_directory(service: object, value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and getattr(getattr(service, "config", None), "dir", None) == Path(value)
    )


def _initial_selected_config(
    current: SyncService | None,
    sync_dir: Path,
) -> Config:
    current_config = getattr(current, "config", None)
    if type(current_config) is not Config:
        return Config(dir=sync_dir, apps=[])
    return Config(
        dir=sync_dir,
        apps=list(current_config.apps),
        backup_keep=current_config.backup_keep,
        bettertouchtool_presets=list(current_config.bettertouchtool_presets),
        app_options=copy.deepcopy(current_config.app_options),
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


def _open_revalidated_sync_directory(
    path: Path,
    expected_identity: tuple[int, int],
) -> int:
    descriptor = _open_directory_no_follow(path)
    try:
        if _directory_identity(descriptor) != expected_identity:
            raise OSError("selected folder identity changed")
        _verify_config_file_no_follow(descriptor, allow_missing=False)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_config_file_no_follow(
    directory_fd: int,
    *,
    allow_missing: bool,
) -> bool:
    try:
        path_metadata = os.stat(
            "dotsync.toml",
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if allow_missing:
            return False
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
    return True


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
    try:
        return _validated_account_label(value)
    except TypeError:
        raise _invalid_request() from None


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


def _context_value(context: JobContext) -> str:
    if type(context.account_id) is not str or not context.account_id:
        raise RuntimeError("job context is missing its operation identifier")
    return context.account_id


def _account_to_dict(
    account: ManagedAccount,
    *,
    usage: UsageSnapshot | None = None,
) -> dict[str, object]:
    if (
        type(account) is not ManagedAccount
        or not _is_canonical_uuid(account.id)
        or account.provider not in _PROVIDERS
        or account.state not in _ACCOUNT_STATES
        or type(account.identity) is not ProviderIdentity
    ):
        raise TypeError("account has an unsupported shape")
    label = _validated_account_label(account.label)
    created_at = _validated_account_created_at(account.created_at)
    return {
        "id": account.id,
        "provider": account.provider,
        "label": label,
        "state": account.state,
        "identity": {
            "display_name": _safe_identity_human_text(
                account.identity.display_name,
                maximum=160,
            ),
            "email": _safe_identity_email(account.identity.email),
            "plan": _safe_identity_human_text(
                account.identity.plan,
                maximum=80,
            ),
        },
        "created_at": created_at,
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
    label = _validated_account_label(account["label"])
    created_at = _validated_account_created_at(account["created_at"])
    identity_value = account["identity"]
    if type(identity_value) is not dict or set(identity_value) != {
        "display_name",
        "email",
        "plan",
    }:
        raise TypeError("account job identity has an unsupported shape")
    identity = cast(dict[str, object], identity_value)
    display_name = _safe_identity_human_text(
        identity["display_name"],
        maximum=160,
    )
    email = _safe_identity_email(identity["email"])
    plan = _safe_identity_human_text(identity["plan"], maximum=80)
    return {
        "id": account["id"],
        "provider": account["provider"],
        "label": label,
        "state": account["state"],
        "identity": {
            "display_name": display_name,
            "email": email,
            "plan": plan,
        },
        "created_at": created_at,
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


def _validated_account_label(value: object) -> str:
    validated = _validated_account_human_text(
        value,
        maximum=80,
        required=True,
    )
    assert validated is not None
    return validated


def _validated_account_created_at(value: object) -> str:
    if (
        type(value) is not str
        or _SAFE_ACCOUNT_TIMESTAMP.fullmatch(value) is None
    ):
        raise TypeError("account created_at has an unsupported shape")
    return value


def _safe_identity_human_text(
    value: object,
    *,
    maximum: int,
) -> str | None:
    return _validated_account_human_text(
        value,
        maximum=maximum,
        required=False,
    )


def _validated_account_human_text(
    value: object,
    *,
    maximum: int,
    required: bool,
) -> str | None:
    if value is None:
        if required:
            raise TypeError("required account text is missing")
        return None
    if type(value) is not str:
        raise TypeError("account text has an unsupported shape")
    if required and value != value.strip():
        raise TypeError("required account text is not canonical")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized) > maximum:
        return _unsafe_account_human_text(required)
    if _contains_structural_locator(normalized):
        return _unsafe_account_human_text(required)
    for character in normalized:
        category = unicodedata.category(character)
        if (
            category in {"Cc", "Cs"}
            or unicodedata.bidirectional(character) in _DANGEROUS_BIDI_CLASSES
            or (
                category[0] not in {"L", "M", "N", "P"}
                and category != "Zs"
                and character not in _SAFE_HUMAN_SYMBOLS
            )
        ):
            return _unsafe_account_human_text(required)
    return normalized


def _unsafe_account_human_text(required: bool) -> None:
    if required:
        raise TypeError("required account text is unsafe")
    return None


def _contains_structural_locator(value: str) -> bool:
    probe = unicodedata.normalize("NFKC", value)
    while True:
        if _is_structural_locator_probe(probe):
            return True
        if _PERCENT_ESCAPE.search(probe) is None:
            return False
        try:
            decoded = unquote(probe, encoding="utf-8", errors="strict")
        except UnicodeError:
            return True
        decoded = unicodedata.normalize("NFKC", decoded)
        if decoded == probe:
            return False
        probe = decoded


def _is_structural_locator_probe(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if candidate.startswith("~") or any(
        separator in candidate for separator in ("/", "\\", "?", "#")
    ):
        return True
    scheme = _URI_SCHEME_PREFIX.match(candidate)
    if scheme is not None:
        remainder = candidate[scheme.end() :]
        if not remainder or not remainder[0].isspace():
            return True
    if _is_forbidden_locator_host(candidate):
        return True
    if any(
        _is_forbidden_locator_host(token)
        for token in _HOST_TOKEN.findall(candidate)
    ):
        return True
    if any(character.isspace() for character in candidate):
        return False
    try:
        parsed = urlsplit(f"//{candidate}")
        port = parsed.port
    except ValueError:
        return ":" in candidate
    return parsed.hostname is not None and port is not None


def _is_forbidden_locator_host(value: str) -> bool:
    candidate = value.strip().strip("[]").rstrip(".")
    if not candidate:
        return False
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(candidate))
        except OSError:
            address = None
    if address is not None:
        return address.is_loopback or address.is_unspecified
    try:
        hostname = candidate.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        hostname = candidate.casefold()
    return "localhost" in hostname.split(".")


def _safe_identity_email(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("account identity email has an unsupported shape")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 254
        or ".." in normalized
        or _SAFE_IDENTITY_EMAIL.fullmatch(normalized) is None
    ):
        return None
    return normalized


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
