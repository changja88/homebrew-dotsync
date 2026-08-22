from __future__ import annotations

from pathlib import Path

import pytest

import dotsync.ui_app as ui_app_module
import dotsync.web.api as api_module
from dotsync.app_state import AppState, AppStateStore
from dotsync.cli import _build_parser, main
from dotsync.config import Config
from dotsync.sync_service import SyncService
from dotsync.ui_app import (
    _load_saved_sync_service,
    build_web_application,
    check_ui_installation,
    run_browser_ui,
    run_native_ui,
)


def _write_config(folder: Path, value: str = "apps = []\n") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dotsync.toml").write_text(value, encoding="utf-8")


def _state_store(tmp_path: Path, sync_dir: Path) -> AppStateStore:
    paths = ui_app_module.AppPaths(tmp_path / "app-data")
    store = AppStateStore(paths)
    store.save(AppState(sync_dir=str(sync_dir)))
    return store


def _factory() -> SyncService:
    return SyncService(Config(dir=Path("/dev/null"), apps=[]))


def test_ui_parser_exposes_browser_ui_but_hides_native_host():
    help_text = _build_parser().format_help()

    assert "ui" in help_text
    assert "--native-host" not in help_text
    assert "--check" not in help_text


def test_ui_help_hides_internal_flags(capsys):
    with pytest.raises(SystemExit) as captured:
        main(["ui", "--help"])

    assert captured.value.code == 0
    output = capsys.readouterr().out
    assert "--no-open" in output
    assert "--native-host" not in output
    assert "--check" not in output


def test_ui_check_does_not_create_application_support(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert main(["ui", "--check"]) == 0
    assert not (tmp_path / "Library/Application Support/DotSync").exists()


def test_check_ui_installation_does_not_construct_application_services(monkeypatch):
    monkeypatch.setattr(
        ui_app_module,
        "build_web_application",
        lambda **kwargs: pytest.fail("diagnostic constructed application services"),
    )

    check_ui_installation()


def test_browser_ui_modes_map_only_the_public_open_choice(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dotsync.cli.run_browser_ui",
        lambda *, open_browser: calls.append(open_browser) or 0,
    )

    assert main(["ui"]) == 0
    assert main(["ui", "--no-open"]) == 0
    assert calls == [True, False]


def test_native_host_uses_stdio_pipes_and_never_opens_browser(monkeypatch):
    calls = []
    monkeypatch.setattr("dotsync.cli.run_native_ui", lambda: calls.append("native") or 0)
    monkeypatch.setattr(
        "webbrowser.open",
        lambda value: (_ for _ in ()).throw(AssertionError(value)),
    )

    assert main(["ui", "--native-host"]) == 0
    assert calls == ["native"]


def test_native_and_check_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit) as captured:
        main(["ui", "--native-host", "--check"])

    assert captured.value.code == 2


def test_browser_keyboard_interrupt_returns_130(monkeypatch):
    monkeypatch.setattr(
        "dotsync.cli.run_browser_ui",
        lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert main(["ui", "--no-open"]) == 130


def test_native_failure_uses_fixed_nonzero_code_and_static_stderr(monkeypatch, capsys):
    monkeypatch.setattr(
        "dotsync.cli.run_native_ui",
        lambda: (_ for _ in ()).throw(RuntimeError("origin token private")),
    )

    assert main(["ui", "--native-host"]) == 7
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "dotsync: native UI failed.\n"
    assert "origin" not in captured.err
    assert "token" not in captured.err


def test_build_web_application_uses_managed_profiles_not_default_cli_homes(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))

    application = build_web_application(idle_shutdown_enabled=False)
    try:
        assert application.idle_shutdown_enabled is False
        assert not (tmp_path / ".claude").exists()
        assert not (tmp_path / ".claude.json").exists()
        assert not (tmp_path / ".codex").exists()
    finally:
        application.shutdown()


def test_run_browser_ui_opens_only_the_fixed_manager_launch_url(monkeypatch):
    calls = []

    class Server:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *args):
            calls.append("close")

        def launch_url_for(self, *, surface, destination):
            calls.append((surface, destination))
            return "browser-launch"

        def wait(self):
            calls.append("wait")

    application = object()
    monkeypatch.setattr(
        ui_app_module,
        "build_web_application",
        lambda *, idle_shutdown_enabled: calls.append(("idle", idle_shutdown_enabled)) or application,
    )
    monkeypatch.setattr(ui_app_module, "run_ui_server", lambda value: Server())
    monkeypatch.setattr(ui_app_module.webbrowser, "open", lambda value: calls.append(("open", value)))

    assert run_browser_ui(open_browser=True) == 0
    assert calls == [
        ("idle", True),
        "enter",
        ("manager", "overview"),
        ("open", "browser-launch"),
        "wait",
        "close",
    ]


