"""Persistence for DotSync's small application-level state."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from dotsync.app_paths import AppPaths
from dotsync.private_fs import atomic_write_json, read_private_json


class AppStateError(ValueError):
    """Raised when persisted application state cannot be understood."""


@dataclass(frozen=True)
class AppState:
    sync_dir: str | None = None


class AppStateStore:
    def __init__(self, paths: AppPaths) -> None:
        self._path = paths.root / "state.json"
        self._root = paths.root

    def load(self) -> AppState:
        try:
            data = read_private_json(self._path, root=self._root)
        except FileNotFoundError:
            return AppState()
        if not isinstance(data, dict):
            raise AppStateError("unsupported app state schema")
        schema_version = data.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise AppStateError("unsupported app state schema")
        return AppState(sync_dir=data.get("sync_dir"))

    def save(self, state: AppState) -> None:
        atomic_write_json(
            self._path,
            {"schema_version": 1, **asdict(state)},
            root=self._root,
        )
