"""Interactive arrow-key checkbox picker.

Used by `dotsync init` (edit branch) and `dotsync apps edit` to let the
user toggle which apps to track. stdlib only; falls back to per-app y/n
prompts when stdin/stdout is not a TTY.

Design split:
    PickerState         — pure logic (no I/O), unit-testable
    _read_key           — termios + ANSI escape parsing
    _render             — ANSI redraw
    _fallback_per_app   — non-TTY sequential prompts
    pick_apps           — top-level glue (TTY detect → loop or fallback)
"""
from __future__ import annotations

import select
import sys
import termios
import tty

from . import ui


_GLYPH_CURSOR = "▸"
_CHECK_ON = "[x]"
_CHECK_OFF = "[ ]"
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"


class PickerState:
    """Pure logic for the picker. Inputs are abstract event strings.

    Events: 'up', 'down', 'space', 'enter', 'cancel'. Anything else is a no-op.
    """

    def __init__(self, items: list[str], preselected) -> None:
        self.items = list(items)
        self.selected: set[str] = {a for a in preselected if a in self.items}
        self.cursor = 0
        self.done = False
        self.cancelled = False

    def handle(self, key: str) -> None:
        n = len(self.items)
        if key == "up":
            self.cursor = (self.cursor - 1) % n
        elif key == "down":
            self.cursor = (self.cursor + 1) % n
        elif key == "space":
            name = self.items[self.cursor]
            if name in self.selected:
                self.selected.remove(name)
            else:
                self.selected.add(name)
        elif key == "enter":
            self.done = True
        elif key == "cancel":
            self.cancelled = True
        # unknown keys: silent no-op

    @property
    def result(self) -> list[str] | None:
        if self.cancelled:
            return None
        return [a for a in self.items if a in self.selected]


def _read_key() -> str | None:
    """Read a single keystroke event. Caller must already have set the
    terminal to cbreak/raw mode. Returns one of:
        'up', 'down', 'space', 'enter', 'cancel', or None (unrecognized).
    Raises KeyboardInterrupt on ctrl+c.
    """
    ch = sys.stdin.read(1)
    if ch == "\x03":            # ctrl+c
        raise KeyboardInterrupt
    if ch == "\x1b":            # ESC — could start an arrow sequence
        # Peek for `[A` / `[B` with a short timeout so a bare ESC press
        # (user wants to cancel) doesn't hang waiting for a 3rd byte.
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            return "cancel"
        if sys.stdin.read(1) != "[":
            return "cancel"
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            return "cancel"
        arrow = sys.stdin.read(1)
        if arrow == "A":
            return "up"
        if arrow == "B":
            return "down"
        return None             # other CSI sequence — ignore
    if ch == " ":
        return "space"
    if ch in ("\r", "\n"):
        return "enter"
    if ch in ("q", "Q"):
        return "cancel"
    return None


def _render(state: PickerState, title: str, *, first: bool) -> None:
    """Print or redraw the picker. `first=True` for the initial pass;
    subsequent passes move the cursor up and clear the previous frame."""
    out = sys.stdout
    n = len(state.items)
    if not first:
        out.write(f"\x1b[{n + 2}A")   # move cursor up (n items + title + spacer)
        out.write("\x1b[J")           # clear from cursor to end of screen
    title_part = ui._wrap(ui.BOLD, title)
    hint = ui._wrap(ui.DIM_ANSI, "↑/↓ move · space toggle · enter submit")
    out.write(f"  {title_part}   {hint}\n")
    out.write("\n")
    for i, name in enumerate(state.items):
        cursor_marker = ui._wrap(ui.PRIMARY, _GLYPH_CURSOR) if i == state.cursor else " "
        if name in state.selected:
            check = ui._wrap(ui.GREEN, _CHECK_ON)
        else:
            check = _CHECK_OFF
        out.write(f"  {cursor_marker} {check} {name}\n")
    out.flush()


def _interactive_supported() -> bool:
    """True only if both stdin and stdout are real TTYs. Pytest captures
    streams (isatty=False) → fallback path is used during tests."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _fallback_per_app(items: list[str], preselected) -> list[str]:
    """Sequential y/n prompt for non-TTY environments (CI, pipe, pytest).
    Default is Y for preselected items, N otherwise — bare Enter accepts
    the default."""
    pre = {a for a in preselected if a in items}
    selected: list[str] = []
    for name in items:
        default_yes = name in pre
        hint = "Y/n" if default_yes else "y/N"
        ans = ui.ask(f"track {name}?", default=hint).strip().lower()
        if not ans:
            keep = default_yes
        else:
            keep = ans in ("y", "yes")
        if keep:
            selected.append(name)
    return selected


def _enter_raw_mode():
    """Switch terminal to cbreak mode and hide the cursor. Returns an
    opaque token to pass back into _restore_terminal."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write(_CURSOR_HIDE)
    sys.stdout.flush()
    return (fd, old)


def _restore_terminal(token) -> None:
    """Revert what _enter_raw_mode did. Always safe to call."""
    fd, old = token
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write(_CURSOR_SHOW)
    sys.stdout.flush()


def pick_apps(
    items: list[str],
    preselected,
    title: str = "Pick apps to track",
) -> list[str] | None:
    """Interactive arrow-key checkbox picker.

    Returns the selected items in input order, or None if the user
    cancelled (q / esc / ctrl+c). On non-TTY environments (CI, piped
    stdin, pytest) falls back to per-app y/n prompts and never returns
    None — fallback can't be cancelled, only answered."""
    if not _interactive_supported():
        return _fallback_per_app(items, preselected)

    state = PickerState(items, preselected)
    token = _enter_raw_mode()
    try:
        _render(state, title, first=True)
        while not state.done and not state.cancelled:
            try:
                key = _read_key()
            except KeyboardInterrupt:
                state.cancelled = True
                break
            if key is not None:
                state.handle(key)
            _render(state, title, first=False)
    finally:
        _restore_terminal(token)
    print()  # blank line after picker exits
    return state.result
