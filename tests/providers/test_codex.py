from __future__ import annotations

import json
import stat
import threading
import time
import tomllib
from datetime import datetime, timezone
from math import inf, nan
from pathlib import Path
from uuid import uuid4

import pytest

from dotsync import __version__
from dotsync.accounts import ManagedAccount, ProviderIdentity
from dotsync.app_paths import AppPaths
from dotsync.providers import ProviderError
from dotsync.providers.codex import CodexUsageProvider
from dotsync.usage import UsageSnapshot, UsageWindow


VALID_WINDOW = {
    "name": "five_hour",
    "limit_id": "codex",
    "label": None,
    "used_percent": 42.0,
    "duration_minutes": 300,
    "resets_at": "2026-08-21T12:00:00Z",
}


def test_usage_window_normalizes_valid_integer_percentage():
    window = UsageWindow(**{**VALID_WINDOW, "used_percent": 42})

    assert window.used_percent == 42.0
    assert type(window.used_percent) is float


@pytest.mark.parametrize("limit_id", ["", " ", "\t\n"])
def test_usage_window_rejects_blank_limit_id(limit_id):
    with pytest.raises(ValueError, match="limit id"):
        UsageWindow(**{**VALID_WINDOW, "limit_id": limit_id})


@pytest.mark.parametrize("duration", [True, 0, -1, 1.5])
def test_usage_window_rejects_invalid_duration(duration):
    with pytest.raises((TypeError, ValueError), match="duration"):
        UsageWindow(**{**VALID_WINDOW, "duration_minutes": duration})


@pytest.mark.parametrize(
    "percentage",
    [False, -0.1, 100.1, nan, inf, -inf, "42"],
)
def test_usage_window_rejects_invalid_percentage(percentage):
    with pytest.raises((TypeError, ValueError), match="percentage"):
        UsageWindow(**{**VALID_WINDOW, "used_percent": percentage})


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-21 12:00:00Z",
        "2026-08-21T12:00:00",
        "2026-13-21T12:00:00Z",
        "not-a-timestamp",
        1_777_000_000,
    ],
)
def test_usage_window_rejects_invalid_rfc3339_reset(timestamp):
    with pytest.raises((TypeError, ValueError), match="RFC 3339"):
        UsageWindow(**{**VALID_WINDOW, "resets_at": timestamp})


def test_usage_snapshot_validates_observation_time_and_contract_fields():
    window = UsageWindow(**VALID_WINDOW)

    with pytest.raises(ValueError, match="RFC 3339"):
        UsageSnapshot(
            account_id="account-id",
            provider="codex",
            windows=(window,),
            observed_at="2026-08-21 12:00:00",
            source="codex_app_server",
            provider_version="0.42.0",
        )


def test_usage_snapshot_accepts_valid_normalized_data():
    window = UsageWindow(**VALID_WINDOW)

    snapshot = UsageSnapshot(
        account_id="account-id",
        provider="codex",
        windows=(window,),
        observed_at="2026-08-21T12:00:00+09:00",
        source="codex_app_server",
        provider_version="0.42.0",
    )

    assert snapshot.windows == (window,)


class FakeRpc:
    def __init__(
        self,
        argv,
        *,
        env,
        cwd,
        timeout,
        on_notification=None,
        responses,
    ):
        self.argv = tuple(argv)
        self.environment = dict(env)
        self.cwd = cwd
        self.timeout = timeout
        self.on_notification = on_notification
        self.responses = responses
        self.events = []
        self.closed = False

    def __enter__(self):
        self.events.append(("enter", None))
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        self.events.append(("close", None))

    def request(self, method, params, **kwargs):
        self.events.append(("request", method, params, kwargs))
        if method == "initialize" and method not in self.responses:
            return {
                "userAgent": "codex_cli_rs/0.42.0 (DotSync fixture)",
                "codexHome": self.environment["CODEX_HOME"],
                "platformFamily": "unix",
                "platformOs": "macos",
            }
        response = self.responses[method]
        if callable(response):
            return response(self, params, kwargs)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, list):
            return response.pop(0)
        return response

    def notify(self, method, params):
        self.events.append(("notify", method, params))


class FakeRpcFactory:
    def __init__(self):
        self.responses = {}
        self.instances = []

    def respond(self, method, response):
        self.responses[method] = response

    def __call__(self, argv, **kwargs):
        rpc = FakeRpc(argv, **kwargs, responses=self.responses)
        self.instances.append(rpc)
        return rpc


@pytest.fixture
def paths(tmp_path):
    return AppPaths(tmp_path / "DotSync")


@pytest.fixture
def account():
    return ManagedAccount(
        id=str(uuid4()),
        provider="codex",
        label="Personal",
        state="logged_out",
        identity=ProviderIdentity(display_name=None, email=None, plan=None),
        created_at="2026-08-21T00:00:00Z",
    )


