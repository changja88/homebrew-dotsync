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


def test_build_app_bettertouchtool_uses_config_preset(tmp_path):
    cfg = Config(dir=tmp_path, apps=["bettertouchtool"], bettertouchtool_preset="MyPreset")
    app = build_app("bettertouchtool", cfg)
    assert app.preset == "MyPreset"


def test_supported_apps_matches_registry():
    """SUPPORTED_APPS in config.py and APP_NAMES here must stay in sync."""
    from dotsync.config import SUPPORTED_APPS
    assert SUPPORTED_APPS == set(APP_NAMES)
