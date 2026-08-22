"""Validated non-secret account metadata models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProviderName = Literal["claude", "codex"]
AccountState = Literal[
    "logged_out", "ready", "reauth_required", "unsupported", "error"
]


@dataclass(frozen=True)
class ProviderIdentity:
    display_name: str | None
    email: str | None
    plan: str | None


@dataclass(frozen=True)
class ManagedAccount:
    id: str
    provider: ProviderName
    label: str
    state: AccountState
    identity: ProviderIdentity
    created_at: str
