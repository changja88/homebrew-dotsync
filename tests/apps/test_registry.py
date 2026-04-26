import pytest
from dotsync.apps import APP_NAMES, build_app
from dotsync.config import Config


def test_app_names_are_supported_set():
    assert APP_NAMES == frozenset({"claude", "ghostty", "bettertouchtool", "zsh"})


def test_build_app_returns_instance(tmp_path):
    cfg = Config(dir=tmp_path, apps=["zsh"])
    app = build_app("zsh", cfg)
    assert app.name == "zsh"


def test_build_app_unknown_raises(tmp_path):
    cfg = Config(dir=tmp_path, apps=[])
    with pytest.raises(KeyError):
        build_app("nonsense", cfg)


def test_build_app_bettertouchtool_uses_config_presets(tmp_path):
    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        bettertouchtool_presets=["MyPreset", "Other"],
    )
    app = build_app("bettertouchtool", cfg)
    assert app.presets == ["MyPreset", "Other"]


def test_supported_apps_matches_registry():
    """SUPPORTED_APPS in config.py and APP_NAMES here must stay in sync."""
    from dotsync.config import SUPPORTED_APPS
    assert SUPPORTED_APPS == set(APP_NAMES)


def test_detect_present_returns_only_locally_installed(fake_home, monkeypatch):
    """detect_present() asks each app's is_present_locally() classmethod."""
    from dotsync.apps import detect_present
    # zsh: present
    (fake_home / ".zshrc").write_text("X")
    # claude: present
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "settings.json").write_text("{}")
    # ghostty: absent (no config dir)
    # bettertouchtool: absent (point APP_PATH to nowhere)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH",
        fake_home / "no-btt-here.app",
    )
    detected = detect_present()
    assert "zsh" in detected
    assert "claude" in detected
    assert "ghostty" not in detected
    assert "bettertouchtool" not in detected
