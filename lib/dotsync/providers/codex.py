"""Official Codex app-server adapter for isolated managed accounts."""

from __future__ import annotations

import os
import queue
import re
import secrets
import stat
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from dotsync import __version__
from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.private_fs import UnsafePrivatePath, ensure_private_dir
from dotsync.usage import UsageSnapshot, UsageWindow

from .base import LoginProgress, ProviderError
from .process import (
    JsonRpcProcess,
    provider_environment,
    resolve_executable,
    run_checked,
)


_RPC_TIMEOUT_SECONDS = 30.0
_LOGIN_TIMEOUT_SECONDS = 600.0
_MAX_CONFIG_BYTES = 1024 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_MISSING = object()
_USER_AGENT_VERSION = re.compile(
    r"^codex_cli_rs/(?P<version>\d+(?:\.\d+){2}(?:[-+][A-Za-z0-9.-]+)?)"
)
_CREDENTIAL_STORE_KEY = "cli_auth_credentials_store"


RpcFactory = Callable[..., JsonRpcProcess]
ExecutableResolver = Callable[..., Path]
CheckedRunner = Callable[..., Any]


@dataclass(frozen=True)
class _CodexInvocation:
    executable: Path
    environment: dict[str, str]
    cwd: Path


def _schema_error() -> ProviderError:
    return ProviderError(
        "unsupported_cli_version",
        "The installed Codex app-server returned an unsupported response.",
    )


def _safe_path_error() -> ProviderError:
    return ProviderError(
        "unsafe_account_path",
        "The managed Codex account path is unsafe.",
    )


def _absolute_parts(path: Path) -> tuple[str, ...]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    return absolute.parts[1:]


