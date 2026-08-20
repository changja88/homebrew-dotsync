import json
import subprocess
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.providers.base import LoginProgress, ProviderError
from dotsync.providers.claude import (
    ClaudeUsageProvider,
    TerminalScreen,
    parse_claude_usage,
)


ACCOUNT_ID = "00000000-0000-4000-8000-000000000005"
OBSERVED_AT = "2026-08-21T12:00:00Z"
MAX_TERMINAL_BYTES = 2 * 1024 * 1024


class DecodeGuard(bytes):
    def __new__(cls, value, calls):
        instance = super().__new__(cls, value)
        instance.calls = calls
        return instance

    def decode(self, *args, **kwargs):
        self.calls.append("decode")
        raise AssertionError("oversized terminal bytes must not be decoded")


class FakeMonotonic:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeCheckedRunner:
    def __init__(self):
        self.version_output = "2.1.215 (Claude Code)\n"
        self.status_output = json.dumps(
            {
                "loggedIn": True,
                "displayName": None,
                "email": None,
                "subscriptionType": "max",
            }
        )
        self.calls = []
        self.failures = {}
        self.after_call = None

    def __call__(self, argv, *, env, cwd, timeout):
        arguments = tuple(str(value) for value in argv[1:])
        self.calls.append(
            {
                "argv": tuple(str(value) for value in argv),
                "env": dict(env),
                "cwd": cwd,
                "timeout": timeout,
            }
        )
        if self.after_call is not None:
            self.after_call(arguments)
        failure = self.failures.get(arguments)
        if failure is not None:
            raise failure
        stdout = {
            ("--version",): self.version_output,
            ("auth", "status"): self.status_output,
            ("auth", "logout"): "",
        }[arguments]
        return subprocess.CompletedProcess(argv, 0, stdout, "")


class FakeExecutableResolver:
    def __init__(self):
        self.calls = []
        self.error = None
        self.after_call = None

    def __call__(self, command, *, path=None):
        self.calls.append((command, path))
        if self.after_call is not None:
            self.after_call()
        if self.error is not None:
            raise self.error
        return Path("/fixture/bin/claude")


class FakePtySession:
    def __init__(self, argv, *, env, cwd, reads):
        self.argv = tuple(str(value) for value in argv)
        self.env = dict(env)
        self.cwd = cwd
        self.reads = list(reads)
        self.read_timeouts = []
        self.lines = []
        self.entered = False
        self.terminated = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc_info):
        self.terminate()

    def read_until(self, predicate, timeout, cancel_event=None):
        self.read_timeouts.append(timeout)
        value = self.reads.pop(0)
        if isinstance(value, Exception):
            raise value
        assert predicate(value), "fake PTY output did not reach a requested marker"
        return value

    def write_line(self, value):
        self.lines.append(value)

    def terminate(self):
        self.terminated = True


class FakePtyFactory:
    def __init__(self):
        self.scenarios = []
        self.instances = []

    def queue(self, *reads):
        self.scenarios.append(reads)

    def __call__(self, argv, *, env, cwd):
        session = FakePtySession(
            argv,
            env=env,
            cwd=cwd,
            reads=self.scenarios.pop(0),
        )
        self.instances.append(session)
        return session


@pytest.fixture
def paths(tmp_path):
    return AppPaths(tmp_path / "DotSync")


@pytest.fixture
def account():
    return ManagedAccount(
        id=str(uuid4()),
        provider="claude",
        label="Personal",
        state="logged_out",
        identity=ProviderIdentity(display_name=None, email=None, plan=None),
        created_at="2026-08-21T00:00:00Z",
    )


@pytest.fixture
def runner():
    return FakeCheckedRunner()


@pytest.fixture
def resolver():
    return FakeExecutableResolver()


@pytest.fixture
def pty_factory():
    return FakePtyFactory()


@pytest.fixture
def provider(paths, runner, resolver, pty_factory):
    return ClaudeUsageProvider(
        paths,
        checked_runner=runner,
        executable_resolver=resolver,
        pty_factory=pty_factory,
        clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
    )


