"""Concrete app sync modules + factory.

We use a factory (build_app) instead of a static REGISTRY because BetterTouchToolApp
needs config-driven construction (preset name).
"""
from __future__ import annotations
from dotsync.apps.base import App
from dotsync.apps.claude import ClaudeApp
from dotsync.apps.ghostty import GhosttyApp
from dotsync.apps.bettertouchtool import BetterTouchToolApp
from dotsync.apps.zsh import ZshApp

APP_NAMES = frozenset({"claude", "ghostty", "bettertouchtool", "zsh"})

_DESCRIPTIONS = {
    "claude": ClaudeApp().description,
    "ghostty": GhosttyApp().description,
    "bettertouchtool": BetterTouchToolApp().description,
    "zsh": ZshApp().description,
}


def app_descriptions() -> dict[str, str]:
    return dict(_DESCRIPTIONS)


def build_app(name: str, cfg) -> App:
    """Construct a configured App instance for `name`.

    `cfg` is a dotsync.config.Config.
    """
    if name not in APP_NAMES:
        raise KeyError(f"unknown app: {name}. Supported: {sorted(APP_NAMES)}")
    if name == "claude":
        return ClaudeApp()
    if name == "ghostty":
        return GhosttyApp()
    if name == "bettertouchtool":
        return BetterTouchToolApp(presets=cfg.bettertouchtool_presets)
    if name == "zsh":
        return ZshApp()
    raise KeyError(name)  # unreachable


_APP_CLASSES = {
    "claude": ClaudeApp,
    "ghostty": GhosttyApp,
    "bettertouchtool": BetterTouchToolApp,
    "zsh": ZshApp,
}


def detect_present() -> list[str]:
    """Return the names of apps whose local install is detected on this machine.

    Order is stable: matches the order in `APP_NAMES` (claude, ghostty, bettertouchtool, zsh).
    """
    return [name for name in ["claude", "ghostty", "bettertouchtool", "zsh"]
            if _APP_CLASSES[name].is_present_locally()]