def _open_directory_without_links(path: Path) -> int:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in _absolute_parts(path):
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_config(home_fd: int) -> str | None:
    try:
        descriptor = os.open("config.toml", _READ_FLAGS, dir_fd=home_fd)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePrivatePath("Codex config must be a regular file")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="strict") as file:
            descriptor = -1
            value = file.read(_MAX_CONFIG_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(value.encode("utf-8")) > _MAX_CONFIG_BYTES:
        raise UnsafePrivatePath("Codex config exceeds the private file limit")
    return value


def _write_config(home_fd: int, value: str) -> None:
    existing = os.stat("config.toml", dir_fd=home_fd, follow_symlinks=False)
    if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
        raise UnsafePrivatePath("Codex config must be a regular file")

    temporary_name = f".config.toml.{secrets.token_hex(16)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=home_fd)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            descriptor = -1
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.replace(
            temporary_name,
            "config.toml",
            src_dir_fd=home_fd,
            dst_dir_fd=home_fd,
        )
    except BaseException:
        try:
            os.unlink(temporary_name, dir_fd=home_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    final_fd = os.open("config.toml", _READ_FLAGS, dir_fd=home_fd)
    try:
        metadata = os.fstat(final_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePrivatePath("Codex config must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise UnsafePrivatePath("Codex config permissions are unsafe")
    finally:
        os.close(final_fd)


def _invalid_config_error() -> ProviderError:
    return ProviderError(
        "provider_unavailable",
        "The managed Codex configuration is invalid.",
    )


def _parse_config(value: str) -> dict[str, Any]:
    try:
        return tomllib.loads(value) if value else {}
    except (tomllib.TOMLDecodeError, UnicodeError):
        pass
    raise _invalid_config_error()


def _skip_toml_layout(value: str, start: int) -> int:
    index = start
    while index < len(value):
        if value[index] in " \t\r\n":
            index += 1
            continue
        if value[index] != "#":
            break
        newline = value.find("\n", index)
        index = len(value) if newline < 0 else newline + 1
    return index


def _toml_statement_end(value: str, start: int) -> int:
    index = start
    array_depth = 0
    inline_table_depth = 0
    string_state: str | None = None
    while index < len(value):
        if string_state == "basic":
            if value[index] == "\\":
                index += 2
            elif value[index] == '"':
                string_state = None
                index += 1
            else:
                index += 1
            continue
        if string_state == "literal":
            if value[index] == "'":
                string_state = None
            index += 1
            continue
        if string_state in {"multiline_basic", "multiline_literal"}:
            quote = '"' if string_state == "multiline_basic" else "'"
            if string_state == "multiline_basic" and value[index] == "\\":
                index += 2
                continue
            if value[index] == quote:
                run_end = index
                while run_end < len(value) and value[run_end] == quote:
                    run_end += 1
                if run_end - index >= 3:
                    string_state = None
                index = run_end
                continue
            index += 1
            continue

        if value.startswith('"""', index):
            string_state = "multiline_basic"
            index += 3
            continue
        if value.startswith("'''", index):
            string_state = "multiline_literal"
            index += 3
            continue
        character = value[index]
        if character == '"':
            string_state = "basic"
            index += 1
        elif character == "'":
            string_state = "literal"
            index += 1
        elif character == "[":
            array_depth += 1
            index += 1
        elif character == "]":
            array_depth -= 1
            index += 1
        elif character == "{":
            inline_table_depth += 1
            index += 1
        elif character == "}":
            inline_table_depth -= 1
            index += 1
        elif character == "#":
            if array_depth == 0 and inline_table_depth == 0:
                return index
            newline = value.find("\n", index)
            index = len(value) if newline < 0 else newline
        elif character in "\r\n":
            if array_depth == 0 and inline_table_depth == 0:
                return index
            index += 1
        else:
            index += 1
    return len(value)


def _top_level_statement_spans(value: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while True:
        start = _skip_toml_layout(value, index)
        if start >= len(value) or value[start] == "[":
            return spans
        end = _toml_statement_end(value, start)
        spans.append((start, end))
        index = end


def _assignment_key(statement: str) -> str | None:
    index = 0
    string_state: str | None = None
    while index < len(statement):
        character = statement[index]
        if string_state == "basic":
            if character == "\\":
                index += 2
            elif character == '"':
                string_state = None
                index += 1
            else:
                index += 1
            continue
        if string_state == "literal":
            if character == "'":
                string_state = None
            index += 1
            continue
        if character == '"':
            string_state = "basic"
            index += 1
        elif character == "'":
            string_state = "literal"
            index += 1
        elif character == "=":
            key_source = statement[:index].strip()
            try:
                probe = tomllib.loads(f"{key_source} = 0")
            except tomllib.TOMLDecodeError:
                return None
            if probe == {_CREDENTIAL_STORE_KEY: 0}:
                return _CREDENTIAL_STORE_KEY
            return None
        else:
            index += 1
    return None


def _force_file_credential_store(value: str) -> str:
    parsed = _parse_config(value)
    candidate_spans = [
        (start, end)
        for start, end in _top_level_statement_spans(value)
        if _assignment_key(value[start:end]) == _CREDENTIAL_STORE_KEY
    ]
    has_top_level_key = _CREDENTIAL_STORE_KEY in parsed
    if has_top_level_key != (len(candidate_spans) == 1):
        raise _invalid_config_error()

    setting = f'{_CREDENTIAL_STORE_KEY} = "file"'
    if candidate_spans:
        start, end = candidate_spans[0]
        separator = " " if end < len(value) and value[end] == "#" else ""
        updated = value[:start] + setting + separator + value[end:]
    else:
        updated = setting + "\n" + value
    final = _parse_config(updated)
    if final.get(_CREDENTIAL_STORE_KEY) != "file":
        raise _invalid_config_error()
    return updated


class CodexUsageProvider:
    """Read subscription identity and limits from official Codex RPC methods."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        rpc_factory: RpcFactory = JsonRpcProcess,
        executable_resolver: ExecutableResolver = resolve_executable,
        checked_runner: CheckedRunner = run_checked,
        clock: Callable[[], datetime] | None = None,
        rpc_timeout: float = _RPC_TIMEOUT_SECONDS,
        login_timeout: float = _LOGIN_TIMEOUT_SECONDS,
    ) -> None:
        self._paths = paths
        self._rpc_factory = rpc_factory
        self._executable_resolver = executable_resolver
        self._checked_runner = checked_runner
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._rpc_timeout = rpc_timeout
        self._login_timeout = login_timeout

    def prepare_profile(self, account: ManagedAccount) -> None:
        home = self._account_paths(account)[0]
        try:
            ensure_private_dir(home, root=self._paths.root)
            home_fd = _open_directory_without_links(home)
            try:
                current = _read_config(home_fd)
                if current is None:
                    create_fd = os.open(
                        "config.toml",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=home_fd,
                    )
                    os.close(create_fd)
                    current = ""
                updated = _force_file_credential_store(current)
                _write_config(home_fd, updated)
                written = _read_config(home_fd)
                if written is None or _parse_config(written).get(
                    _CREDENTIAL_STORE_KEY
                ) != "file":
                    raise _invalid_config_error()
            finally:
                os.close(home_fd)
        except ProviderError:
            raise
        except (OSError, UnicodeError, UnsafePrivatePath):
            raise _safe_path_error() from None

    def login(
        self,
        account: ManagedAccount,
        report: Callable[[LoginProgress], None],
        *,
        cancel_event: threading.Event | None = None,
    ) -> ProviderIdentity:
        self.prepare_profile(account)
        notifications: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()

        def collect_notification(method: str, params: Any) -> None:
            notifications.put((method, params))

        report(LoginProgress("starting"))
        identity: ProviderIdentity | None = None
        failure: ProviderError | None = None
        try:
            with self._initialized_rpc(
                account, on_notification=collect_notification
            ) as (rpc, _):
                result = rpc.request(
                    "account/login/start",
                    {
                        "type": "chatgpt",
                        "useHostedLoginSuccessPage": True,
                        "appBrand": "codex",
                    },
                    cancel_event=cancel_event,
                )
                login_id, auth_url = self._login_start_result(result)
                report(
                    LoginProgress(
                        "waiting_for_browser",
                        verification_url=auth_url,
                    )
                )
                report(LoginProgress("waiting_for_user"))
                self._wait_for_login(
                    rpc,
                    login_id,
                    notifications,
                    cancel_event,
                )
                identity = self._read_identity(rpc, cancel_event)
                report(LoginProgress("done"))
        except ProviderError as error:
            failure = self._normalize_error(error, operation="login")
        if failure is not None:
            raise failure
        if identity is None:
            raise _schema_error()
        return identity

    def refresh_usage(self, account: ManagedAccount) -> UsageSnapshot:
        snapshot: UsageSnapshot | None = None
        failure: ProviderError | None = None
        try:
            with self._initialized_rpc(account) as (rpc, version):
                result = rpc.request("account/rateLimits/read", {})
            windows = self._rate_limit_windows(result)
            snapshot = UsageSnapshot(
                account_id=account.id,
                provider="codex",
                windows=windows,
                observed_at=self._observed_at(),
                source="codex_app_server",
                provider_version=version,
            )
        except ProviderError as error:
            failure = self._normalize_error(error, operation="refresh")
        if failure is not None:
            raise failure
        if snapshot is None:
            raise _schema_error()
        return snapshot

    def logout(self, account: ManagedAccount) -> None:
        invocation = self._prepare_invocation(account)
        failure: ProviderError | None = None
        app_server_start_failed = False
        try:
            with self._initialized_rpc(account, invocation=invocation) as (rpc, _):
                result = rpc.request("account/logout", {})
                if type(result) is not dict or result:
                    raise _schema_error()
        except ProviderError as error:
            if error.code == "rpc_start_failed":
                app_server_start_failed = True
            else:
                failure = self._normalize_error(error, operation="logout")
        if failure is not None:
            raise failure
        if not app_server_start_failed:
            return

        try:
            self._checked_runner(
                [invocation.executable, "logout"],
                env=invocation.environment,
                cwd=invocation.cwd,
                timeout=self._rpc_timeout,
            )
        except ProviderError as fallback_error:
            failure = self._normalize_error(
                fallback_error,
                operation="logout",
            )
        if failure is not None:
            raise failure

    def _account_paths(self, account: ManagedAccount) -> tuple[Path, Path, Path, Path]:
        if account.provider != "codex":
            raise _safe_path_error()
        try:
            root = self._paths.account_root("codex", account.id)
            return (
                self._paths.account_home("codex", account.id),
                self._paths.account_probe("codex", account.id),
                self._paths.account_tmp("codex", account.id),
                root,
            )
        except ValueError:
            raise _safe_path_error() from None

    def _ensure_account_directories(self, account: ManagedAccount) -> tuple[Path, Path]:
        home, probe, temporary, root = self._account_paths(account)
        try:
            ensure_private_dir(home, root=self._paths.root)
            ensure_private_dir(probe, root=self._paths.root)
            ensure_private_dir(temporary, root=self._paths.root)
        except (OSError, UnsafePrivatePath):
            raise _safe_path_error() from None
        return root, probe

    @contextmanager
    def _initialized_rpc(
        self,
        account: ManagedAccount,
        *,
        on_notification: Callable[[str, Any], None] | None = None,
        invocation: _CodexInvocation | None = None,
    ) -> Iterator[tuple[JsonRpcProcess, str]]:
        invocation = invocation or self._prepare_invocation(account)
        executable = invocation.executable
        environment = invocation.environment
        probe = invocation.cwd
        rpc = self._rpc_factory(
            [executable, "app-server"],
            env=environment,
            cwd=probe,
            timeout=self._rpc_timeout,
            on_notification=on_notification,
        )
        with rpc:
            result = rpc.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "dotsync",
                        "title": "DotSync",
                        "version": __version__,
                    }
                },
            )
            version = self._initialize_result(result, environment["CODEX_HOME"])
            rpc.notify("initialized", {})
            yield rpc, version

    def _prepare_invocation(self, account: ManagedAccount) -> _CodexInvocation:
        root, probe = self._ensure_account_directories(account)
        environment = provider_environment("codex", root)
        try:
            executable = self._executable_resolver(
                "codex", path=environment.get("PATH")
            )
        except ProviderError as error:
            if error.code == "executable_unavailable":
                raise ProviderError(
                    "cli_missing", "Codex CLI is not installed."
                ) from None
            raise
        return _CodexInvocation(executable, environment, probe)

    @staticmethod
    def _initialize_result(result: Any, expected_home: str) -> str:
        if type(result) is not dict:
            raise _schema_error()
        required = {
            "userAgent": str,
            "codexHome": str,
            "platformFamily": str,
            "platformOs": str,
        }
        if any(type(result.get(key)) is not value for key, value in required.items()):
            raise _schema_error()
        if result["codexHome"] != expected_home:
            raise _safe_path_error()
        if result["platformFamily"] != "unix" or result["platformOs"] != "macos":
            raise _schema_error()
        match = _USER_AGENT_VERSION.match(result["userAgent"])
        if match is None:
            raise _schema_error()
        return match.group("version")

    @staticmethod
    def _login_start_result(result: Any) -> tuple[str, str]:
        if type(result) is not dict or result.get("type") != "chatgpt":
            raise _schema_error()
        login_id = result.get("loginId")
        auth_url = result.get("authUrl")
        if not isinstance(login_id, str) or not login_id.strip():
            raise _schema_error()
        if not isinstance(auth_url, str):
            raise _schema_error()
        parsed_url = None
        try:
            parsed_url = urlsplit(auth_url)
        except ValueError:
            pass
        if (
            parsed_url is None
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
        ):
            raise _schema_error()
        return login_id, auth_url

    def _wait_for_login(
        self,
        rpc: JsonRpcProcess,
        login_id: str,
        notifications: queue.SimpleQueue[tuple[str, Any]],
        cancel_event: threading.Event | None,
    ) -> None:
        deadline = time.monotonic() + self._login_timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                try:
                    result = rpc.request(
                        "account/login/cancel", {"loginId": login_id}
                    )
                    if (
                        type(result) is not dict
                        or set(result) != {"status"}
                        or result["status"] not in {"canceled", "notFound"}
                    ):
                        raise _schema_error()
                except ProviderError as error:
                    if error.code == "unsupported_cli_version":
                        raise
                raise ProviderError("login_cancelled", "Codex login was cancelled.")
            if time.monotonic() >= deadline:
                raise ProviderError(
                    "provider_unavailable", "Codex login did not complete in time."
                )
            try:
                method, params = notifications.get(timeout=0.05)
            except queue.Empty:
                continue
            if method != "account/login/completed":
                continue
            if type(params) is not dict:
                raise _schema_error()
            notification_login_id = params.get("loginId")
            if not isinstance(notification_login_id, str):
                raise _schema_error()
            if notification_login_id != login_id:
                continue
            if type(params.get("success")) is not bool:
                raise _schema_error()
            error = params.get("error")
            if error is not None and not isinstance(error, str):
                raise _schema_error()
            if not params["success"]:
                raise ProviderError(
                    "reauth_required", "Codex login did not complete successfully."
                )
            return

    @staticmethod
    def _read_identity(
        rpc: JsonRpcProcess,
        cancel_event: threading.Event | None,
    ) -> ProviderIdentity:
        result = rpc.request(
            "account/read",
            {"refreshToken": False},
            cancel_event=cancel_event,
        )
        if (
            type(result) is not dict
            or "account" not in result
            or type(result.get("requiresOpenaiAuth")) is not bool
        ):
            raise _schema_error()
        account = result["account"]
        if account is None:
            raise ProviderError(
                "reauth_required", "Codex login is no longer authenticated."
            )
        if (
            type(account) is not dict
            or account.get("type") != "chatgpt"
            or "email" not in account
            or "planType" not in account
        ):
            raise _schema_error()
        email = account["email"]
        plan = account["planType"]
        if email is not None and (not isinstance(email, str) or not email.strip()):
            raise _schema_error()
        if not isinstance(plan, str) or not plan.strip():
            raise _schema_error()
        return ProviderIdentity(display_name=None, email=email, plan=plan)

    @staticmethod
    def _rate_limit_windows(result: Any) -> tuple[UsageWindow, ...]:
        if type(result) is not dict:
            raise _schema_error()
        buckets = result.get("rateLimitsByLimitId", _MISSING)
        snapshots: list[tuple[str, dict[str, Any]]]
        if buckets is _MISSING or buckets is None:
            fallback = result.get("rateLimits", _MISSING)
            if type(fallback) is not dict:
                raise _schema_error()
            raw_fallback_id = fallback.get("limitId")
            fallback_id = "codex" if raw_fallback_id is None else raw_fallback_id
            if not isinstance(fallback_id, str) or not fallback_id.strip():
                raise _schema_error()
            snapshots = [(fallback_id, fallback)]
        else:
            if type(buckets) is not dict or not buckets:
                raise _schema_error()
            if any(
                not isinstance(limit_id, str) or not limit_id.strip()
                for limit_id in buckets
            ):
                raise _schema_error()
            snapshots = []
            for limit_id in sorted(buckets):
                snapshot = buckets[limit_id]
                if type(snapshot) is not dict:
                    raise _schema_error()
                embedded_id = snapshot.get("limitId")
                if embedded_id is not None and embedded_id != limit_id:
                    raise _schema_error()
                snapshots.append((limit_id, snapshot))

        windows: list[UsageWindow] = []
        for limit_id, snapshot in snapshots:
            label = snapshot.get("limitName")
            if label is not None and (
                not isinstance(label, str) or not label.strip()
            ):
                raise _schema_error()
            for field in ("primary", "secondary"):
                raw_window = snapshot.get(field)
                if raw_window is None:
                    continue
                windows.append(
                    CodexUsageProvider._rate_limit_window(
                        limit_id,
                        label,
                        raw_window,
                    )
                )
        if not windows:
            raise _schema_error()
        return tuple(windows)

    @staticmethod
    def _rate_limit_window(
        limit_id: str,
        label: str | None,
        raw_window: Any,
    ) -> UsageWindow:
        if type(raw_window) is not dict:
            raise _schema_error()
        percentage = raw_window.get("usedPercent")
        duration = raw_window.get("windowDurationMins")
        resets_at = raw_window.get("resetsAt")
        if type(percentage) not in {int, float}:
            raise _schema_error()
        if type(duration) is not int or duration <= 0:
            raise _schema_error()
        if resets_at is not None and (
            type(resets_at) is not int or resets_at < 0
        ):
            raise _schema_error()
        reset_timestamp = None
        if resets_at is not None:
            try:
                reset_timestamp = (
                    datetime.fromtimestamp(resets_at, timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
            except (OverflowError, OSError, ValueError):
                reset_timestamp = None
            if reset_timestamp is None:
                raise _schema_error()
        name = {
            300: "five_hour",
            10080: "seven_day",
        }.get(duration, "other")
        window = None
        try:
            window = UsageWindow(
                name=name,
                limit_id=limit_id,
                label=label,
                used_percent=percentage,
                duration_minutes=duration,
                resets_at=reset_timestamp,
            )
        except (OverflowError, TypeError, ValueError):
            pass
        if window is None:
            raise _schema_error()
        return window

    def _observed_at(self) -> str:
        observed = self._clock()
        if not isinstance(observed, datetime) or observed.tzinfo is None:
            raise _schema_error()
        return (
            observed.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _normalize_error(error: ProviderError, *, operation: str) -> ProviderError:
        if error.code in {
            "cli_missing",
            "login_cancelled",
            "provider_unavailable",
            "reauth_required",
            "unsafe_account_path",
            "unsupported_cli_version",
        }:
            return error
        if error.code == "executable_unavailable":
            return ProviderError("cli_missing", "Codex CLI is not installed.")
        if error.code in {
            "rpc_line_too_large",
            "rpc_method_not_found",
            "rpc_invalid_request",
            "rpc_invalid_params",
            "rpc_protocol_error",
        }:
            return _schema_error()
        if error.code == "rpc_authentication_error":
            return ProviderError(
                "reauth_required",
                "Codex authentication is required.",
            )
        if error.code == "rpc_timeout" and operation == "refresh":
            return ProviderError("refresh_timeout", "Codex usage refresh timed out.")
        if error.code == "rpc_cancelled" and operation == "login":
            return ProviderError("login_cancelled", "Codex login was cancelled.")
        if operation == "logout" and error.code.startswith("process_"):
            return ProviderError("logout_failed", "Codex logout failed.")
        return ProviderError(
            "provider_unavailable",
            f"Codex {operation} is unavailable.",
        )