@pytest.fixture
def fake_rpc():
    return FakeRpcFactory()


@pytest.fixture
def provider(paths, fake_rpc):
    return CodexUsageProvider(
        paths,
        rpc_factory=fake_rpc,
        executable_resolver=lambda command, **kwargs: Path("/fixture/bin/codex"),
        clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
    )


def load_fixture(name):
    path = Path(__file__).with_name("fixtures") / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_prepare_profile_forces_top_level_file_credentials(
    provider, account, paths
):
    home = paths.account_home("codex", account.id)
    home.mkdir(parents=True)
    config = home / "config.toml"
    config.write_text(
        'model = "gpt-5"\n'
        'cli_auth_credentials_store = "keyring"\n'
        "[features]\n"
        "web_search = true\n"
        "[compatibility]\n"
        'cli_auth_credentials_store = "nested-value"\n',
        encoding="utf-8",
    )

    provider.prepare_profile(account)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-5"
    assert parsed["cli_auth_credentials_store"] == "file"
    assert parsed["features"] == {"web_search": True}
    assert parsed["compatibility"]["cli_auth_credentials_store"] == "nested-value"
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_prepare_profile_adds_top_level_key_when_only_nested_key_exists(
    provider, account, paths
):
    home = paths.account_home("codex", account.id)
    home.mkdir(parents=True)
    config = home / "config.toml"
    config.write_text(
        "[features]\n"
        'cli_auth_credentials_store = "same-name-but-nested"\n',
        encoding="utf-8",
    )

    provider.prepare_profile(account)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["cli_auth_credentials_store"] == "file"
    assert parsed["features"]["cli_auth_credentials_store"] == (
        "same-name-but-nested"
    )


def test_prepare_profile_populates_existing_empty_config(provider, account, paths):
    home = paths.account_home("codex", account.id)
    home.mkdir(parents=True)
    config = home / "config.toml"
    config.write_text("", encoding="utf-8")

    provider.prepare_profile(account)

    assert tomllib.loads(config.read_text(encoding="utf-8")) == {
        "cli_auth_credentials_store": "file"
    }
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_refresh_maps_official_rate_limit_windows(
    provider, account, paths, fake_rpc
):
    fake_rpc.respond(
        "account/rateLimits/read",
        load_fixture("codex_rate_limits.json"),
    )

    snapshot = provider.refresh_usage(account)

    assert [(window.name, window.used_percent) for window in snapshot.windows] == [
        ("five_hour", 42.0),
        ("seven_day", 61.0),
    ]
    assert [window.limit_id for window in snapshot.windows] == ["codex", "codex"]
    assert snapshot.windows[0].resets_at == "2026-08-21T13:00:00Z"
    assert snapshot.provider_version == "0.42.0"
    assert snapshot.observed_at == "2026-08-21T12:00:00Z"

    rpc = fake_rpc.instances[-1]
    assert rpc.argv == (Path("/fixture/bin/codex"), "app-server")
    assert rpc.environment["CODEX_HOME"] == str(
        paths.account_home("codex", account.id)
    )
    assert rpc.environment["HOME"] == str(paths.account_home("codex", account.id))
    assert rpc.cwd == paths.account_probe("codex", account.id)
    assert rpc.closed is True
    assert rpc.events[1:] == [
        (
            "request",
            "initialize",
            {
                "clientInfo": {
                    "name": "dotsync",
                    "title": "DotSync",
                    "version": __version__,
                }
            },
            {},
        ),
        ("notify", "initialized", {}),
        ("request", "account/rateLimits/read", {}, {}),
        ("close", None),
    ]


def test_login_uses_official_browser_flow_and_maps_non_secret_identity(
    provider, account, fake_rpc
):
    login_id = "login-fixture-id"
    callback_elapsed = []

    def start_login(rpc, params, kwargs):
        started = time.monotonic()
        rpc.on_notification(
            "account/login/completed",
            {"loginId": "different-login", "success": True, "error": None},
        )
        rpc.on_notification(
            "account/login/completed",
            {"loginId": login_id, "success": True, "error": None},
        )
        callback_elapsed.append(time.monotonic() - started)
        return {
            "type": "chatgpt",
            "loginId": login_id,
            "authUrl": "https://auth.openai.invalid/login",
        }

    fake_rpc.respond("account/login/start", start_login)
    fake_rpc.respond(
        "account/read",
        {
            "account": {
                "type": "chatgpt",
                "email": "person@example.invalid",
                "planType": "plus",
            },
            "requiresOpenaiAuth": True,
        },
    )
    progress = []

    identity = provider.login(account, progress.append)

    assert identity == ProviderIdentity(
        display_name=None,
        email="person@example.invalid",
        plan="plus",
    )
    assert [(item.state, item.verification_url) for item in progress] == [
        ("starting", None),
        ("waiting_for_browser", "https://auth.openai.invalid/login"),
        ("waiting_for_user", None),
        ("done", None),
    ]
    assert callback_elapsed[0] < 0.05
    rpc = fake_rpc.instances[-1]
    assert rpc.closed is True
    assert [event[1] for event in rpc.events if event[0] == "request"] == [
        "initialize",
        "account/login/start",
        "account/read",
    ]
    assert next(
        event[2]
        for event in rpc.events
        if event[0] == "request" and event[1] == "account/login/start"
    ) == {
        "type": "chatgpt",
        "useHostedLoginSuccessPage": True,
        "appBrand": "codex",
    }
    assert next(
        event[2]
        for event in rpc.events
        if event[0] == "request" and event[1] == "account/read"
    ) == {"refreshToken": False}