def fixture_bytes(name: str) -> bytes:
    return (Path(__file__).with_name("fixtures") / name).read_bytes()


def bytes_at_terminal_limit(unit: bytes) -> bytes:
    fixture = fixture_bytes("claude_usage_v2_1_215.ansi")
    filler_size = MAX_TERMINAL_BYTES - len(fixture)
    repetitions, remainder = divmod(filler_size, len(unit))
    return unit * repetitions + b"x" * remainder + fixture


def assert_safe_error_drops_raw_value(error: ProviderError, raw_value) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__context__ is None
    assert raw_value not in error.args
    assert all(value is not raw_value for value in vars(error).values())
    assert "LEAK_INVALID_UTF8_MARKER" not in rendered


def test_terminal_screen_applies_cursor_motion_and_erases_lines():
    screen = TerminalScreen(width=100, height=40)

    screen.feed("5-hour 10%\r\x1b[2K5-hour 42%")

    assert "5-hour 42%" in screen.text()
    assert "5-hour 10%" not in screen.text()


def test_terminal_screen_reconstructs_supported_cursor_and_erase_operations():
    screen = TerminalScreen(width=20, height=5)

    screen.feed(
        "wrong\r\x1b[2Kright\r\n"
        "xx\bY\x1b[31m!\x1b[0m\r\n"
        "third\x1b[1A\r\x1b[4C?\x1b[3;1Hlast\x1b[0J"
    )

    assert screen.text() == "right\nxY! ?\nlast"


def test_terminal_screen_has_bounded_memory():
    screen = TerminalScreen(width=100, height=40)

    with pytest.raises(ProviderError, match="terminal output") as error:
        screen.feed("x" * (2 * 1024 * 1024 + 1))

    assert error.value.code == "terminal_output_limit"


def test_terminal_screen_invalid_encoding_drops_buffer_bearing_exception_state():
    raw_output = "LEAK_INVALID_UTF8_MARKER\ud800"
    screen = TerminalScreen(width=100, height=40)

    with pytest.raises(ProviderError) as captured:
        screen.feed(raw_output)

    assert captured.value.code == "terminal_output_invalid"
    assert_safe_error_drops_raw_value(captured.value, raw_output)


def test_parse_invalid_utf8_drops_buffer_bearing_exception_state():
    terminal_bytes = b"LEAK_INVALID_UTF8_MARKER\xff"

    with pytest.raises(ProviderError) as captured:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version="2.1.215",
            terminal_bytes=terminal_bytes,
            observed_at=OBSERVED_AT,
        )

    assert captured.value.code == "unsupported_usage_layout"
    assert_safe_error_drops_raw_value(captured.value, terminal_bytes)


@pytest.mark.parametrize(
    "unit",
    [
        pytest.param(b"x", id="ascii"),
        pytest.param("한".encode("utf-8"), id="multibyte"),
    ],
)
def test_parse_accepts_exact_terminal_byte_limit(unit):
    terminal_bytes = bytes_at_terminal_limit(unit)
    assert len(terminal_bytes) == MAX_TERMINAL_BYTES

    snapshot = parse_claude_usage(
        account_id=ACCOUNT_ID,
        provider_version="2.1.215",
        terminal_bytes=terminal_bytes,
        observed_at=OBSERVED_AT,
    )

    assert snapshot.windows[0].used_percent == 37.0


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"x" * (MAX_TERMINAL_BYTES + 1), id="ascii"),
        pytest.param(
            "한".encode("utf-8") * ((MAX_TERMINAL_BYTES // 3) + 1),
            id="multibyte",
        ),
    ],
)
def test_parse_rejects_oversized_terminal_bytes_before_decode(payload):
    calls = []
    guarded = DecodeGuard(payload, calls)
    assert len(guarded) > MAX_TERMINAL_BYTES

    with pytest.raises(ProviderError) as captured:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version="2.1.215",
            terminal_bytes=guarded,
            observed_at=OBSERVED_AT,
        )

    assert captured.value.code == "unsupported_usage_layout"
    assert calls == []


