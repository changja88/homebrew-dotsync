"""Official Claude CLI adapter and versioned ``/usage`` parser."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.private_fs import UnsafePrivatePath, ensure_private_dir
from dotsync.usage import UsageSnapshot, UsageWindow

from .base import LoginProgress, ProviderError
from .process import (
    PtySession,
    provider_environment,
    resolve_executable,
    run_checked,
)


_MAX_TERMINAL_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_TERMINAL_WIDTH = 240
_MAX_TERMINAL_HEIGHT = 100
_MAX_ESCAPE_BYTES = 64
_MAX_STATUS_BYTES = 64 * 1024
_MAX_VERSION_COMPONENT = 9_999
_MINIMUM_CLI_VERSION = (2, 1, 215)
_COMMAND_TIMEOUT_SECONDS = 15.0
_LOGIN_TIMEOUT_SECONDS = 600.0
_REFRESH_TIMEOUT_SECONDS = 45.0
_NUMERIC_VERSION = re.compile(
    r"^(?P<major>\d{1,6})\.(?P<minor>\d{1,6})\.(?P<patch>\d{1,6})$"
)
_CLI_VERSION_OUTPUT = re.compile(
    r"^\s*(?P<version>\d{1,6}\.\d{1,6}\.\d{1,6})"
    r"(?:\s+\(Claude Code\))?\s*$"
)
_USAGE_ROWS = (
    re.compile(
        r"^(?P<value>\d{1,3}(?:\.\d{1,3})?)%\s+used$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?P<value>\d{1,3}(?:\.\d{1,3})?)%\s*사용$"),
)
_ENGLISH_RESET = re.compile(
    r"^Resets\s+in\s+"
    r"(?:(?P<days>\d{1,3})\s+days?\s*)?"
    r"(?:(?P<hours>\d{1,2})\s+(?:hours?|hrs?)\s*)?"
    r"(?:(?P<minutes>\d{1,2})\s+(?:minutes?|mins?))?$",
    re.IGNORECASE,
)
_KOREAN_RESET = re.compile(
    r"^(?:(?P<days>\d{1,3})일\s+)?"
    r"(?:(?P<hours>\d{1,2})시간\s+)?"
    r"(?:(?P<minutes>\d{1,2})분\s+)?"
    r"후\s+(?:재설정|초기화)$"
)
_WINDOW_LABELS = {
    "five_hour": re.compile(r"(?:\b5[- ]?hour\b|5\s*시간)", re.IGNORECASE),
    "seven_day": re.compile(r"(?:\b7[- ]?day\b|7\s*일)", re.IGNORECASE),
}
_WINDOW_METADATA = {
    "five_hour": ("claude_five_hour", "5-hour limit", 300),
    "seven_day": ("claude_seven_day", "7-day limit", 10_080),
}
_LOGIN_SUCCESS_MARKERS = (
    "authentication successful",
    "login successful",
    "successfully logged in",
    "인증 성공",
    "로그인 성공",
)
_LOGIN_CANCEL_MARKERS = ("login cancelled", "login canceled", "로그인 취소")
_REAUTH_MARKERS = (
    "authentication required",
    "not logged in",
    "please run /login",
    "로그인이 필요",
    "인증이 필요",
)
_INPUT_READY_MARKERS = ("❯", "type / for commands", "명령어를 입력")
_USAGE_FOOTER_MARKERS = (
    "press esc to go back",
    "esc to go back",
    "닫으려면 esc",
)
_USAGE_HEADER_MARKERS = ("usage", "사용량")


PtyFactory = Callable[..., PtySession]
ExecutableResolver = Callable[..., Path]
CheckedRunner = Callable[..., Any]


@dataclass(frozen=True)
class _ClaudeInvocation:
    executable: Path
    environment: dict[str, str]
    cwd: Path


def _terminal_error(code: str, message: str) -> ProviderError:
    return ProviderError(code, message)


def _usage_error() -> ProviderError:
    return ProviderError(
        "unsupported_usage_layout",
        "The installed Claude CLI returned an unsupported usage layout.",
    )


class TerminalScreen:
    """A fixed-size emulator for the bounded ANSI subset emitted by fixtures."""

    def __init__(self, *, width: int, height: int) -> None:
        if (
            type(width) is not int
            or type(height) is not int
            or not 1 <= width <= _MAX_TERMINAL_WIDTH
            or not 1 <= height <= _MAX_TERMINAL_HEIGHT
        ):
            raise _terminal_error(
                "terminal_dimensions_invalid",
                "The terminal screen dimensions are invalid.",
            )
        self._width = width
        self._height = height
        self._rows = [[" "] * width for _ in range(height)]
        self._row = 0
        self._column = 0
        self._input_bytes = 0
        self._pending_escape = ""

    def feed(self, value: str) -> None:
        if not isinstance(value, str):
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        added_bytes: int | None = None
        try:
            added_bytes = len(value.encode("utf-8", errors="strict"))
        except UnicodeError:
            value = ""
        if added_bytes is None:
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        if self._input_bytes + added_bytes > _MAX_TERMINAL_OUTPUT_BYTES:
            raise _terminal_error(
                "terminal_output_limit",
                "The terminal output exceeded its safe limit.",
            )
        self._input_bytes += added_bytes

        source = self._pending_escape + value
        self._pending_escape = ""
        index = 0
        while index < len(source):
            character = source[index]
            if character == "\x1b":
                consumed = self._consume_escape(source, index)
                if consumed is None:
                    pending = source[index:]
                    if len(pending.encode("utf-8")) > _MAX_ESCAPE_BYTES:
                        raise _terminal_error(
                            "terminal_output_invalid",
                            "The terminal output was invalid.",
                        )
                    self._pending_escape = pending
                    return
                index = consumed
                continue
            if character == "\r":
                self._column = 0
            elif character == "\n":
                self._line_feed()
            elif character == "\b":
                self._column = max(0, self._column - 1)
            elif ord(character) < 0x20 or ord(character) == 0x7F:
                raise _terminal_error(
                    "terminal_output_invalid", "The terminal output was invalid."
                )
            else:
                self._write(character)
            index += 1

    def text(self) -> str:
        if self._pending_escape:
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        lines = ["".join(row).rstrip() for row in self._rows]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _consume_escape(self, source: str, start: int) -> int | None:
        if start + 1 >= len(source):
            return None
        if source[start + 1] != "[":
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        index = start + 2
        while index < len(source) and not "@" <= source[index] <= "~":
            index += 1
            if index - start > _MAX_ESCAPE_BYTES:
                raise _terminal_error(
                    "terminal_output_invalid", "The terminal output was invalid."
                )
        if index >= len(source):
            return None
        parameters = source[start + 2 : index]
        if parameters and re.fullmatch(r"[0-9;]*", parameters) is None:
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        if parameters and any(not value for value in parameters.split(";")):
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        self._apply_csi(source[index], parameters)
        return index + 1

    def _apply_csi(self, operation: str, parameters: str) -> None:
        values = self._csi_values(parameters)
        if operation in {"A", "B", "C", "D", "E", "F", "G"}:
            self._require_csi_shape(values, {0, 1})
        elif operation in {"H", "f"}:
            self._require_csi_shape(values, {0, 2})
        elif operation in {"J", "K", "m"}:
            self._require_csi_shape(values, {0, 1})
        else:
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        first = values[0] if values else 0
        amount = first or 1
        if operation == "A":
            self._row = max(0, self._row - amount)
        elif operation == "B":
            self._row = min(self._height - 1, self._row + amount)
        elif operation == "C":
            self._column = min(self._width - 1, self._column + amount)
        elif operation == "D":
            self._column = max(0, self._column - amount)
        elif operation == "E":
            self._row = min(self._height - 1, self._row + amount)
            self._column = 0
        elif operation == "F":
            self._row = max(0, self._row - amount)
            self._column = 0
        elif operation == "G":
            self._column = min(self._width - 1, max(0, amount - 1))
        elif operation in {"H", "f"}:
            row = (values[0] if values and values[0] else 1) - 1
            column = (values[1] if len(values) > 1 and values[1] else 1) - 1
            self._row = min(self._height - 1, max(0, row))
            self._column = min(self._width - 1, max(0, column))
        elif operation == "J":
            if first not in {0, 2}:
                raise _terminal_error(
                    "terminal_output_invalid", "The terminal output was invalid."
                )
            self._erase_display(first)
        elif operation == "K":
            if first not in {0, 2}:
                raise _terminal_error(
                    "terminal_output_invalid", "The terminal output was invalid."
                )
            self._erase_line(first)
        elif operation == "m":
            if first not in {0, 1, 31}:
                raise _terminal_error(
                    "terminal_output_invalid", "The terminal output was invalid."
                )

    @staticmethod
    def _csi_values(parameters: str) -> list[int]:
        if not parameters:
            return []
        return [int(value) if value else 0 for value in parameters.split(";")]

    @staticmethod
    def _require_csi_shape(values: list[int], allowed_counts: set[int]) -> None:
        if len(values) not in allowed_counts:
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )

    def _erase_line(self, mode: int) -> None:
        if mode == 0:
            start, end = self._column, self._width
        elif mode == 1:
            start, end = 0, self._column + 1
        elif mode == 2:
            start, end = 0, self._width
        else:
            raise _terminal_error(
                "terminal_output_invalid", "The terminal output was invalid."
            )
        self._rows[self._row][start:end] = [" "] * (end - start)

    def _erase_display(self, mode: int) -> None:
        if mode == 2:
            for row in self._rows:
                row[:] = [" "] * self._width
            return
        if mode == 0:
            self._erase_line(0)
            for row_number in range(self._row + 1, self._height):
                self._rows[row_number][:] = [" "] * self._width
            return
        if mode == 1:
            for row_number in range(0, self._row):
                self._rows[row_number][:] = [" "] * self._width
            self._erase_line(1)
            return
        raise _terminal_error(
            "terminal_output_invalid", "The terminal output was invalid."
        )

    def _write(self, character: str) -> None:
        self._rows[self._row][self._column] = character
        self._column += 1
        if self._column < self._width:
            return
        self._column = 0
        self._line_feed()

    def _line_feed(self) -> None:
        if self._row < self._height - 1:
            self._row += 1
            return
        self._rows.pop(0)
        self._rows.append([" "] * self._width)


def parse_claude_usage(
    *,
    account_id: str,
    provider_version: str,
    terminal_bytes: bytes,
    observed_at: str,
) -> UsageSnapshot:
    """Parse one synthetic/redacted Claude 2.1.x usage screen safely."""
    snapshot: UsageSnapshot | None = None
    failed = False
    terminal_text: str | None = None
    screen: TerminalScreen | None = None
    try:
        version = _parse_numeric_version(provider_version)
        strategy = _usage_strategy(version)
        if (
            not isinstance(terminal_bytes, bytes)
            or len(terminal_bytes) > _MAX_TERMINAL_OUTPUT_BYTES
        ):
            raise _usage_error()
        terminal_text = terminal_bytes.decode("utf-8", errors="strict")
        screen = TerminalScreen(width=160, height=60)
        screen.feed(terminal_text)
        windows = strategy(screen.text(), observed_at)
        snapshot = UsageSnapshot(
            account_id=account_id,
            provider="claude",
            windows=windows,
            observed_at=observed_at,
            source="claude_usage",
            provider_version=provider_version,
        )
    except ProviderError:
        failed = True
    except (OverflowError, TypeError, UnicodeError, ValueError):
        failed = True
    terminal_bytes = b""
    terminal_text = None
    screen = None
    if failed or snapshot is None:
        raise _usage_error()
    return snapshot


def _parse_numeric_version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise _usage_error()
    match = _NUMERIC_VERSION.fullmatch(value)
    if match is None:
        raise _usage_error()
    version = tuple(
        int(match.group(name)) for name in ("major", "minor", "patch")
    )
    if any(component > _MAX_VERSION_COMPONENT for component in version):
        raise _usage_error()
    return version


def _usage_strategy(
    version: tuple[int, int, int],
) -> Callable[[str, str], tuple[UsageWindow, ...]]:
    if (2, 1, 215) <= version < (2, 2, 0):
        return _parse_v2_1_usage
    raise _usage_error()


def _parse_v2_1_usage(text: str, observed_at: str) -> tuple[UsageWindow, ...]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    labels: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        matches = [
            name for name, pattern in _WINDOW_LABELS.items() if pattern.search(line)
        ]
        if len(matches) > 1:
            raise _usage_error()
        if matches:
            labels.append((index, matches[0]))
    if not labels or sum(name == "five_hour" for _, name in labels) != 1:
        raise _usage_error()
    if len({name for _, name in labels}) != len(labels):
        raise _usage_error()

    parsed: dict[str, UsageWindow] = {}
    for position, (start, name) in enumerate(labels):
        end = labels[position + 1][0] if position + 1 < len(labels) else len(lines)
        block = lines[start:end]
        if len(block) < 3:
            raise _usage_error()
        percentage = _usage_percentage(block[1])
        reset_value = _relative_reset(block[2], observed_at)
        if percentage is None or reset_value is None:
            raise _usage_error()
        if any("%" in line for line in block[2:]):
            raise _usage_error()
        if any(
            _relative_reset(line, observed_at) is not None
            for line in block[3:]
        ):
            raise _usage_error()
        limit_id, label, duration = _WINDOW_METADATA[name]
        if not 0.0 <= percentage <= 100.0:
            raise _usage_error()
        if _minutes_between(observed_at, reset_value) > duration:
            raise _usage_error()
        parsed[name] = UsageWindow(
            name=name,
            limit_id=limit_id,
            label=label,
            used_percent=percentage,
            duration_minutes=duration,
            resets_at=reset_value,
        )

    return tuple(
        parsed[name] for name in ("five_hour", "seven_day") if name in parsed
    )


def _usage_percentage(line: str) -> float | None:
    matches = [pattern.fullmatch(line) for pattern in _USAGE_ROWS]
    values = [match.group("value") for match in matches if match is not None]
    if len(values) != 1:
        return None
    return float(values[0])


def _relative_reset(line: str, observed_at: str) -> str | None:
    match = _ENGLISH_RESET.fullmatch(line) or _KOREAN_RESET.fullmatch(line)
    if match is None:
        return None
    values = {
        name: int(match.group(name) or 0) for name in ("days", "hours", "minutes")
    }
    if values["hours"] >= 24 or values["minutes"] >= 60:
        raise _usage_error()
    delta = timedelta(**values)
    if delta <= timedelta(0):
        raise _usage_error()
    observed = _rfc3339_datetime(observed_at)
    return (
        (observed + delta)
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _minutes_between(start: str, end: str) -> int:
    elapsed = _rfc3339_datetime(end) - _rfc3339_datetime(start)
    return int(elapsed.total_seconds() / 60)


def _rfc3339_datetime(value: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise _usage_error()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise _usage_error()
    return parsed


def _unsupported_version_error() -> ProviderError:
    return ProviderError(
        "unsupported_cli_version",
        "The installed Claude CLI version is not supported.",
    )


def _unsafe_account_path_error() -> ProviderError:
    return ProviderError(
        "unsafe_account_path",
        "The managed Claude account path is unsafe.",
    )


def _reauthentication_error() -> ProviderError:
    return ProviderError(
        "reauth_required",
        "Claude authentication is required.",
    )


class ClaudeUsageProvider:
    """Use only official Claude CLI surfaces under one managed account home."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        pty_factory: PtyFactory = PtySession,
        executable_resolver: ExecutableResolver = resolve_executable,
        checked_runner: CheckedRunner = run_checked,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        command_timeout: float = _COMMAND_TIMEOUT_SECONDS,
        login_timeout: float = _LOGIN_TIMEOUT_SECONDS,
        refresh_timeout: float = _REFRESH_TIMEOUT_SECONDS,
    ) -> None:
        self._paths = paths
        self._pty_factory = pty_factory
        self._executable_resolver = executable_resolver
        self._checked_runner = checked_runner
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._command_timeout = command_timeout
        self._login_timeout = login_timeout
        self._refresh_timeout = refresh_timeout

    def login(
        self,
        account: ManagedAccount,
        report: Callable[[LoginProgress], None],
        *,
        cancel_event: threading.Event | None = None,
    ) -> ProviderIdentity:
        _raise_if_login_cancelled(cancel_event)
        identity: ProviderIdentity | None = None
        failure: ProviderError | None = None
        try:
            invocation = self._prepare_invocation(account)
            _raise_if_login_cancelled(cancel_event)
            self._read_version(invocation)
            _raise_if_login_cancelled(cancel_event)
            report(LoginProgress("starting"))
            _raise_if_login_cancelled(cancel_event)
            session = self._pty_factory(
                [invocation.executable, "auth", "login"],
                env=invocation.environment,
                cwd=invocation.cwd,
            )
            with session:
                report(LoginProgress("waiting_for_browser"))
                report(LoginProgress("waiting_for_user"))
                output = session.read_until(
                    _login_wait_complete,
                    self._login_timeout,
                    cancel_event,
                )
                if _contains_marker(output, _LOGIN_CANCEL_MARKERS):
                    raise ProviderError(
                        "login_cancelled", "Claude login was cancelled."
                    )
                if not _contains_marker(output, _LOGIN_SUCCESS_MARKERS):
                    raise ProviderError(
                        "provider_unavailable", "Claude login is unavailable."
                    )
            identity = self._read_status(invocation)
            report(LoginProgress("done"))
        except ProviderError as error:
            failure = self._normalize_error(error, operation="login")
        if failure is not None:
            raise failure
        if identity is None:
            raise _unsupported_version_error()
        return identity

    def refresh_usage(
        self,
        account: ManagedAccount,
        *,
        cancel_event: threading.Event | None = None,
    ) -> UsageSnapshot:
        _raise_if_operation_cancelled(cancel_event, operation="refresh")
        deadline = self._monotonic() + self._refresh_timeout
        snapshot: UsageSnapshot | None = None
        failure: ProviderError | None = None
        try:
            invocation = self._prepare_invocation(account)
            version_timeout = min(
                self._command_timeout,
                self._remaining(deadline),
            )
            provider_version = self._read_version(
                invocation,
                timeout=version_timeout,
            )
            self._remaining(deadline)
            observed_at = self._observed_at()
            self._remaining(deadline)
            session = self._pty_factory(
                [invocation.executable],
                env=invocation.environment,
                cwd=invocation.cwd,
            )
            with session:
                output = session.read_until(
                    _input_or_reauthentication_ready,
                    self._remaining(deadline),
                    cancel_event,
                )
                if _has_reauthentication_marker(output):
                    raise _reauthentication_error()
                session.write_line("/usage")
                output = session.read_until(
                    _usage_or_reauthentication_ready,
                    self._remaining(deadline),
                    cancel_event,
                )
                if _has_reauthentication_marker(output):
                    raise _reauthentication_error()
            try:
                terminal_bytes = output.encode("utf-8", errors="strict")
            except UnicodeError:
                raise _usage_error() from None
            snapshot = parse_claude_usage(
                account_id=account.id,
                provider_version=provider_version,
                terminal_bytes=terminal_bytes,
                observed_at=observed_at,
            )
        except ProviderError as error:
            failure = self._normalize_error(error, operation="refresh")
        if failure is not None:
            raise failure
        if snapshot is None:
            raise _usage_error()
        return snapshot

    def logout(
        self,
        account: ManagedAccount,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        _raise_if_operation_cancelled(cancel_event, operation="logout")
        failure: ProviderError | None = None
        try:
            invocation = self._prepare_invocation(account)
            _raise_if_operation_cancelled(cancel_event, operation="logout")
            self._checked_runner(
                [invocation.executable, "auth", "logout"],
                env=invocation.environment,
                cwd=invocation.cwd,
                timeout=self._command_timeout,
            )
            _raise_if_operation_cancelled(cancel_event, operation="logout")
        except ProviderError as error:
            failure = self._normalize_error(error, operation="logout")
        if failure is not None:
            raise failure

    def _prepare_invocation(self, account: ManagedAccount) -> _ClaudeInvocation:
        root, probe = self._ensure_account_directories(account)
        environment = provider_environment("claude", root)
        try:
            executable = self._executable_resolver(
                "claude", path=environment.get("PATH")
            )
        except ProviderError as error:
            if error.code == "executable_unavailable":
                raise ProviderError(
                    "cli_missing", "Claude CLI is not installed."
                ) from None
            raise
        return _ClaudeInvocation(executable, environment, probe)

    def _ensure_account_directories(
        self, account: ManagedAccount
    ) -> tuple[Path, Path]:
        if account.provider != "claude":
            raise _unsafe_account_path_error()
        try:
            root = self._paths.account_root("claude", account.id)
            home = self._paths.account_home("claude", account.id)
            probe = self._paths.account_probe("claude", account.id)
            temporary = self._paths.account_tmp("claude", account.id)
            ensure_private_dir(home, root=self._paths.root)
            ensure_private_dir(probe, root=self._paths.root)
            ensure_private_dir(temporary, root=self._paths.root)
            return root, probe
        except (OSError, UnsafePrivatePath, ValueError):
            raise _unsafe_account_path_error() from None

    def _read_version(
        self,
        invocation: _ClaudeInvocation,
        *,
        timeout: float | None = None,
    ) -> str:
        completed = self._checked_runner(
            [invocation.executable, "--version"],
            env=invocation.environment,
            cwd=invocation.cwd,
            timeout=self._command_timeout if timeout is None else timeout,
        )
        output = completed.stdout
        if not isinstance(output, str):
            raise _unsupported_version_error()
        match = _CLI_VERSION_OUTPUT.fullmatch(output)
        if match is None:
            raise _unsupported_version_error()
        version_text = match.group("version")
        try:
            version = _parse_numeric_version(version_text)
        except ProviderError:
            raise _unsupported_version_error() from None
        if version < _MINIMUM_CLI_VERSION:
            raise _unsupported_version_error()
        return version_text

    def _read_status(self, invocation: _ClaudeInvocation) -> ProviderIdentity:
        try:
            completed = self._checked_runner(
                [invocation.executable, "auth", "status"],
                env=invocation.environment,
                cwd=invocation.cwd,
                timeout=self._command_timeout,
            )
        except ProviderError as error:
            if error.code == "process_failed":
                raise _reauthentication_error() from None
            raise
        output = completed.stdout
        if not isinstance(output, str):
            raise _unsupported_version_error()
        try:
            encoded_size = len(output.encode("utf-8", errors="strict"))
            if encoded_size > _MAX_STATUS_BYTES:
                raise ValueError("oversized status")
            value = json.loads(output)
            if type(value) is not dict or type(value.get("loggedIn")) is not bool:
                raise ValueError("invalid status")
            if not value["loggedIn"]:
                raise _reauthentication_error()
            display_name = _identity_field(value, "displayName")
            email = _identity_field(value, "email")
            plan_key = "subscriptionType" if "subscriptionType" in value else "plan"
            plan = _identity_field(value, plan_key)
            return ProviderIdentity(display_name, email, plan)
        except ProviderError:
            raise
        except (TypeError, UnicodeError, ValueError):
            raise _unsupported_version_error() from None

    def _observed_at(self) -> str:
        observed = self._clock()
        if not isinstance(observed, datetime) or observed.tzinfo is None:
            raise ProviderError(
                "provider_unavailable", "Claude usage refresh is unavailable."
            )
        return (
            observed.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _normalize_error(error: ProviderError, *, operation: str) -> ProviderError:
        if error.code in {"cli_missing", "executable_unavailable"}:
            return ProviderError("cli_missing", "Claude CLI is not installed.")
        if error.code == "unsafe_account_path":
            return _unsafe_account_path_error()
        if error.code in {"refresh_cancelled", "logout_cancelled"}:
            return ProviderError(
                error.code,
                f"Claude {operation} was cancelled.",
            )
        if operation == "logout":
            return ProviderError("logout_failed", "Claude logout failed.")
        if error.code == "reauth_required":
            return _reauthentication_error()
        if error.code == "unsupported_cli_version":
            return _unsupported_version_error()
        if error.code == "unsupported_usage_layout":
            return _usage_error()
        if error.code == "login_cancelled":
            return ProviderError("login_cancelled", "Claude login was cancelled.")
        if error.code == "refresh_timeout":
            return ProviderError(
                "refresh_timeout", "Claude usage refresh timed out."
            )
        if error.code == "pty_cancelled" and operation == "login":
            return ProviderError("login_cancelled", "Claude login was cancelled.")
        if error.code == "pty_cancelled" and operation == "refresh":
            return ProviderError(
                "refresh_cancelled", "Claude refresh was cancelled."
            )
        if error.code == "pty_timeout" and operation == "refresh":
            return ProviderError(
                "refresh_timeout", "Claude usage refresh timed out."
            )
        if error.code == "process_timeout" and operation == "refresh":
            return ProviderError(
                "refresh_timeout", "Claude usage refresh timed out."
            )
        return ProviderError(
            "provider_unavailable",
            f"Claude {operation} is unavailable.",
        )

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise ProviderError(
                "refresh_timeout", "Claude usage refresh timed out."
            )
        return remaining


def _identity_field(value: dict[str, Any], key: str) -> str | None:
    field = value.get(key)
    if field is None:
        return None
    if not isinstance(field, str) or not field.strip() or len(field) > 256:
        raise ValueError("invalid identity")
    for character in field:
        if (
            unicodedata.category(character) in {"Cc", "Cs"}
            or unicodedata.bidirectional(character)
            in {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
        ):
            raise ValueError("invalid identity")
    return field


def _contains_marker(output: str, markers: tuple[str, ...]) -> bool:
    if not isinstance(output, str):
        return False
    normalized = output.casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _raise_if_login_cancelled(
    cancel_event: threading.Event | None,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProviderError("login_cancelled", "Claude login was cancelled.")


def _raise_if_operation_cancelled(
    cancel_event: threading.Event | None,
    *,
    operation: str,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProviderError(
            f"{operation}_cancelled",
            f"Claude {operation} was cancelled.",
        )


def _login_wait_complete(output: str) -> bool:
    return _contains_marker(
        output, _LOGIN_SUCCESS_MARKERS + _LOGIN_CANCEL_MARKERS
    )


def _has_reauthentication_marker(output: str) -> bool:
    return _contains_marker(output, _REAUTH_MARKERS)


def _input_or_reauthentication_ready(output: str) -> bool:
    return _has_reauthentication_marker(output) or _contains_marker(
        output, _INPUT_READY_MARKERS
    )


def _usage_or_reauthentication_ready(output: str) -> bool:
    if _has_reauthentication_marker(output):
        return True
    try:
        screen = TerminalScreen(width=160, height=60)
        screen.feed(output)
        text = screen.text()
    except ProviderError:
        return False
    has_footer = _contains_marker(text, _USAGE_FOOTER_MARKERS)
    has_header = _contains_marker(text, _USAGE_HEADER_MARKERS)
    return has_footer and has_header and "%" in text
