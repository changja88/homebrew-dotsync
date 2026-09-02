"""Explicit production composition root for the browser DotSync UI."""

from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

from dotsync.accounts import AccountStore
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppStateError, AppStateStore
from dotsync.config import Config, ConfigError
from dotsync.macos_actions import (
    choose_sync_folder,
    open_provider_login_url,
    reveal_in_finder,
)
from dotsync.providers.claude import ClaudeUsageProvider
from dotsync.providers.codex import CodexUsageProvider
from dotsync.sync_service import SyncService
from dotsync.usage import UsageCache, UsageService
from dotsync.web.api import load_persisted_sync_service
from dotsync.web.server import (
    WebApplication,
    run_ui_server,
    verify_packaged_assets,
)


_PACKAGED_UI_ASSETS = (
    "index.html",
    "styles.css",
    "state.mjs",
    "api-client.mjs",
    "render.mjs",
    "app.mjs",
)


def build_web_application(*, idle_shutdown_enabled: bool) -> WebApplication:
    paths = AppPaths.for_home(Path.home())
    state_store = AppStateStore(paths)
    accounts = AccountStore(paths)
    cache = UsageCache(paths)
    usage = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={
            "codex": CodexUsageProvider(paths),
            "claude": ClaudeUsageProvider(paths),
        },
    )
    return WebApplication(
        paths=paths,
        state_store=state_store,
        account_store=accounts,
        usage_service=usage,
        sync_service=_load_saved_sync_service(state_store),
        folder_picker=choose_sync_folder,
        sync_folder_initializer=_empty_sync_service,
        reveal_app_data=reveal_in_finder,
        open_provider_url=open_provider_login_url,
        idle_shutdown_enabled=idle_shutdown_enabled,
    )


def _load_saved_sync_service(state_store: AppStateStore) -> SyncService | None:
    try:
        return load_persisted_sync_service(
            state_store=state_store,
            factory=_empty_sync_service,
        )
    except (AppStateError, ConfigError, OSError, TypeError, ValueError):
        return None


def _empty_sync_service() -> SyncService:
    return SyncService(Config(dir=Path("/dev/null"), apps=[]))


def run_browser_ui(*, open_browser: bool) -> int:
    application = build_web_application(idle_shutdown_enabled=True)
    with run_ui_server(application) as server:
        if open_browser:
            webbrowser.open(server.launch_url_for(destination="overview"))
        server.wait()
    return 0


def check_ui_installation() -> None:
    verify_packaged_assets(_PACKAGED_UI_ASSETS)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
