"""UI primitives for the Serena agent launcher TUI.

This module provides a single-responsibility split of the launcher's screen
concerns:

* State    -- BoxModel / Item dataclasses (this section)
* Renderer -- BoxModel -> ANSI text + in-place updates (later task)
* Progress -- spinner ticker thread (later task)
* Prompt   -- yes/no confirmation (later task)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