@pytest.mark.parametrize(
    "sequence",
    [
        pytest.param("\x1b[1;999A", id="cursor-motion-extra-parameter"),
        pytest.param("\x1b[3J", id="unsupported-erase-display-mode"),
        pytest.param("\x1b[1;2;3H", id="cursor-position-extra-parameter"),
        pytest.param("\x1b[2;1K", id="erase-line-extra-parameter"),
        pytest.param("\x1b[31;1m", id="unsupported-sgr-shape"),
    ],
)
def test_terminal_screen_rejects_unsupported_csi_parameter_shapes(sequence):
    screen = TerminalScreen(width=100, height=40)

    with pytest.raises(ProviderError) as captured:
        screen.feed(sequence)

    assert captured.value.code == "terminal_output_invalid"


def test_parse_current_claude_usage_layout():
    snapshot = parse_claude_usage(
        account_id=ACCOUNT_ID,
        provider_version="2.1.215",
        terminal_bytes=fixture_bytes("claude_usage_v2_1_215.ansi"),
        observed_at=OBSERVED_AT,
    )

    assert [(window.name, window.used_percent) for window in snapshot.windows] == [
        ("five_hour", 37.0),
        ("seven_day", 64.0),
    ]
    assert [window.resets_at for window in snapshot.windows] == [
        "2026-08-21T14:15:00Z",
        "2026-08-26T00:00:00Z",
    ]


def test_parse_current_layout_supports_korean_window_semantics():
    terminal_bytes = (
        "사용량\r\n"
        "5시간 한도\r\n"
        "37% 사용\r\n"
        "2시간 15분 후 재설정\r\n"
        "7일 한도\r\n"
        "64% 사용\r\n"
        "4일 12시간 후 재설정\r\n"
        "닫으려면 Esc\r\n"
    ).encode()

    snapshot = parse_claude_usage(
        account_id=ACCOUNT_ID,
        provider_version="2.1.215",
        terminal_bytes=terminal_bytes,
        observed_at=OBSERVED_AT,
    )

    assert [(window.name, window.used_percent) for window in snapshot.windows] == [
        ("five_hour", 37.0),
        ("seven_day", 64.0),
    ]


def test_missing_optional_window_stays_absent():
    snapshot = parse_claude_usage(
        account_id=ACCOUNT_ID,
        provider_version="2.1.215",
        terminal_bytes=fixture_bytes("claude_usage_missing_window.ansi"),
        observed_at=OBSERVED_AT,
    )

    assert [window.name for window in snapshot.windows] == ["five_hour"]


@pytest.mark.parametrize(
    ("provider_version", "terminal_bytes"),
    [
        pytest.param(
            "9.9.9",
            fixture_bytes("claude_usage_unknown.ansi"),
            id="unknown-version-and-layout",
        ),
        pytest.param(
            "2.1.215-beta",
            fixture_bytes("claude_usage_v2_1_215.ansi"),
            id="ambiguous-version-suffix",
        ),
        pytest.param(
            "2.1.9999999",
            fixture_bytes("claude_usage_v2_1_215.ansi"),
            id="huge-version-component",
        ),
        pytest.param(
            "2.1.215",
            b"5-hour limit\r\n0% used\r\n",
            id="partial-primary-window",
        ),
        pytest.param(
            "2.1.215",
            b"5-hour limit\r\n101% used\r\nResets in 2 hours\r\n",
            id="invalid-percentage",
        ),
    ],
)
def test_unknown_or_partial_layout_never_reports_zero(
    provider_version, terminal_bytes
):
    with pytest.raises(ProviderError) as error:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version=provider_version,
            terminal_bytes=terminal_bytes,
            observed_at=OBSERVED_AT,
        )

    assert error.value.code == "unsupported_usage_layout"
    assert terminal_bytes.decode("utf-8", errors="ignore") not in str(error.value)


def test_present_but_partial_optional_window_rejects_the_layout():
    terminal_bytes = (
        b"5-hour limit\r\n37% used\r\nResets in 2 hours\r\n"
        b"7-day limit\r\n64% used\r\n"
    )

    with pytest.raises(ProviderError) as error:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version="2.1.215",
            terminal_bytes=terminal_bytes,
            observed_at=OBSERVED_AT,
        )

    assert error.value.code == "unsupported_usage_layout"


