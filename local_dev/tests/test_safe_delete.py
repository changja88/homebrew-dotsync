from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.safe_delete import (
    SafeDeleteError,
    delete_directory_tree,
    directory_is_empty_no_follow,
    read_json_file_no_follow,
    remove_json_object_key,
    tree_digest_no_follow,
)


def test_descriptor_delete_removes_tree_without_following_child_symlink(tmp_path):
    target = tmp_path / "config/plans"
    nested = target / "nested"
    nested.mkdir(parents=True)
    (nested / "trace.txt").write_text("generated", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (target / "outside-link").symlink_to(outside, target_is_directory=True)

    delete_directory_tree(target)

    assert not target.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_descriptor_delete_rejects_ancestor_swap_before_mutation(tmp_path):
    config = tmp_path / "config"
    target = config / "plans"
    target.mkdir(parents=True)
    original = target / "original.txt"
    original.write_text("original", encoding="utf-8")
    outside_config = tmp_path / "outside-config"
    outside_target = outside_config / "plans"
    outside_target.mkdir(parents=True)
    sentinel = outside_target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    detached = tmp_path / "detached-config"

    def swap_ancestor() -> None:
        config.rename(detached)
        config.symlink_to(outside_config, target_is_directory=True)

    with pytest.raises(SafeDeleteError, match="changed"):
        delete_directory_tree(target, before_mutation=swap_ancestor)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (detached / "plans/original.txt").read_text(encoding="utf-8") == (
        "original"
    )


def test_descriptor_delete_unlinks_allowlisted_final_symlink_only(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    linked_target = tmp_path / "config/plans"
    linked_target.parent.mkdir()
    linked_target.symlink_to(outside, target_is_directory=True)

    delete_directory_tree(linked_target, allow_final_symlink=True)

    assert not linked_target.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_descriptor_delete_rejects_final_symlink_for_memory_store(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    linked_target = tmp_path / "memory"
    linked_target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeDeleteError, match="symlink"):
        delete_directory_tree(linked_target)

    assert linked_target.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_descriptor_json_rewrite_removes_only_generated_key(tmp_path):
    target = tmp_path / "config/backups/.claude.json.backup.1"
    target.parent.mkdir(parents=True)
    target.write_text(
        '{"oauthAccount":{"id":"user"},"theme":"dark",'
        '"projects":{"/repo":{"lastSessionId":"session-1"}}}',
        encoding="utf-8",
    )

    changed = remove_json_object_key(target, key="projects")

    assert changed is True
    assert target.read_text(encoding="utf-8") == (
        '{"oauthAccount":{"id":"user"},"theme":"dark"}'
    )


def test_descriptor_json_rewrite_rejects_ancestor_swap(tmp_path):
    config = tmp_path / "config"
    target = config / "backups/.claude.json.backup.1"
    target.parent.mkdir(parents=True)
    target.write_text('{"projects":{"/repo":{}}}', encoding="utf-8")
    outside_config = tmp_path / "outside-config"
    outside_target = outside_config / "backups/.claude.json.backup.1"
    outside_target.parent.mkdir(parents=True)
    outside_target.write_text('{"projects":{"/outside":{}}}', encoding="utf-8")
    detached = tmp_path / "detached-config"

    def swap_ancestor() -> None:
        config.rename(detached)
        config.symlink_to(outside_config, target_is_directory=True)

    with pytest.raises(SafeDeleteError, match="changed"):
        remove_json_object_key(
            target,
            key="projects",
            before_mutation=swap_ancestor,
        )

    assert outside_target.read_text(encoding="utf-8") == (
        '{"projects":{"/outside":{}}}'
    )
    assert (detached / "backups/.claude.json.backup.1").read_text(
        encoding="utf-8"
    ) == '{"projects":{"/repo":{}}}'


def test_descriptor_json_rewrite_rejects_in_place_update(tmp_path):
    target = tmp_path / "config/backups/.claude.json.backup.1"
    target.parent.mkdir(parents=True)
    target.write_text(
        '{"theme":"dark","projects":{"/repo":{}}}',
        encoding="utf-8",
    )

    def update_same_file() -> None:
        target.write_text(
            '{"theme":"dark","newPreference":true,'
            '"projects":{"/repo":{}}}',
            encoding="utf-8",
        )

    with pytest.raises(SafeDeleteError, match="changed"):
        remove_json_object_key(
            target,
            key="projects",
            before_mutation=update_same_file,
        )

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "theme": "dark",
        "newPreference": True,
        "projects": {"/repo": {}},
    }


def test_descriptor_json_read_rejects_ancestor_swap(tmp_path):
    config = tmp_path / "config"
    target = config / "remote-settings.json"
    config.mkdir()
    target.write_text('{"theme":"dark"}', encoding="utf-8")
    outside_config = tmp_path / "outside-config"
    outside_config.mkdir()
    outside_target = outside_config / "remote-settings.json"
    outside_target.write_text(
        '{"autoMemoryDirectory":"/outside"}',
        encoding="utf-8",
    )
    detached = tmp_path / "detached-config"

    def swap_ancestor() -> None:
        config.rename(detached)
        config.symlink_to(outside_config, target_is_directory=True)

    with pytest.raises(SafeDeleteError, match="changed"):
        read_json_file_no_follow(target, before_read=swap_ancestor)

    assert outside_target.read_text(encoding="utf-8") == (
        '{"autoMemoryDirectory":"/outside"}'
    )


def test_descriptor_empty_check_rejects_target_swap(tmp_path):
    target = tmp_path / "config/projects"
    target.mkdir(parents=True)
    detached = tmp_path / "detached-projects"
    outside = tmp_path / "outside"
    outside.mkdir()

    def swap_target() -> None:
        target.rename(detached)
        target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeDeleteError, match="changed"):
        directory_is_empty_no_follow(target, before_read=swap_target)


def test_descriptor_empty_check_rejects_content_created_after_listing(tmp_path):
    target = tmp_path / "config/projects"
    target.mkdir(parents=True)

    def create_residual() -> None:
        (target / "late.jsonl").write_text("conversation", encoding="utf-8")

    with pytest.raises(SafeDeleteError, match="changed"):
        directory_is_empty_no_follow(target, after_read=create_residual)


def test_tree_digest_rejects_entry_created_during_snapshot(tmp_path):
    target = tmp_path / "config/skills"
    target.mkdir(parents=True)
    skill = target / "personal.md"
    skill.write_text("before", encoding="utf-8")

    def create_entry() -> None:
        (target / "late.md").write_text("late", encoding="utf-8")

    with pytest.raises(SafeDeleteError, match="changed"):
        tree_digest_no_follow(target, after_read=create_entry)


def test_tree_digest_frames_file_content_and_directory_structure(tmp_path):
    flat = tmp_path / "flat"
    nested = tmp_path / "nested"
    flat.mkdir()
    (flat / "group").write_text("a", encoding="utf-8")
    (flat / "item").write_text("b", encoding="utf-8")
    nested_item = nested / "group/item"
    nested_item.parent.mkdir(parents=True)
    nested_item.write_text("ab", encoding="utf-8")

    assert tree_digest_no_follow(flat) != tree_digest_no_follow(nested)
