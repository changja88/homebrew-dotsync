"""Recoverable two-phase deletion for DotSync-owned account data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from dotsync.accounts import ManagedAccount, ProviderName
from dotsync.app_paths import AppPaths
from dotsync.private_fs import (
    atomic_write_json,
    ensure_private_dir,
    move_private_tree,
    read_private_json,
    remove_private_tree,
    validate_private_tree,
)


class DeletionRecoveryError(RuntimeError):
    """Raised when an interrupted deletion cannot be safely reconciled."""


class DeletionCleanupPending(RuntimeError):
    """Raised after metadata commit when staged data still needs scrubbing."""


_MANIFEST_FIELDS = frozenset({"account_id", "provider"})
_PROVIDERS = frozenset({"claude", "codex"})


@dataclass(frozen=True)
class AccountDeletion:
    """Stage account trees and use metadata presence as the commit oracle."""

    paths: AppPaths
    account_id: str
    provider: ProviderName

    @classmethod
    def begin(cls, paths: AppPaths, account: ManagedAccount) -> "AccountDeletion":
        deletion = cls(paths, account.id, account.provider)
        if validate_private_tree(deletion.root, allowed_root=paths.root):
            raise DeletionRecoveryError("account deletion already has staged data")
        ensure_private_dir(deletion.root, root=paths.root)
        atomic_write_json(
            deletion.manifest,
            {"account_id": account.id, "provider": account.provider},
            root=paths.root,
        )
        return deletion

    @classmethod
    def load(cls, paths: AppPaths, account_id: str) -> "AccountDeletion | None":
        root = paths.root / ".deletions" / account_id
        if not validate_private_tree(root, allowed_root=paths.root):
            return None
        try:
            data = read_private_json(root / "manifest.json", root=paths.root)
        except FileNotFoundError as error:
            raise DeletionRecoveryError(
                "account deletion staging is missing its manifest"
            ) from error
        provider = _decode_manifest(data, account_id)
        return cls(paths, account_id, provider)

    @property
    def root(self) -> Path:
        return self.paths.root / ".deletions" / self.account_id

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def staged_profile(self) -> Path:
        return self.root / "profile"

    @property
    def staged_cache(self) -> Path:
        return self.root / "cache"

    @property
    def profile(self) -> Path:
        return self.paths.account_root(self.provider, self.account_id)

    @property
    def cache(self) -> Path:
        return self.paths.usage / self.account_id

    def stage(self) -> None:
        try:
            move_private_tree(
                self.profile,
                self.staged_profile,
                allowed_root=self.paths.root,
            )
            move_private_tree(
                self.cache,
                self.staged_cache,
                allowed_root=self.paths.root,
            )
        except BaseException:
            self.restore()
            raise

    def restore(self) -> None:
        """Restore staged trees while metadata still declares the account live."""
        move_private_tree(
            self.staged_cache,
            self.cache,
            allowed_root=self.paths.root,
        )
        move_private_tree(
            self.staged_profile,
            self.profile,
            allowed_root=self.paths.root,
        )
        self._cleanup()

    def cleanup_committed(self) -> None:
        """Scrub staged data after the account metadata commit point."""
        try:
            self._cleanup()
        except Exception:
            raise DeletionCleanupPending(
                "account deletion committed; staged cleanup remains pending"
            ) from None

    def _cleanup(self) -> None:
        try:
            remove_private_tree(
                self.root,
                allowed_root=self.paths.root / ".deletions",
            )
        except FileNotFoundError:
            return


def _decode_manifest(data: Any, expected_id: str) -> ProviderName:
    if type(data) is not dict or set(data) != _MANIFEST_FIELDS:
        raise DeletionRecoveryError("unsupported account deletion manifest")
    if data["account_id"] != expected_id:
        raise DeletionRecoveryError("account deletion manifest id does not match")
    provider = data["provider"]
    if type(provider) is not str or provider not in _PROVIDERS:
        raise DeletionRecoveryError("unsupported account deletion provider")
    return cast(ProviderName, provider)