@pytest.mark.parametrize(
    "usage_rows",
    [
        pytest.param(
            "5-hour limit\r\n63% remaining\r\nResets in 2 hours\r\n",
            id="remaining-not-used",
        ),
        pytest.param(
            "5-hour limit\r\nSave 63% today\r\nResets in 2 hours\r\n",
            id="unrelated-discount",
        ),
        pytest.param(
            "5-hour limit\r\nPlan note\r\n63% used\r\nResets in 2 hours\r\n",
            id="non-adjacent-usage-row",
        ),
        pytest.param(
            "5-hour limit\r\nUsed: 63%\r\nResets in 2 hours\r\n",
            id="changed-english-semantics",
        ),
        pytest.param(
            "5시간 한도\r\n63% 남음\r\n2시간 후 재설정\r\n",
            id="changed-korean-semantics",
        ),
    ],
)
def test_parser_rejects_non_usage_or_non_adjacent_percentages(usage_rows):
    with pytest.raises(ProviderError) as captured:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version="2.1.215",
            terminal_bytes=usage_rows.encode("utf-8"),
            observed_at=OBSERVED_AT,
        )

    assert captured.value.code == "unsupported_usage_layout"


@pytest.mark.parametrize(
    "terminal_text",
    [
        pytest.param(
            "5-hour limit\r\n"
            "37% used\r\n"
            "Resets in 2 hours\r\n"
            "Resets in 1 hour\r\n",
            id="english",
        ),
        pytest.param(
            "5시간 한도\r\n"
            "37% 사용\r\n"
            "2시간 후 재설정\r\n"
            "1시간 후 초기화\r\n",
            id="korean",
        ),
    ],
)
def test_parser_rejects_duplicate_recognized_reset_rows(terminal_text):
    with pytest.raises(ProviderError) as captured:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version="2.1.215",
            terminal_bytes=terminal_text.encode("utf-8"),
            observed_at=OBSERVED_AT,
        )

    assert captured.value.code == "unsupported_usage_layout"


def test_parser_allows_unrelated_footer_after_single_reset_row():
    terminal_bytes = (
        b"5-hour limit\r\n"
        b"37% used\r\n"
        b"Resets in 2 hours\r\n"
        b"Press Esc to go back\r\n"
    )

    snapshot = parse_claude_usage(
        account_id=ACCOUNT_ID,
        provider_version="2.1.215",
        terminal_bytes=terminal_bytes,
        observed_at=OBSERVED_AT,
    )

    assert [(window.name, window.used_percent) for window in snapshot.windows] == [
        ("five_hour", 37.0)
    ]


def test_truncated_terminal_escape_rejects_an_otherwise_valid_layout():
    terminal_bytes = fixture_bytes("claude_usage_v2_1_215.ansi") + b"\x1b["

    with pytest.raises(ProviderError) as error:
        parse_claude_usage(
            account_id=ACCOUNT_ID,
            provider_version="2.1.215",
            terminal_bytes=terminal_bytes,
            observed_at=OBSERVED_AT,
        )

    assert error.value.code == "unsupported_usage_layout"


@pytest.mark.parametrize(
    "version_output",
    [
        pytest.param("2.1.214 (Claude Code)", id="below-floor"),
        pytest.param("2.1.215-beta (Claude Code)", id="ambiguous-suffix"),
        pytest.param("2.1.9999999 (Claude Code)", id="huge-component"),
        pytest.param("2.1.215 2.1.216", id="multiple-versions"),
    ],
)
def test_login_rejects_unsupported_version_before_pty_creation(
    provider, account, runner, resolver, pty_factory, version_output
):
    runner.version_output = version_output

    with pytest.raises(ProviderError) as error:
        provider.login(account, lambda progress: None)

    assert error.value.code == "unsupported_cli_version"
    assert pty_factory.instances == []
    assert len(resolver.calls) == 1
    assert [call["argv"][1:] for call in runner.calls] == [("--version",)]


