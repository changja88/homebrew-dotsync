"""Private filesystem locations for DotSync account state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Locations owned by DotSync under one macOS home directory."""

    root: Path

    @classmethod
    def for_home(cls, home: Path) -> "AppPaths":
        return cls(home / "Library" / "Application Support" / "DotSync")

    @property
    def accounts(self) -> Path:
        return self.root / "accounts"

    @property
    def usage(self) -> Path:
        return self.root / "usage"

    def account_root(self, provider: str, account_id: str) -> Path:
        if provider not in {"claude", "codex"}:
            raise ValueError("unsupported provider")
        try:
            parsed = uuid.UUID(account_id)
        except (AttributeError, ValueError) as error:
            raise ValueError("invalid account id") from error
        if str(parsed) != account_id:
            raise ValueError("invalid account id")
        return self.accounts / provider / account_id

    def account_home(self, provider: str, account_id: str) -> Path:
        return self.account_root(provider, account_id) / "home"

    def account_probe(self, provider: str, account_id: str) -> Path:
        return self.account_root(provider, account_id) / "probe"

    def account_tmp(self, provider: str, account_id: str) -> Path:
        return self.account_root(provider, account_id) / "tmp"


def default_data_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "DotSync"
