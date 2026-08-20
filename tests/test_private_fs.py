from __future__ import annotations

import json
import os
import stat

import pytest

from dotsync.private_fs import (
    UnsafePrivatePath,
    atomic_write_json,
    ensure_private_dir,
    move_private_tree,
    read_private_json,
    remove_private_tree,
    validate_private_tree,
)


def test_ensure_private_dir_creates_private_ancestor_chain(tmp_path):
    directory = tmp_path / "private" / "account"

    ensure_private_dir(directory, root=tmp_path)

    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o700


def test_ensure_private_dir_secures_existing_managed_ancestors(tmp_path):
    root = tmp_path / "private"
    existing = root / "accounts"
    existing.mkdir(parents=True)
    existing.chmod(0o755)

    ensure_private_dir(existing / "claude" / "account", root=root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(existing.stat().st_mode) == 0o700
    assert stat.S_IMODE((existing / "claude").stat().st_mode) == 0o700


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


def test_atomic_write_json_keeps_mutation_in_open_parent_after_path_is_replaced(
    tmp_path, monkeypatch
):
    root = tmp_path / "private"
    outside = tmp_path / "outside"
    moved = tmp_path / "moved-private"
    root.mkdir()
    outside.mkdir()
    real_replace = os.replace

    def replace_after_parent_swap(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        root.rename(moved)
        root.symlink_to(outside, target_is_directory=True)
        real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", replace_after_parent_swap)

    atomic_write_json(root / "state.json", {"schema_version": 1}, root=root)

    assert not (outside / "state.json").exists()
    assert json.loads((moved / "state.json").read_text()) == {"schema_version": 1}


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


def test_remove_private_tree_does_not_follow_parent_swapped_after_scan(
    tmp_path, monkeypatch
):
    allowed_root = tmp_path / "accounts"
    root = allowed_root / "account"
    home = root / "home"
    moved_home = root / "moved-home"
    outside = tmp_path / "outside"
    home.mkdir(parents=True)
    outside.mkdir()
    (home / "config.json").write_text('{"private": true}')
    outside_config = outside / "config.json"
    outside_config.write_text('{"outside": true}')
    real_unlink = os.unlink
    swapped = False

    def unlink_after_parent_swap(path, *, dir_fd=None):
        nonlocal swapped
        if not swapped:
            swapped = True
            home.rename(moved_home)
            home.symlink_to(outside, target_is_directory=True)
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink_after_parent_swap)

    with pytest.raises(OSError):
        remove_private_tree(root, allowed_root=allowed_root)

    assert json.loads(outside_config.read_text()) == {"outside": True}


def test_validate_private_tree_checks_every_entry_without_mutating(tmp_path):
    root = tmp_path / "private" / "account"
    root.mkdir(parents=True)
    (root / "home").mkdir()
    (root / "home" / "auth.json").write_text('{"keep": true}')

    assert validate_private_tree(root, allowed_root=tmp_path / "private") is True
    assert json.loads((root / "home" / "auth.json").read_text()) == {"keep": True}
    assert (
        validate_private_tree(
            tmp_path / "private" / "missing",
            allowed_root=tmp_path / "private",
        )
        is False
    )


def test_validate_private_tree_rejects_nested_symlink_without_mutating(tmp_path):
    root = tmp_path / "private" / "account"
    outside = tmp_path / "outside"
    root.mkdir(parents=True)
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("keep")
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        validate_private_tree(root, allowed_root=tmp_path / "private")

    assert sentinel.read_text() == "keep"
    assert root.exists()


def test_move_private_tree_atomically_stages_and_restores_validated_tree(tmp_path):
    private_root = tmp_path / "private"
    source = private_root / "accounts" / "account"
    staged = private_root / ".deletions" / "account" / "profile"
    source.mkdir(parents=True)
    (source / "home").mkdir()
    sentinel = source / "home" / "auth.json"
    sentinel.write_bytes(b"credential-bytes")

    assert move_private_tree(source, staged, allowed_root=private_root) is True
    assert not source.exists()
    assert (staged / "home" / "auth.json").read_bytes() == b"credential-bytes"

    assert move_private_tree(staged, source, allowed_root=private_root) is True
    assert not staged.exists()
    assert sentinel.read_bytes() == b"credential-bytes"


def test_move_private_tree_rejects_existing_destination_before_mutation(tmp_path):
    private_root = tmp_path / "private"
    source = private_root / "accounts" / "account"
    destination = private_root / ".deletions" / "account" / "profile"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "keep").write_text("source")
    (destination / "keep").write_text("destination")

    with pytest.raises(UnsafePrivatePath, match="destination"):
        move_private_tree(source, destination, allowed_root=private_root)

    assert (source / "keep").read_text() == "source"
    assert (destination / "keep").read_text() == "destination"


def test_move_private_tree_revalidates_contents_after_atomic_move(
    tmp_path, monkeypatch
):
    import dotsync.private_fs as private_fs

    root = tmp_path / "private"
    source = root / "source"
    destination = root / "staging" / "destination"
    outside = tmp_path / "outside"
    source.mkdir(parents=True)
    outside.mkdir()
    (source / "sentinel.txt").write_text("source")
    outside_sentinel = outside / "keep.txt"
    outside_sentinel.write_text("keep")
    real_rename = private_fs.os.rename
    injected = False

    def inject_symlink_after_move(src, dst, **kwargs):
        nonlocal injected
        real_rename(src, dst, **kwargs)
        if not injected and src == "source" and dst == "destination":
            injected = True
            (destination / "link").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(private_fs.os, "rename", inject_symlink_after_move)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        move_private_tree(source, destination, allowed_root=root)

    assert source.exists()
    assert not destination.exists()
    assert outside_sentinel.read_text() == "keep"
