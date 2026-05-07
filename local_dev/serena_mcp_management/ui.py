"""UI primitives for the Serena agent launcher TUI.

This module provides a single-responsibility split of the launcher's screen
concerns:

* State    -- BoxModel / Item dataclasses (this section)
* Renderer -- BoxModel -> ANSI text + in-place updates (later task)
* Progress -- spinner ticker thread (later task)
* Prompt   -- yes/no confirmation (later task)
"""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TextIO


PhaseKind = Literal["preflight", "serena-init", "launch-prep", "summary"]
ItemStatus = Literal["pending", "spin", "done", "warn", "skip"]


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


def _ansi(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m"


def _marker_for(status: ItemStatus, *, spin_frame: int = 0) -> str:
    if status == "spin":
        frame = SPINNER_FRAMES[spin_frame % len(SPINNER_FRAMES)]
        return _ansi("36", frame)
    if status == "done":
        return _ansi("32", "✓")
    if status == "warn":
        return _ansi("33", "!")
    if status == "skip":
        return _ansi("90", "-")
    return _ansi("90", "o")  # pending


def _border(char: str) -> str:
    return _ansi("90", char * _BOX_WIDTH)


def render_box(model: BoxModel, *, spin_frame: int = 0) -> str:
    lines: list[str] = []
    lines.append("  " + _border("─"))
    header = f"{model.title}  ·  {model.phase}"
    lines.append("  " + _ansi("1;36", header))
    lines.append("  " + _border("─"))
    for item in model.items:
        marker = _marker_for(item.status, spin_frame=spin_frame)
        label = _ansi("90", f"{item.label:<10}")
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


def confirm(
    question: str,
    *,
    default: bool,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] = input,
) -> bool:
    """Prompt for a yes/no confirmation.

    Args:
        question: The prompt text.
        default: The default value if user presses Enter without input.
        stream: Output stream (defaults to sys.stdout).
        input_fn: Input function (defaults to builtin input).

    Returns:
        True if user answered yes/y, False otherwise.
    """
    out = stream if stream is not None else sys.stdout
    suffix = "[Y/n]" if default else "[y/N]"
    out.write(f"  > {question} {suffix} ")
    out.flush()
    reply = input_fn().strip().lower()
    if not reply:
        return default
    return reply in {"y", "yes"}
