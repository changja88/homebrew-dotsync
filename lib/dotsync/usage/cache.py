"""Validated, non-secret persistence for subscription usage snapshots."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

from dotsync.app_paths import AppPaths
from dotsync.private_fs import atomic_write_json, read_private_json, remove_private_tree

from .model import UsageSnapshot, UsageWindow


class UsageCacheError(ValueError):
    """Raised when a usage cache entry cannot be safely understood."""


_ROOT_FIELDS = frozenset(
    {
        "account_id",
        "provider",
        "windows",
        "observed_at",
        "source",
        "provider_version",
    }
)
_WINDOW_FIELDS = frozenset(
    {
        "name",
        "limit_id",
        "label",
        "used_percent",
        "duration_minutes",
        "resets_at",
    }
)


class UsageCache:
    """Store one validated snapshot per managed account under ``AppPaths``."""

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths

    def save(self, snapshot: UsageSnapshot) -> None:
        if type(snapshot) is not UsageSnapshot:
            raise UsageCacheError("usage cache snapshot has an unsupported type")
        account_id = _validate_account_id(snapshot.account_id)
        atomic_write_json(
            self._cache_file(account_id),
            _encode_snapshot(snapshot),
            root=self._paths.root,
        )

    def load(self, account_id: str) -> UsageSnapshot | None:
        validated_id = _validate_account_id(account_id)
        invalid_json = False
        try:
            data = read_private_json(
                self._cache_file(validated_id),
                root=self._paths.root,
            )
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            invalid_json = True
        if invalid_json:
            raise UsageCacheError("usage cache contains invalid JSON") from None

        snapshot = _decode_snapshot(data)
        if snapshot.account_id != validated_id:
            raise UsageCacheError("usage cache account id does not match its location")
        return snapshot

    def delete(self, account_id: str) -> None:
        validated_id = _validate_account_id(account_id)
        try:
            remove_private_tree(
                self._cache_root(validated_id),
                allowed_root=self._paths.usage,
            )
        except FileNotFoundError:
            return

    def _cache_root(self, account_id: str) -> Path:
        return self._paths.usage / account_id

    def _cache_file(self, account_id: str) -> Path:
        return self._cache_root(account_id) / "snapshot.json"


def _validate_account_id(account_id: Any) -> str:
    if type(account_id) is not str:
        raise UsageCacheError("usage cache account id must be a canonical UUID")
    try:
        parsed = uuid.UUID(account_id)
    except ValueError:
        raise UsageCacheError(
            "usage cache account id must be a canonical UUID"
        ) from None
    if str(parsed) != account_id:
        raise UsageCacheError("usage cache account id must be a canonical UUID")
    return account_id


def _decode_snapshot(data: Any) -> UsageSnapshot:
    if type(data) is not dict or set(data) != _ROOT_FIELDS:
        raise UsageCacheError("unsupported usage cache schema")
    raw_windows = data["windows"]
    if type(raw_windows) is not list:
        raise UsageCacheError("unsupported usage cache schema")

    windows = tuple(_decode_window(window) for window in raw_windows)
    invalid_model = False
    snapshot: UsageSnapshot | None = None
    try:
        snapshot = UsageSnapshot(
            account_id=_require_string(data["account_id"]),
            provider=cast(Any, _require_string(data["provider"])),
            windows=windows,
            observed_at=_require_string(data["observed_at"]),
            source=cast(Any, _require_string(data["source"])),
            provider_version=_require_string(data["provider_version"]),
        )
        _validate_account_id(snapshot.account_id)
    except (TypeError, ValueError):
        invalid_model = True
    if invalid_model or snapshot is None:
        raise UsageCacheError("unsupported usage cache schema") from None
    return snapshot


def _decode_window(data: Any) -> UsageWindow:
    if type(data) is not dict or set(data) != _WINDOW_FIELDS:
        raise UsageCacheError("unsupported usage cache window schema")
    if type(data["used_percent"]) not in {int, float}:
        raise UsageCacheError("unsupported usage cache window schema")
    if type(data["duration_minutes"]) is not int:
        raise UsageCacheError("unsupported usage cache window schema")
    label = data["label"]
    resets_at = data["resets_at"]
    if label is not None and type(label) is not str:
        raise UsageCacheError("unsupported usage cache window schema")
    if resets_at is not None and type(resets_at) is not str:
        raise UsageCacheError("unsupported usage cache window schema")

    invalid_model = False
    window: UsageWindow | None = None
    try:
        window = UsageWindow(
            name=cast(Any, _require_string(data["name"])),
            limit_id=_require_string(data["limit_id"]),
            label=label,
            used_percent=data["used_percent"],
            duration_minutes=data["duration_minutes"],
            resets_at=resets_at,
        )
    except (TypeError, ValueError):
        invalid_model = True
    if invalid_model or window is None:
        raise UsageCacheError("unsupported usage cache window schema") from None
    return window


def _require_string(value: Any) -> str:
    if type(value) is not str:
        raise UsageCacheError("unsupported usage cache schema")
    return value


def _encode_snapshot(snapshot: UsageSnapshot) -> dict[str, object]:
    return {
        "account_id": snapshot.account_id,
        "provider": snapshot.provider,
        "windows": [_encode_window(window) for window in snapshot.windows],
        "observed_at": snapshot.observed_at,
        "source": snapshot.source,
        "provider_version": snapshot.provider_version,
    }


def _encode_window(window: UsageWindow) -> dict[str, object]:
    return {
        "name": window.name,
        "limit_id": window.limit_id,
        "label": window.label,
        "used_percent": window.used_percent,
        "duration_minutes": window.duration_minutes,
        "resets_at": window.resets_at,
    }
