import pytest
from dotsync.apps import APP_NAMES, build_app
from dotsync.config import Config


def test_app_names_derive_from_app_classes():
    """APP_NAMES is derived from APP_CLASSES, not a separate literal — adding
    a new app only requires appending to APP_CLASSES."""
    from dotsync.apps import APP_CLASSES

    assert APP_NAMES == frozenset(c.name for c in APP_CLASSES)


def test_build_app_returns_instance(tmp_path):
    cfg = Config(dir=tmp_path, apps=["zsh"])
    app = build_app("zsh", cfg)
    assert app.name == "zsh"


def test_build_app_returns_herdr_instance(tmp_path):
    cfg = Config(dir=tmp_path, apps=["herdr"])

    app = build_app("herdr", cfg)

    assert type(app).__name__ == "HerdrApp"
    assert app.name == "herdr"


def test_build_app_unknown_raises(tmp_path):
    cfg = Config(dir=tmp_path, apps=[])
    with pytest.raises(KeyError):
        build_app("nonsense", cfg)


def test_build_app_bettertouchtool_uses_config_presets(tmp_path):
    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["MyPreset", "Other"]}},
    )
    app = build_app("bettertouchtool", cfg)
    assert app.presets == ["MyPreset", "Other"]


def test_build_app_uses_from_config_polymorphism(tmp_path, monkeypatch):
    """build_app must call cls.from_config(cfg), not have its own if-elif."""
    from dotsync.apps import build_app
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    calls = []
    original = BetterTouchToolApp.from_config

    @classmethod
    def spy(cls, cfg):
        calls.append(cfg)
        return original.__func__(cls, cfg)

    monkeypatch.setattr(BetterTouchToolApp, "from_config", spy)
    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["X"]}},
    )
    app = build_app("bettertouchtool", cfg)

    assert len(calls) == 1 and calls[0] is cfg
    assert app.presets == ["X"]