def test_login_uses_official_scoped_cli_and_reads_safe_status_identity(
    provider, account, paths, runner, resolver, pty_factory
):
    pty_factory.queue("Authentication successful")
    progress = []

    identity = provider.login(account, progress.append)

    assert identity == ProviderIdentity(
        display_name=None,
        email=None,
        plan="max",
    )
    assert progress == [
        LoginProgress("starting"),
        LoginProgress("waiting_for_browser"),
        LoginProgress("waiting_for_user"),
        LoginProgress("done"),
    ]
    assert len(resolver.calls) == 1
    assert [call["argv"][1:] for call in runner.calls] == [
        ("--version",),
        ("auth", "status"),
    ]
    session = pty_factory.instances[0]
    account_root = paths.account_root("claude", account.id)
    assert session.argv[1:] == ("auth", "login")
    assert session.cwd == paths.account_probe("claude", account.id)
    assert session.env["CLAUDE_CONFIG_DIR"] == str(account_root / "home")
    assert session.env["CLAUDE_CODE_TMPDIR"] == str(account_root / "tmp")
    assert session.env["HOME"] == str(account_root / "home")
    assert session.env["TMPDIR"] == str(account_root / "tmp")
    assert session.terminated


def test_login_cancellation_during_pty_is_safe_and_terminates_pty(
    provider, account, pty_factory
):
    unsafe_output = "LEAK_EMAIL_MARKER LEAK_CALLBACK_MARKER LEAK_TOKEN_MARKER"
    pty_factory.queue(ProviderError("pty_cancelled", unsafe_output))
    cancel_event = threading.Event()

    with pytest.raises(ProviderError) as error:
        provider.login(account, lambda progress: None, cancel_event=cancel_event)

    assert error.value.code == "login_cancelled"
    assert unsafe_output not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert pty_factory.instances[0].terminated


def test_pre_cancelled_login_runs_no_resolver_version_or_pty(
    provider, account, resolver, runner, pty_factory
):
    cancel_event = threading.Event()
    cancel_event.set()
    pty_factory.queue(ProviderError("pty_cancelled", "must-not-launch"))

    with pytest.raises(ProviderError) as captured:
        provider.login(
            account,
            lambda progress: None,
            cancel_event=cancel_event,
        )

    assert captured.value.code == "login_cancelled"
    assert resolver.calls == []
    assert runner.calls == []
    assert pty_factory.instances == []


def test_login_cancelled_after_version_does_not_construct_pty(
    provider, account, runner, pty_factory
):
    cancel_event = threading.Event()
    runner.after_call = lambda arguments: cancel_event.set()
    pty_factory.queue(ProviderError("pty_cancelled", "must-not-launch"))

    with pytest.raises(ProviderError) as captured:
        provider.login(
            account,
            lambda progress: None,
            cancel_event=cancel_event,
        )

    assert captured.value.code == "login_cancelled"
    assert [call["argv"][1:] for call in runner.calls] == [("--version",)]
    assert pty_factory.instances == []


def test_login_cancelled_by_start_progress_does_not_construct_pty(
    provider, account, pty_factory
):
    cancel_event = threading.Event()
    pty_factory.queue(ProviderError("pty_cancelled", "must-not-launch"))

    def report(progress):
        if progress.state == "starting":
            cancel_event.set()

    with pytest.raises(ProviderError) as captured:
        provider.login(account, report, cancel_event=cancel_event)

    assert captured.value.code == "login_cancelled"
    assert pty_factory.instances == []


def test_login_status_exit_one_maps_to_reauthentication(
    provider, account, runner, pty_factory
):
    pty_factory.queue("Login successful")
    runner.failures[("auth", "status")] = ProviderError(
        "process_failed", "LEAK_STATUS_MARKER"
    )

    with pytest.raises(ProviderError) as error:
        provider.login(account, lambda progress: None)

    assert error.value.code == "reauth_required"
    assert "LEAK_STATUS_MARKER" not in str(error.value)