def test_login_cancellation_uses_official_cancel_and_closes_server(
    provider, account, fake_rpc
):
    cancel_event = threading.Event()
    login_id = "cancelled-login-id"

    def start_login(rpc, params, kwargs):
        cancel_event.set()
        return {
            "type": "chatgpt",
            "loginId": login_id,
            "authUrl": "https://auth.openai.invalid/login",
        }

    fake_rpc.respond("account/login/start", start_login)
    fake_rpc.respond("account/login/cancel", {"status": "canceled"})

    with pytest.raises(ProviderError) as captured:
        provider.login(account, lambda progress: None, cancel_event=cancel_event)

    assert captured.value.code == "login_cancelled"
    assert captured.value.safe_message == "Codex login was cancelled."
    rpc = fake_rpc.instances[-1]
    assert (
        "request",
        "account/login/cancel",
        {"loginId": login_id},
        {},
    ) in rpc.events
    assert rpc.closed is True


def test_login_rejects_null_chatgpt_plan(provider, account, fake_rpc):
    login_id = "login-without-plan"

    def start_login(rpc, params, kwargs):
        rpc.on_notification(
            "account/login/completed",
            {"loginId": login_id, "success": True, "error": None},
        )
        return {
            "type": "chatgpt",
            "loginId": login_id,
            "authUrl": "https://auth.openai.invalid/login",
        }

    fake_rpc.respond("account/login/start", start_login)
    fake_rpc.respond(
        "account/read",
        {
            "account": {
                "type": "chatgpt",
                "email": None,
                "planType": None,
            },
            "requiresOpenaiAuth": True,
        },
    )

    with pytest.raises(ProviderError) as captured:
        provider.login(account, lambda progress: None)

    assert captured.value.code == "unsupported_cli_version"
    assert fake_rpc.instances[-1].closed is True


def test_logout_uses_official_rpc_and_closes_server(provider, account, fake_rpc):
    fake_rpc.respond("account/logout", {})

    provider.logout(account)

    rpc = fake_rpc.instances[-1]
    assert [event[1] for event in rpc.events if event[0] == "request"] == [
        "initialize",
        "account/logout",
    ]
    assert rpc.closed is True


def test_refresh_prefers_current_bucket_map_and_orders_limit_ids(
    provider, account, fake_rpc
):
    fake_rpc.respond(
        "account/rateLimits/read",
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 99,
                    "windowDurationMins": 300,
                    "resetsAt": None,
                }
            },
            "rateLimitsByLimitId": {
                "zeta": {
                    "limitId": "zeta",
                    "limitName": "Zeta limit",
                    "primary": {
                        "usedPercent": 12.5,
                        "windowDurationMins": 60,
                        "resetsAt": None,
                    },
                    "secondary": None,
                },
                "codex": {
                    "limitId": "codex",
                    "limitName": None,
                    "primary": {
                        "usedPercent": 42,
                        "windowDurationMins": 300,
                        "resetsAt": None,
                    },
                    "secondary": {
                        "usedPercent": 61,
                        "windowDurationMins": 10080,
                        "resetsAt": None,
                    },
                },
            },
        },
    )

    snapshot = provider.refresh_usage(account)

    assert [
        (window.limit_id, window.label, window.name, window.duration_minutes)
        for window in snapshot.windows
    ] == [
        ("codex", None, "five_hour", 300),
        ("codex", None, "seven_day", 10080),
        ("zeta", "Zeta limit", "other", 60),
    ]


def test_refresh_uses_backward_compatible_single_bucket(provider, account, fake_rpc):
    fake_rpc.respond(
        "account/rateLimits/read",
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 7,
                    "windowDurationMins": 300,
                    "resetsAt": None,
                },
                "secondary": None,
            }
        },
    )

    snapshot = provider.refresh_usage(account)

    assert [
        (window.limit_id, window.name, window.used_percent)
        for window in snapshot.windows
    ] == [("codex", "five_hour", 7.0)]


