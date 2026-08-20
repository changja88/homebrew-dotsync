from __future__ import annotations

import json
import os
import stat

import pytest

from dotsync.private_fs import (
    UnsafePrivatePath,
    atomic_write_json,
    ensure_private_dir,
    read_private_json,
    remove_private_tree,
)


def test_ensure_private_dir_creates_private_ancestor_chain(tmp_path):
    directory = tmp_path / "private" / "account"

    ensure_private_dir(directory, root=tmp_path)

    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o700


def test_ensure_private_dir_rejects_symlink_parent(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "private").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        ensure_private_dir(tmp_path / "private" / "account", root=tmp_path)

    assert not (outside / "account").exists()


def test_atomic_write_json_creates_private_file(tmp_path):
    target = tmp_path / "state.json"

    atomic_write_json(
        target,
        {"schema_version": 1, "sync_dir": "/tmp/configs"},
        root=tmp_path,
    )

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {
        "schema_version": 1,
        "sync_dir": "/tmp/configs",
    }


def test_atomic_write_json_rejects_symlink_target(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep": true}')
    target = tmp_path / "state.json"
    target.symlink_to(outside)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        atomic_write_json(target, {"schema_version": 1}, root=tmp_path)

    assert json.loads(outside.read_text()) == {"keep": True}


def test_read_private_json_rejects_symlink_target(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{}')
    target = tmp_path / "state.json"
    target.symlink_to(outside)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        read_private_json(target, root=tmp_path)


def test_remove_private_tree_rejects_nested_symlink(tmp_path):
    root = tmp_path / "account"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        remove_private_tree(root, allowed_root=tmp_path)

    assert outside.exists()
    assert root.exists()


def test_remove_private_tree_removes_regular_tree_only_below_allowed_root(tmp_path):
    root = tmp_path / "accounts" / "claude" / "account"
    root.mkdir(parents=True)
    (root / "state.json").write_text("{}")
    (root / "home").mkdir()
    (root / "home" / "config.json").write_text("{}")

    remove_private_tree(root, allowed_root=tmp_path / "accounts")

    assert not root.exists()
    assert (tmp_path / "accounts").exists()


def test_remove_private_tree_requires_strict_descendant_of_allowed_root(tmp_path):
    with pytest.raises(UnsafePrivatePath, match="strict descendant"):
        remove_private_tree(tmp_path, allowed_root=tmp_path)
