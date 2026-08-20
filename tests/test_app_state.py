from __future__ import annotations

import json

import pytest

from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState, AppStateError, AppStateStore
from dotsync.private_fs import UnsafePrivatePath


def test_state_store_returns_empty_state_when_no_state_file_exists(tmp_path):
    store = AppStateStore(AppPaths.for_home(tmp_path))

    assert store.load() == AppState()


def test_state_store_persists_sync_directory_with_schema(tmp_path):
    paths = AppPaths.for_home(tmp_path)
    store = AppStateStore(paths)
    state = AppState(sync_dir="/tmp/configs")

    store.save(state)

    assert store.load() == state
    assert json.loads((paths.root / "state.json").read_text()) == {
        "schema_version": 1,
        "sync_dir": "/tmp/configs",
    }


def test_state_store_rejects_unsupported_schema(tmp_path):
    paths = AppPaths.for_home(tmp_path)
    paths.root.mkdir(parents=True)
    (paths.root / "state.json").write_text('{"schema_version": 2}')

    with pytest.raises(AppStateError, match="unsupported app state schema"):
        AppStateStore(paths).load()


def test_state_store_rejects_broken_symlink_instead_of_treating_it_as_missing(tmp_path):
    paths = AppPaths.for_home(tmp_path)
    paths.root.mkdir(parents=True)
    (paths.root / "state.json").symlink_to(tmp_path / "missing.json")

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        AppStateStore(paths).load()