def test_refresh_uses_codex_id_for_legacy_null_limit_id(
    provider, account, fake_rpc
):
    fake_rpc.respond(
        "account/rateLimits/read",
        {
            "rateLimits": {
                "limitId": None,
                "primary": {
                    "usedPercent": 7,
                    "windowDurationMins": 300,
                    "resetsAt": None,
                },
                "secondary": None,
            },
            "rateLimitsByLimitId": None,
        },
    )

    snapshot = provider.refresh_usage(account)

    assert snapshot.windows[0].limit_id == "codex"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("usedPercent", True),
        ("usedPercent", nan),
        ("usedPercent", inf),
        ("usedPercent", -1),
        ("usedPercent", 101),
        ("windowDurationMins", True),
        ("windowDurationMins", 0),
        ("resetsAt", True),
        ("resetsAt", -1),
    ],
)
def test_refresh_rejects_invalid_official_window_values(
    provider, account, fake_rpc, field, value
):
    window = {
        "usedPercent": 42,
        "windowDurationMins": 300,
        "resetsAt": 1787317200,
    }
    window[field] = value
    fake_rpc.respond(
        "account/rateLimits/read",
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": window,
                "secondary": None,
            }
        },
    )

    with pytest.raises(ProviderError) as captured:
        provider.refresh_usage(account)

    assert captured.value.code == "unsupported_cli_version"


def test_malformed_response_error_never_contains_token_like_data(
    provider, account, fake_rpc
):
    secret = "sk-sentinel-token-must-not-leak"
    fake_rpc.respond(
        "account/rateLimits/read",
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": secret,
                    "windowDurationMins": 300,
                    "resetsAt": None,
                },
                "secondary": None,
            }
        },
    )

    with pytest.raises(ProviderError) as captured:
        provider.refresh_usage(account)

    assert captured.value.code == "unsupported_cli_version"
    assert secret not in captured.value.safe_message
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_every_invocation_ignores_inherited_default_codex_home(
    monkeypatch, provider, account, fake_rpc
):
    sentinel = "/Users/test/.codex"
    monkeypatch.setenv("CODEX_HOME", sentinel)
    fake_rpc.respond("account/rateLimits/read", load_fixture("codex_rate_limits.json"))
    fake_rpc.respond("account/logout", {})

    provider.refresh_usage(account)
    provider.logout(account)

    assert fake_rpc.instances
    assert all(
        rpc.environment["CODEX_HOME"] != sentinel for rpc in fake_rpc.instances
    )


def test_provider_never_resolves_default_codex_profile(
    monkeypatch, provider, account, fake_rpc
):
    def forbidden_home(cls):
        raise AssertionError("default home lookup is forbidden")

    monkeypatch.setattr(Path, "home", classmethod(forbidden_home))
    fake_rpc.respond("account/rateLimits/read", load_fixture("codex_rate_limits.json"))

    snapshot = provider.refresh_usage(account)

    assert snapshot.account_id == account.id


def test_profile_preparation_rejects_symlink_config(
    provider, account, paths, tmp_path
):
    home = paths.account_home("codex", account.id)
    home.mkdir(parents=True)
    outside = tmp_path / "outside.toml"
    outside.write_text('cli_auth_credentials_store = "keep"\n', encoding="utf-8")
    (home / "config.toml").symlink_to(outside)

    with pytest.raises(ProviderError) as captured:
        provider.prepare_profile(account)

    assert captured.value.code == "unsafe_account_path"
    assert outside.read_text(encoding="utf-8") == (
        'cli_auth_credentials_store = "keep"\n'
    )


def test_initialize_rejects_wrong_account_home_without_exposing_it(
    provider, account, fake_rpc
):
    wrong_home = "/Users/test/.codex/sentinel-token"
    fake_rpc.respond(
        "initialize",
        {
            "userAgent": "codex_cli_rs/0.42.0",
            "codexHome": wrong_home,
            "platformFamily": "unix",
            "platformOs": "macos",
        },
    )

    with pytest.raises(ProviderError) as captured:
        provider.refresh_usage(account)

    assert captured.value.code == "unsafe_account_path"
    assert wrong_home not in captured.value.safe_message
    assert fake_rpc.instances[-1].closed is True


def test_refresh_maps_rpc_authentication_error_to_reauth_required(
    provider, account, fake_rpc
):
    fake_rpc.respond(
        "account/rateLimits/read",
        ProviderError("rpc_remote_error", "RPC account error."),
    )

    with pytest.raises(ProviderError) as captured:
        provider.refresh_usage(account)

    assert captured.value.code == "reauth_required"