def test_refresh_uses_official_interactive_usage_and_45_second_deadline(
    provider, account, paths, runner, resolver, pty_factory
):
    pty_factory.queue(
        "Claude Code\r\n❯",
        fixture_bytes("claude_usage_v2_1_215.ansi").decode("utf-8"),
    )

    snapshot = provider.refresh_usage(account)

    assert [(window.name, window.used_percent) for window in snapshot.windows] == [
        ("five_hour", 37.0),
        ("seven_day", 64.0),
    ]
    assert snapshot.provider_version == "2.1.215"
    assert len(resolver.calls) == 1
    assert [call["argv"][1:] for call in runner.calls] == [("--version",)]
    session = pty_factory.instances[0]
    assert session.argv[1:] == ()
    assert session.cwd == paths.account_probe("claude", account.id)
    assert session.lines == ["/usage"]
    assert len(session.read_timeouts) == 2
    assert all(0 < timeout <= 45.0 for timeout in session.read_timeouts)
    assert session.terminated


def test_refresh_version_time_is_deducted_from_both_pty_waits(
    paths, account, runner, resolver, pty_factory
):
    monotonic = FakeMonotonic()
    runner.after_call = lambda arguments: monotonic.advance(12.0)
    pty_factory.queue(
        "Claude Code\r\n❯",
        fixture_bytes("claude_usage_v2_1_215.ansi").decode("utf-8"),
    )
    provider = ClaudeUsageProvider(
        paths,
        checked_runner=runner,
        executable_resolver=resolver,
        pty_factory=pty_factory,
        clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        monotonic=monotonic,
    )

    provider.refresh_usage(account)

    assert runner.calls[0]["timeout"] == 15.0
    assert pty_factory.instances[0].read_timeouts == [33.0, 33.0]


def test_refresh_expired_preparation_budget_maps_timeout_before_version_or_pty(
    paths, account, runner, resolver, pty_factory
):
    monotonic = FakeMonotonic()
    resolver.after_call = lambda: monotonic.advance(46.0)
    pty_factory.queue("must-not-launch")
    provider = ClaudeUsageProvider(
        paths,
        checked_runner=runner,
        executable_resolver=resolver,
        pty_factory=pty_factory,
        clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        monotonic=monotonic,
    )

    with pytest.raises(ProviderError) as captured:
        provider.refresh_usage(account)

    assert captured.value.code == "refresh_timeout"
    assert len(resolver.calls) == 1
    assert runner.calls == []
    assert pty_factory.instances == []


def test_refresh_expired_version_budget_maps_timeout_without_pty(
    paths, account, runner, resolver, pty_factory
):
    monotonic = FakeMonotonic()
    runner.after_call = lambda arguments: monotonic.advance(46.0)
    pty_factory.queue("must-not-launch")
    provider = ClaudeUsageProvider(
        paths,
        checked_runner=runner,
        executable_resolver=resolver,
        pty_factory=pty_factory,
        clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        monotonic=monotonic,
    )

    with pytest.raises(ProviderError) as captured:
        provider.refresh_usage(account)

    assert captured.value.code == "refresh_timeout"
    assert runner.calls[0]["timeout"] == 15.0
    assert pty_factory.instances == []


def test_refresh_rejects_unsupported_version_before_pty_creation(
    provider, account, runner, resolver, pty_factory
):
    runner.version_output = "2.1.214 (Claude Code)"

    with pytest.raises(ProviderError) as error:
        provider.refresh_usage(account)

    assert error.value.code == "unsupported_cli_version"
    assert pty_factory.instances == []
    assert len(resolver.calls) == 1


def test_refresh_maps_expired_login_without_sending_usage(
    provider, account, pty_factory
):
    pty_factory.queue("Authentication required. Run /login")

    with pytest.raises(ProviderError) as error:
        provider.refresh_usage(account)

    assert error.value.code == "reauth_required"
    assert pty_factory.instances[0].lines == []
    assert pty_factory.instances[0].terminated


def test_refresh_unknown_completed_screen_maps_to_unsupported_layout(
    provider, account, pty_factory
):
    pty_factory.queue(
        "Claude Code\r\n❯",
        fixture_bytes("claude_usage_unknown.ansi").decode("utf-8"),
    )

    with pytest.raises(ProviderError) as error:
        provider.refresh_usage(account)

    assert error.value.code == "unsupported_usage_layout"
    assert pty_factory.instances[0].terminated


