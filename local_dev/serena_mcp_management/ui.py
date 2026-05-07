"""UI primitives for the Serena agent launcher TUI.

This module provides a single-responsibility split of the launcher's screen
concerns:

* State    -- BoxModel / Item dataclasses (this section)
* Renderer -- BoxModel -> ANSI text + in-place updates (later task)
* Progress -- spinner ticker thread (later task)
* Prompt   -- yes/no confirmation (later task)
"""
from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TextIO

try:
    import termios
    import tty
    _RAW_TTY_AVAILABLE = True
except ImportError:  # pragma: no cover - non-Unix
    _RAW_TTY_AVAILABLE = False


PhaseKind = Literal["preflight", "serena-init", "launch-prep", "summary"]
ItemStatus = Literal["pending", "spin", "done", "warn", "skip", "info"]


@dataclass
class Item:
    id: str
    label: str
    value: str
    status: ItemStatus = "pending"


@dataclass
class BoxModel:
    phase: PhaseKind
    title: str
    items: list[Item] = field(default_factory=list)

    def replace_item(self, new: Item) -> None:
        for index, existing in enumerate(self.items):
            if existing.id == new.id:
                self.items[index] = new
                return
        raise KeyError(f"unknown item id: {new.id}")


# Renderer implementation

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_BOX_WIDTH = 60

# charmbracelet palette (gum/huh). Use truecolor (24-bit) escapes so the
# accents land on the exact charm hues rather than the closest 256-colour
# approximation.
PINK = "38;2;255;6;183"  # #FF06B7, charm hot pink
PURPLE = "38;2;135;75;253"  # #874BFD, charm vivid violet (huh focus tone)
MINT = "38;2;0;215;175"  # #00D7AF, charm mint -- legible label colour


