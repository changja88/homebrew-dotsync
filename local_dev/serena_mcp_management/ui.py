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