def test_refresh_timeout_has_safe_code_and_terminates_pty(
    provider, account, pty_factory
):
    unsafe_output = "LEAK_EMAIL_MARKER LEAK_CALLBACK_MARKER LEAK_TOKEN_MARKER"
    pty_factory.queue(ProviderError("pty_timeout", unsafe_output))

    with pytest.raises(ProviderError) as error:
        provider.refresh_usage(account)

    assert error.value.code == "refresh_timeout"
    assert unsafe_output not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert pty_factory.instances[0].terminated


def test_logout_uses_only_official_scoped_command(
    provider, account, paths, runner, resolver
):
    provider.logout(account)

    assert len(resolver.calls) == 1
    assert [call["argv"][1:] for call in runner.calls] == [
        ("auth", "logout"),
    ]
    call = runner.calls[0]
    account_root = paths.account_root("claude", account.id)
    assert call["cwd"] == paths.account_probe("claude", account.id)
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(account_root / "home")
    assert call["env"]["CLAUDE_CODE_TMPDIR"] == str(account_root / "tmp")


def test_logout_failure_is_normalized_without_raw_command_output(
    provider, account, runner
):
    unsafe_output = "LEAK_EMAIL_MARKER LEAK_CALLBACK_MARKER LEAK_TOKEN_MARKER"
    runner.failures[("auth", "logout")] = ProviderError(
        "provider_unavailable", unsafe_output
    )

    with pytest.raises(ProviderError) as error:
        provider.logout(account)

    assert error.value.code == "logout_failed"
    assert unsafe_output not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize("operation", ["login", "refresh", "logout"])
def test_missing_executable_maps_to_cli_missing(
    provider, account, resolver, pty_factory, runner, operation
):
    resolver.error = ProviderError(
        "executable_unavailable", "LEAK_EXECUTABLE_PATH_MARKER"
    )

    with pytest.raises(ProviderError) as error:
        if operation == "login":
            provider.login(account, lambda progress: None)
        elif operation == "refresh":
            provider.refresh_usage(account)
        else:
            provider.logout(account)

    assert error.value.code == "cli_missing"
    assert "LEAK_EXECUTABLE_PATH_MARKER" not in str(error.value)
    assert pty_factory.instances == []
    assert runner.calls == []


def test_all_provider_failures_keep_default_profile_and_keychain_sentinels_unchanged(
    provider,
    account,
    runner,
    pty_factory,
    tmp_path,
    monkeypatch,
    caplog,
):
    fake_home = tmp_path / "fake-home"
    sentinels = [
        fake_home / ".claude" / "settings.json",
        fake_home / ".claude.json",
        fake_home / "Library" / "Keychains" / "default-credential.bin",
    ]
    for index, sentinel in enumerate(sentinels):
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(f"sentinel-{index}".encode())
    before = {
        sentinel: (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
        for sentinel in sentinels
    }
    monkeypatch.setenv("HOME", str(fake_home))
    unsafe_output = "LEAK_EMAIL_MARKER LEAK_CALLBACK_MARKER LEAK_TOKEN_MARKER"

    pty_factory.queue(ProviderError("pty_cancelled", unsafe_output))
    with pytest.raises(ProviderError):
        provider.login(account, lambda progress: None)

    pty_factory.queue(
        "Claude Code\r\n❯",
        fixture_bytes("claude_usage_v2_1_215.ansi").decode("utf-8"),
    )
    provider.refresh_usage(account)
    provider.logout(account)

    with pytest.raises(ProviderError):
        parse_claude_usage(
            account_id=account.id,
            provider_version="2.1.215",
            terminal_bytes=unsafe_output.encode(),
            observed_at=OBSERVED_AT,
        )

    pty_factory.queue(ProviderError("pty_timeout", unsafe_output))
    with pytest.raises(ProviderError):
        provider.refresh_usage(account)

    after = {
        sentinel: (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
        for sentinel in sentinels
    }
    assert after == before
    captured = caplog.text
    assert unsafe_output not in captured
    for marker in unsafe_output.split():
        assert marker not in captured