def _ansi(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m"


def style_spinner(frame: int) -> str:
    """Return the spinner glyph for ``frame`` styled with the purple accent."""
    glyph = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    return _ansi(PURPLE, glyph)


_COUNT_KEYWORDS = sorted(
    [
        "memory files reset",
        "files to reset",
        "scan skipped",
        "to delete",
        "to keep",
        "deleted",
        "kept",
    ],
    key=len,
    reverse=True,
)


def style_count(phrase: str) -> str:
    """Colorize digits (pink) and count keywords (purple) using the gum palette.

    Plain phrase in, ANSI-formatted phrase out. Unmatched substrings pass through.
    Used by launcher for preflight/summary cleanup and memory rows.
    """
    if not phrase:
        return phrase
    result = re.sub(r"\d+", lambda m: _ansi(PINK, m.group(0)), phrase)
    for kw in _COUNT_KEYWORDS:
        result = result.replace(kw, _ansi(PURPLE, kw))
    return result


def _marker_for(status: ItemStatus, *, spin_frame: int = 0) -> str:
    if status == "spin":
        frame = SPINNER_FRAMES[spin_frame % len(SPINNER_FRAMES)]
        return _ansi(PURPLE, frame)
    if status == "done":
        return _ansi(PINK, "✓")
    if status == "warn":
        return _ansi("33", "!")
    if status == "skip":
        return _ansi("90", "-")
    if status == "info":
        return _ansi("90", "·")
    return _ansi("90", "o")  # pending


def _border(char: str) -> str:
    return _ansi("90", char * _BOX_WIDTH)


def render_box(model: BoxModel, *, spin_frame: int = 0) -> str:
    lines: list[str] = []
    lines.append("  " + _border("─"))
    header = f"{model.title}  ·  {model.phase}"
    lines.append("  " + _ansi(f"1;{PINK}", header))
    lines.append("  " + _border("─"))
    for item in model.items:
        marker = _marker_for(item.status, spin_frame=spin_frame)
        label = _ansi(MINT, f"{item.label:<10}")
        lines.append(f"  {marker} {label}  {item.value}")
    lines.append("  " + _border("─"))
    return "\n".join(lines) + "\n"


class BoxRenderer:
    """Renders a BoxModel and updates it in-place on subsequent draws.

    Uses ANSI cursor-up and erase-below escape sequences to overwrite the
    previous box in-place. Thread-safe via internal lock.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._last_line_count = 0
        self._lock = threading.Lock()

    def draw(self, model: BoxModel, *, spin_frame: int = 0) -> None:
        """Draw the box, updating in-place if previously drawn."""
        text = render_box(model, spin_frame=spin_frame)
        line_count = text.count("\n")
        with self._lock:
            if self._last_line_count > 0:
                self._stream.write(f"\x1b[{self._last_line_count}A\x1b[J")
            self._stream.write(text)
            self._stream.flush()
            self._last_line_count = line_count

    def clear(self) -> None:
        """Clear the box by moving cursor up and erasing."""
        with self._lock:
            if self._last_line_count > 0:
                self._stream.write(f"\x1b[{self._last_line_count}A\x1b[J")
                self._stream.flush()
                self._last_line_count = 0


class SpinnerTicker:
    """Periodically calls ``on_tick`` from a daemon thread until stopped."""

    def __init__(
        self,
        *,
        on_tick: Callable[[int], None],
        interval: float = 0.1,
    ) -> None:
        self._on_tick = on_tick
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)

    def _loop(self) -> None:
        frame = 0
        while not self._stop_event.wait(self._interval):
            frame += 1
            self._on_tick(frame)


# Prompt implementation


def _read_yes_no_arrow(
    question: str,
    *,
    default: bool,
    stream: TextIO,
    fd: int,
) -> bool:
    """huh-inspired arrow-key yes/no select.

    Renders two option lines, lets the user move the cursor with up/down
    arrows (or k/j), and confirms with Enter. Returns True for Yes.
    """
    options = ("Yes", "No")
    cursor = 0 if default else 1

    def render(initial: bool) -> None:
        if not initial:
            # Move cursor back to the start of the prompt block and erase.
            stream.write("\x1b[3A\x1b[J")
        stream.write(f"  \x1b[{PURPLE}m?\x1b[0m {question}\n")
        for index, label in enumerate(options):
            if index == cursor:
                stream.write(
                    f"    \x1b[{PURPLE}m▶\x1b[0m \x1b[{PURPLE}m{label}\x1b[0m\n"
                )
            else:
                stream.write(f"      \x1b[90m{label}\x1b[0m\n")
        stream.flush()

    render(initial=True)
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = os.read(fd, 1).decode(errors="replace")
            if ch == "\x1b":
                seq = os.read(fd, 2).decode(errors="replace")
                if seq == "[A" and cursor > 0:
                    cursor -= 1
                    render(initial=False)
                elif seq == "[B" and cursor < len(options) - 1:
                    cursor += 1
                    render(initial=False)
            elif ch in ("k", "K") and cursor > 0:
                cursor -= 1
                render(initial=False)
            elif ch in ("j", "J") and cursor < len(options) - 1:
                cursor += 1
                render(initial=False)
            elif ch in ("\r", "\n"):
                break
            elif ch in ("y", "Y"):
                cursor = 0
                break
            elif ch in ("n", "N"):
                cursor = 1
                break
            elif ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    # Collapse the prompt block to a single confirmation line.
    stream.write("\x1b[3A\x1b[J")
    chosen = options[cursor]
    stream.write(
        f"  \x1b[{PURPLE}m?\x1b[0m {question} \x1b[{PURPLE}m{chosen}\x1b[0m\n"
    )
    stream.flush()
    return cursor == 0


def confirm(
    question: str,
    *,
    default: bool,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> bool:
    """Prompt for a yes/no confirmation.

    When ``input_fn`` is left at its default and stdin is a TTY, render a
    huh-inspired arrow-key selector (Up/Down/k/j to move, Enter to confirm,
    y/n shortcuts also accepted). Otherwise fall back to a single-line
    text prompt that reads from ``input_fn`` (defaults to builtin input).

    Args:
        question: The prompt text.
        default: The default value if user presses Enter without input.
        stream: Output stream (defaults to sys.stdout).
        input_fn: Optional input function. Passing one forces line-input
            mode (used by tests and non-interactive callers).

    Returns:
        True if user answered yes, False otherwise.
    """
    out = stream if stream is not None else sys.stdout

    if input_fn is None and _RAW_TTY_AVAILABLE:
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            fd = -1
        if fd >= 0 and os.isatty(fd):
            return _read_yes_no_arrow(question, default=default, stream=out, fd=fd)

    reader = input_fn if input_fn is not None else input
    suffix = "[Y/n]" if default else "[y/N]"
    out.write(f"  > {question} {suffix} ")
    out.flush()
    reply = reader().strip().lower()
    if not reply:
        return default
    return reply in {"y", "yes"}