def test_run_browser_ui_closes_server_when_wait_is_interrupted(monkeypatch):
    calls = []

    class Server:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("close")

        def wait(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(ui_app_module, "build_web_application", lambda **kwargs: object())
    monkeypatch.setattr(ui_app_module, "run_ui_server", lambda value: Server())

    with pytest.raises(KeyboardInterrupt):
        run_browser_ui(open_browser=False)

    assert calls == ["close"]


def test_run_native_ui_passes_binary_stdio_and_parent_owned_lifetime(monkeypatch):
    calls = []
    application = object()
    control = object()
    handshake = object()

    class Input:
        buffer = control

    class Output:
        buffer = handshake

    monkeypatch.setattr(ui_app_module.sys, "stdin", Input())
    monkeypatch.setattr(ui_app_module.sys, "stdout", Output())
    monkeypatch.setattr(
        ui_app_module,
        "build_web_application",
        lambda *, idle_shutdown_enabled: calls.append(("idle", idle_shutdown_enabled)) or application,
    )
    monkeypatch.setattr(
        ui_app_module,
        "run_native_host",
        lambda value, *, control, handshake: calls.append((value, control, handshake)) or 9,
    )

    assert run_native_ui() == 9
    assert calls == [("idle", False), (application, control, handshake)]


def test_saved_symlink_is_not_loaded_or_initialized(tmp_path):
    real = tmp_path / "real-sync"
    _write_config(real)
    selected = tmp_path / "linked-sync"
    selected.symlink_to(real, target_is_directory=True)
    store = _state_store(tmp_path, selected)
    before = (real / "dotsync.toml").read_bytes()

    assert _load_saved_sync_service(store) is None
    assert (real / "dotsync.toml").read_bytes() == before


def test_replaced_saved_directory_identity_is_not_published(monkeypatch, tmp_path):
    selected = tmp_path / "sync"
    _write_config(selected)
    store = _state_store(tmp_path, selected)
    original_builder = api_module._build_sync_directory_candidate

    def replace_after_build(factory, sync_dir):
        candidate = original_builder(factory, sync_dir)
        moved = tmp_path / "moved-sync"
        selected.rename(moved)
        _write_config(selected)
        return candidate

    monkeypatch.setattr(api_module, "_build_sync_directory_candidate", replace_after_build)

    assert _load_saved_sync_service(store) is None


def test_final_config_symlink_is_not_published(monkeypatch, tmp_path):
    selected = tmp_path / "sync"
    _write_config(selected)
    store = _state_store(tmp_path, selected)
    external = tmp_path / "external.toml"
    external.write_text("apps = []\n", encoding="utf-8")
    original_factory = ui_app_module._empty_sync_service

    def swap_config_during_factory():
        (selected / "dotsync.toml").unlink()
        (selected / "dotsync.toml").symlink_to(external)
        return original_factory()

    monkeypatch.setattr(ui_app_module, "_empty_sync_service", swap_config_during_factory)

    assert _load_saved_sync_service(store) is None
    assert external.read_text(encoding="utf-8") == "apps = []\n"


@pytest.mark.parametrize("value", [None, "not = valid = toml\n"])
def test_missing_or_malformed_saved_config_returns_none_without_initializing(
    monkeypatch, tmp_path, value
):
    selected = tmp_path / "sync"
    selected.mkdir()
    if value is not None:
        (selected / "dotsync.toml").write_text(value, encoding="utf-8")
    store = _state_store(tmp_path, selected)
    calls = []
    monkeypatch.setattr(
        ui_app_module,
        "_empty_sync_service",
        lambda: calls.append("factory") or _factory(),
    )

    assert _load_saved_sync_service(store) is None
    assert calls == []
    if value is None:
        assert not (selected / "dotsync.toml").exists()


def test_valid_saved_sync_folder_loads_existing_configuration(tmp_path):
    selected = tmp_path / "sync"
    _write_config(selected)
    store = _state_store(tmp_path, selected)

    service = _load_saved_sync_service(store)

    assert service is not None
    assert service.config.dir == selected
    assert service.config.apps == []
