"""Validated, provider-neutral subscription usage values."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DANGEROUS_BIDI_CLASSES = frozenset(
    {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
)
_DANGEROUS_INVISIBLE_CODE_POINTS = frozenset(
    {
        0x00AD,  # soft hyphen
        0x061C,  # Arabic letter mark
        0x180E,  # Mongolian vowel separator
        0x200B,  # zero-width space
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x2028,  # line separator
        0x2029,  # paragraph separator
        0x2060,  # word joiner
        0x2061,  # function application
        0x2062,  # invisible times
        0x2063,  # invisible separator
        0x2064,  # invisible plus
        0xFEFF,  # zero-width no-break space
        0xFFF9,  # interlinear annotation anchor
        0xFFFA,  # interlinear annotation separator
        0xFFFB,  # interlinear annotation terminator
    }
).union(range(0x206A, 0x2070), range(0x1BCA0, 0x1BCA4))


def _require_rfc3339(value: object, field: str) -> None:
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        raise ValueError(f"{field} must be an RFC 3339 timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from error


def _reject_unsafe_display_characters(value: str, field: str) -> None:
    for character in value:
        if (
            unicodedata.category(character) in {"Cc", "Cs"}
            or unicodedata.bidirectional(character) in _DANGEROUS_BIDI_CLASSES
            or ord(character) in _DANGEROUS_INVISIBLE_CODE_POINTS
        ):
            raise ValueError(
                f"{field} must not contain control or unsafe formatting characters"
            )


@dataclass(frozen=True)
class UsageWindow:
    name: Literal["five_hour", "seven_day", "other"]
    limit_id: str
    label: str | None
    used_percent: float
    duration_minutes: int
    resets_at: str | None

    def __post_init__(self) -> None:
        if self.name not in {"five_hour", "seven_day", "other"}:
            raise ValueError("unsupported usage window name")
        if not isinstance(self.limit_id, str) or not self.limit_id.strip():
            raise ValueError("limit id must not be blank")
        _reject_unsafe_display_characters(self.limit_id, "limit id")
        if self.label is not None and not isinstance(self.label, str):
            raise TypeError("usage window label must be a string or None")
        if self.label is not None:
            _reject_unsafe_display_characters(self.label, "usage window label")
        if type(self.used_percent) not in {int, float}:
            raise TypeError("usage percentage must be a number")
        try:
            percentage = float(self.used_percent)
        except OverflowError as error:
            raise ValueError(
                "usage percentage must be between 0 and 100"
            ) from error
        if not math.isfinite(percentage) or not 0.0 <= percentage <= 100.0:
            raise ValueError("usage percentage must be between 0 and 100")
        object.__setattr__(self, "used_percent", percentage)
        if type(self.duration_minutes) is not int:
            raise TypeError("usage duration must be an integer")
        if self.duration_minutes <= 0:
            raise ValueError("usage duration must be positive")
        if self.resets_at is not None:
            _require_rfc3339(self.resets_at, "reset time")


@dataclass(frozen=True)
class UsageSnapshot:
    account_id: str
    provider: Literal["claude", "codex"]
    windows: tuple[UsageWindow, ...]
    observed_at: str
    source: Literal["codex_app_server", "claude_usage"]
    provider_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError("account id must not be blank")
        if self.provider not in {"claude", "codex"}:
            raise ValueError("unsupported usage provider")
        if type(self.windows) is not tuple or any(
            not isinstance(window, UsageWindow) for window in self.windows
        ):
            raise TypeError("usage windows must be a tuple of UsageWindow values")
        _require_rfc3339(self.observed_at, "observation time")
        expected_source = {
            "claude": "claude_usage",
            "codex": "codex_app_server",
        }[self.provider]
        if self.source != expected_source:
            raise ValueError("usage source does not match provider")
        if (
            not isinstance(self.provider_version, str)
            or not self.provider_version.strip()
        ):
            raise ValueError("provider version must not be blank")
