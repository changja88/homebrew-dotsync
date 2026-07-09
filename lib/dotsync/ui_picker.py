"""Interactive arrow-key checkbox picker.

Used by `dotsync init` and `dotsync apps` to let the user toggle which
apps to track. stdlib only; falls back to per-app y/n prompts when
stdin/stdout is not a TTY.

Design split:
    PickerState         — pure logic + presentation data (no I/O), unit-testable
    _read_key           — termios + ANSI escape parsing
    _render             — ANSI redraw
    _fallback_per_app   — non-TTY sequential prompts
    pick_apps           — top-level glue (TTY detect → loop or fallback)

Each row in the picker shows three signals at once:
    [x]/[ ]            — tracked / untracked (toggleable)
    installed / not    — local install state (read-only, from `detected`)
    optional annotation — extra info per item (e.g. "2 presets" for BTT)
The combination drives the row color so misconfigured states stand out:
    [x] + installed       → default (healthy)
    [x] + not installed   → red dim    (cleanup candidate)
    [ ] + installed       → yellow dim (add candidate)
    [ ] + not installed   → dim
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty

from . import ui


_GLYPH_CURSOR = "▸"
_CHECK_ON = "[x]"
_CHECK_OFF = "[ ]"
_HINT_INSTALLED = "installed"
_HINT_NOT_INSTALLED = "not installed"
_HINT_COL = 24  # column where the right-side hint starts (after the name)
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"


class PickerState:
    """Pure logic + presentation data for the picker.

    `selected` and `cursor` mutate; `detected` and `annotations` are
    read-only context the renderer uses to color rows.

    Events: 'up', 'down', 'space', 'enter', 'cancel'. Anything else is a no-op.
    """

    def __init__(
        self,
        items: list[str],
        preselected,
        detected: "set[str] | None" = None,
        annotations: "dict[str, str] | None" = None,
    ) -> None:
        self.items = list(items)
        self.selected: set[str] = {a for a in preselected if a in self.items}
        self.detected: set[str] = set(detected) if detected else set()
        self.annotations: dict[str, str] = dict(annotations) if annotations else {}
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
    def result(self) -> "list[str] | None":
        if self.cancelled:
            return None
        return [a for a in self.items if a in self.selected]


class SingleChoiceState:
    """Pure logic for a radio (choose-exactly-one) picker.

    Unlike PickerState there is no toggle set: the cursor *is* the selection.
    Events: 'up', 'down', 'enter', 'cancel'. 'space' and unknown keys are no-ops.
    """

    def __init__(self, items: list[str], current: "str | None" = None) -> None:
        self.items = list(items)
        self.cursor = self.items.index(current) if current in self.items else 0
        self.done = False
        self.cancelled = False

    def handle(self, key: str) -> None:
        n = len(self.items)
        if not n:
            return
        if key == "up":
            self.cursor = (self.cursor - 1) % n
        elif key == "down":
            self.cursor = (self.cursor + 1) % n
        elif key == "enter":
            self.done = True
        elif key == "cancel":
            self.cancelled = True
        # 'space' and unknown keys: silent no-op (radio has nothing to toggle)

    @property
    def result(self) -> "str | None":
        if self.cancelled or not self.items:
            return None
        return self.items[self.cursor]


def _read_key() -> "str | None":
    """Read a single keystroke event. Caller must already have set the
    terminal to cbreak/raw mode. Returns one of:
        'up', 'down', 'space', 'enter', 'cancel', or None (unrecognized).
    Raises KeyboardInterrupt on ctrl+c.

    Uses os.read(fd, 1) instead of sys.stdin.read(1) to bypass Python's
    text-mode stdin buffer — without this, a multi-byte arrow sequence
    (\\x1b[A) gets slurped into Python's buffer in one go, leaving the
    fd empty and causing the subsequent select.select peek to time out
    (so arrows wrongly read as bare-ESC = cancel).
    """
    fd = sys.stdin.fileno()
    ch = os.read(fd, 1)
    if ch == b"\x03":  # ctrl+c
        raise KeyboardInterrupt
    if ch == b"\x1b":  # ESC — could start an arrow sequence
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return "cancel"
        if os.read(fd, 1) != b"[":
            return "cancel"
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return "cancel"
        arrow = os.read(fd, 1)
        if arrow == b"A":
            return "up"
        if arrow == b"B":
            return "down"
        return None  # other CSI sequence — ignore
    if ch == b" ":
        return "space"
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch in (b"q", b"Q"):
        return "cancel"
    return None


def _row_color(*, selected: bool, installed: bool) -> str:
    """Map (selected, installed) to an ANSI color prefix for the row.

    Healthy combinations get the default fg; mismatches get a tinted dim
    so misconfigured states stand out without being loud.
    """
    if selected and installed:
        return ""  # healthy → default
    if selected and not installed:
        return ui.RED + ui.DIM_ANSI  # tracked but missing
    if not selected and installed:
        return ui.YELLOW + ui.DIM_ANSI  # installed but untracked
    return ui.DIM_ANSI  # neither


def _render(state: PickerState, title: str, *, first: bool) -> None:
    """Print or redraw the picker. `first=True` for the initial pass;
    subsequent passes move the cursor up and clear the previous frame."""
    out = sys.stdout
    n = len(state.items)
    if not first:
        out.write(f"\x1b[{n + 2}A")  # move cursor up (n items + title + spacer)
        out.write("\x1b[J")  # clear from cursor to end of screen
    title_part = ui._wrap(ui.BOLD, title)
    hint = ui._wrap(ui.DIM_ANSI, "↑/↓ move · space toggle · enter submit")
    out.write(f"  {title_part}   {hint}\n")
    out.write("\n")
    for i, name in enumerate(state.items):
        cursor_marker = (
            ui._wrap(ui.PRIMARY, _GLYPH_CURSOR) if i == state.cursor else " "
        )
        is_selected = name in state.selected
        is_installed = name in state.detected
        if is_selected:
            check = ui._wrap(ui.GREEN, _CHECK_ON)
        else:
            check = _CHECK_OFF
        # Right-side hint: "installed" / "not installed", with optional
        # annotation appended ("installed · 2 presets").
        base_hint = _HINT_INSTALLED if is_installed else _HINT_NOT_INSTALLED
        annotation = state.annotations.get(name, "")
        if annotation:
            hint_text = f"{base_hint} · {annotation}"
        else:
            hint_text = base_hint
        # Pad the name so hints align in a column.
        name_padded = name.ljust(_HINT_COL)
        # Apply a row-level color tint to the name+hint combo so misconfigured
        # rows draw the eye. Cursor marker and check stay in their own colors.
        tint = _row_color(selected=is_selected, installed=is_installed)
        body = f"{name_padded}{hint_text}"
        if tint:
            body = f"{tint}{body}{ui.RESET}" if ui._color_enabled() else body
        out.write(f"  {cursor_marker} {check} {body}\n")
    out.flush()


def _interactive_supported(out=None) -> bool:
    """True only if both stdin and the picker's output stream are real TTYs.
    Pytest captures streams (isatty=False) → fallback path is used during tests.

    `out` lets a caller drive the picker on stderr (so stdout can carry a
    machine-readable result) — the tty check then targets that stream."""
    out = out if out is not None else sys.stdout
    return sys.stdin.isatty() and out.isatty()


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


def _enter_raw_mode(out=None):
    """Switch terminal to cbreak mode and hide the cursor. Returns an
    opaque token to pass back into _restore_terminal. Cursor control is
    written to `out` (default stdout) so it lands on the picker's stream."""
    out = out if out is not None else sys.stdout
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    out.write(_CURSOR_HIDE)
    out.flush()
    return (fd, old, out)


def _restore_terminal(token) -> None:
    """Revert what _enter_raw_mode did. Always safe to call."""
    fd, old, out = token
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    out.write(_CURSOR_SHOW)
    out.flush()


def _drain_input() -> None:
    """Flush any pre-typed bytes so muscle-memory keystrokes typed before the
    picker appeared can't move the cursor or submit a selection."""
    try:
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except (termios.error, ValueError, OSError):
        pass


def _render_one(
    state: SingleChoiceState,
    title: str,
    labels: "dict[str, str] | None",
    *,
    first: bool,
    out=None,
) -> None:
    out = out if out is not None else sys.stdout
    n = len(state.items)
    if not first:
        out.write(f"\x1b[{n + 2}A")
        out.write("\x1b[J")
    title_part = ui._wrap(ui.BOLD, title)
    hint = ui._wrap(ui.DIM_ANSI, "↑/↓ move · enter switch · esc keep current")
    out.write(f"  {title_part}   {hint}\n\n")
    for i, name in enumerate(state.items):
        cursor_marker = (
            ui._wrap(ui.PRIMARY, _GLYPH_CURSOR) if i == state.cursor else " "
        )
        dot = ui._wrap(ui.GREEN, "●") if i == state.cursor else "○"
        label = (labels or {}).get(name, name)
        out.write(f"  {cursor_marker} {dot} {label}\n")
    out.flush()


def pick_one(
    items: list[str],
    *,
    current: "str | None" = None,
    title: str = "Select an account",
    labels: "dict[str, str] | None" = None,
    stream=None,
) -> "str | None":
    """Interactive radio picker. Returns the chosen item, or None.

    Returns None on cancel (esc / q / ctrl+c) AND in any non-TTY environment
    (CI, pipe, pytest) — the caller must treat None as "no change", so an
    account is never switched unattended.

    `stream` (default stdout) is where the picker draws. Pass `sys.stderr` when
    the caller captures stdout for a machine-readable result (e.g. the launcher
    reading the chosen account name).
    """
    out = stream if stream is not None else sys.stdout
    if not items:
        return None
    if not _interactive_supported(out):
        return None

    state = SingleChoiceState(items, current=current)
    token = _enter_raw_mode(out)
    try:
        _drain_input()
        _render_one(state, title, labels, first=True, out=out)
        while not state.done and not state.cancelled:
            try:
                key = _read_key()
            except KeyboardInterrupt:
                state.cancelled = True
                break
            if key is not None:
                state.handle(key)
            _render_one(state, title, labels, first=False, out=out)
    finally:
        _restore_terminal(token)
    print(file=out)
    return state.result


def pick_apps(
    items: list[str],
    preselected,
    detected: "set[str] | None" = None,
    *,
    annotations: "dict[str, str] | None" = None,
    title: str = "Pick apps to track",
) -> "list[str] | None":
    """Interactive arrow-key checkbox picker.

    `detected` and `annotations` are presentation hints — they don't
    influence which items are selected, only how each row is drawn.

    Returns the selected items in input order, or None if the user
    cancelled (q / esc / ctrl+c). On non-TTY environments (CI, piped
    stdin, pytest) falls back to per-app y/n prompts and never returns
    None — fallback can't be cancelled, only answered."""
    if not _interactive_supported():
        return _fallback_per_app(items, preselected)

    state = PickerState(items, preselected, detected=detected, annotations=annotations)
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
