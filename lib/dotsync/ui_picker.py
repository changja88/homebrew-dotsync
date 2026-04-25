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
