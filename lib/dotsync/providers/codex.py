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
)


_RPC_TIMEOUT_SECONDS = 30.0
_LOGIN_TIMEOUT_SECONDS = 600.0
_MAX_CONFIG_BYTES = 1024 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_USER_AGENT_VERSION = re.compile(
    r"^codex_cli_rs/(?P<version>\d+(?:\.\d+){2}(?:[-+][A-Za-z0-9.-]+)?)"
)
_TOP_LEVEL_CREDENTIAL_KEY = re.compile(
    r"^\s*(?:cli_auth_credentials_store|"
    r"\"cli_auth_credentials_store\"|'cli_auth_credentials_store')\s*="
)
_TABLE_HEADER = re.compile(r"^\s*\[\[?.+\]\]?\s*(?:#.*)?$")


RpcFactory = Callable[..., JsonRpcProcess]
ExecutableResolver = Callable[..., Path]


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


def _force_file_credential_store(value: str) -> str:
    try:
        parsed = tomllib.loads(value) if value else {}
    except (tomllib.TOMLDecodeError, UnicodeError):
        raise ProviderError(
            "provider_unavailable",
            "The managed Codex configuration is invalid.",
        ) from None

    lines = value.splitlines(keepends=True)
    table_index = len(lines)
    for index, line in enumerate(lines):
        if _TABLE_HEADER.fullmatch(line.rstrip("\r\n")):
            table_index = index
            break
    assignment_indexes = [
        index
        for index, line in enumerate(lines[:table_index])
        if _TOP_LEVEL_CREDENTIAL_KEY.match(line)
    ]
    has_top_level_key = "cli_auth_credentials_store" in parsed
    if has_top_level_key and len(assignment_indexes) != 1:
        raise ProviderError(
            "provider_unavailable",
            "The managed Codex configuration is invalid.",
        )

    setting = 'cli_auth_credentials_store = "file"\n'
    if assignment_indexes:
        lines[assignment_indexes[0]] = setting
        return "".join(lines)
    return setting + value


class CodexUsageProvider:
    """Read subscription identity and limits from official Codex RPC methods."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        rpc_factory: RpcFactory = JsonRpcProcess,
        executable_resolver: ExecutableResolver = resolve_executable,
        clock: Callable[[], datetime] | None = None,
        rpc_timeout: float = _RPC_TIMEOUT_SECONDS,
        login_timeout: float = _LOGIN_TIMEOUT_SECONDS,
    ) -> None:
        self._paths = paths
        self._rpc_factory = rpc_factory
        self._executable_resolver = executable_resolver
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
                _write_config(home_fd, _force_file_credential_store(current))
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
                return identity
        except ProviderError as error:
            raise self._normalize_error(error, operation="login") from None

    def refresh_usage(self, account: ManagedAccount) -> UsageSnapshot:
        try:
            with self._initialized_rpc(account) as (rpc, version):
                result = rpc.request("account/rateLimits/read", {})
            windows = self._rate_limit_windows(result)
            return UsageSnapshot(
                account_id=account.id,
                provider="codex",
                windows=windows,
                observed_at=self._observed_at(),
                source="codex_app_server",
                provider_version=version,
            )
        except ProviderError as error:
            raise self._normalize_error(error, operation="refresh") from None

    def logout(self, account: ManagedAccount) -> None:
        try:
            with self._initialized_rpc(account) as (rpc, _):
                result = rpc.request("account/logout", {})
                if type(result) is not dict or result:
                    raise _schema_error()
        except ProviderError as error:
            raise self._normalize_error(error, operation="logout") from None

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
    ) -> Iterator[tuple[JsonRpcProcess, str]]:
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
        parsed_url = urlsplit(auth_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
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
        if type(result) is not dict or type(result.get("rateLimits")) is not dict:
            raise _schema_error()
        buckets = result.get("rateLimitsByLimitId")
        snapshots: list[tuple[str, dict[str, Any]]]
        if buckets is None:
            fallback = result["rateLimits"]
            raw_fallback_id = fallback.get("limitId")
            fallback_id = "codex" if raw_fallback_id is None else raw_fallback_id
            if not isinstance(fallback_id, str) or not fallback_id.strip():
                raise _schema_error()
            snapshots = [(fallback_id, fallback)]
        else:
            if type(buckets) is not dict or not buckets:
                raise _schema_error()
            snapshots = []
            for limit_id in sorted(buckets):
                snapshot = buckets[limit_id]
                if not isinstance(limit_id, str) or not limit_id.strip():
                    raise _schema_error()
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
                raise _schema_error() from None
        name = {
            300: "five_hour",
            10080: "seven_day",
        }.get(duration, "other")
        try:
            return UsageWindow(
                name=name,
                limit_id=limit_id,
                label=label,
                used_percent=percentage,
                duration_minutes=duration,
                resets_at=reset_timestamp,
            )
        except (TypeError, ValueError):
            raise _schema_error() from None

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
        if error.code == "rpc_remote_error":
            code = "logout_failed" if operation == "logout" else "reauth_required"
            return ProviderError(code, f"Codex {operation} requires authentication.")
        if error.code == "rpc_timeout" and operation == "refresh":
            return ProviderError("refresh_timeout", "Codex usage refresh timed out.")
        if error.code == "rpc_cancelled" and operation == "login":
            return ProviderError("login_cancelled", "Codex login was cancelled.")
        code = "logout_failed" if operation == "logout" else "provider_unavailable"
        return ProviderError(code, f"Codex {operation} is unavailable.")
