"""Concrete app sync modules + single-source-of-truth registry.

Adding a new app: write the module under `dotsync/apps/`, then append its
class to APP_CLASSES below. APP_NAMES, app_descriptions(), build_app(), and
detect_present() all derive from this single tuple — no other site requires
edits.
"""
from __future__ import annotations
from dotsync.apps.base import App
from dotsync.apps.claude import ClaudeApp
from dotsync.apps.codex import CodexApp
from dotsync.apps.ghostty import GhosttyApp
from dotsync.apps.bettertouchtool import BetterTouchToolApp
from dotsync.apps.zsh import ZshApp

# Order is the canonical app order (used by detect_present and any UI listing).
APP_CLASSES: tuple[type[App], ...] = (
    ClaudeApp,
    CodexApp,
    GhosttyApp,
    BetterTouchToolApp,
    ZshApp,
)

_BY_NAME: dict[str, type[App]] = {c.name: c for c in APP_CLASSES}
APP_NAMES: frozenset[str] = frozenset(_BY_NAME)


def app_descriptions() -> dict[str, str]:
    """Return {name: description} for every registered app, preserving registry order."""
    return {c.name: c.description for c in APP_CLASSES}


def build_app(name: str, cfg) -> App:
    """Construct a configured App instance for `name`.

    Dispatches via cls.from_config(cfg) so apps with config dependencies
    (e.g. BetterTouchToolApp) construct themselves correctly without the
    registry knowing per-app details.
    """
    cls = _BY_NAME.get(name)
    if cls is None:
        raise KeyError(f"unknown app: {name}. Supported: {sorted(APP_NAMES)}")
    return cls.from_config(cfg)


def detect_present() -> list[str]:
    """Return the names of apps whose local install is detected on this machine.

    Order matches APP_CLASSES (canonical registry order).
    """
    return [c.name for c in APP_CLASSES if c.is_present_locally()]
