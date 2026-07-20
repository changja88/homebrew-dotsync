import json

import pytest

from local_dev.serena_mcp_management.memory_management import (
    scan_memory_inventory,
)


def test_codex_inventory_scans_only_memories_under_all_known_homes(tmp_path):
    home = tmp_path / "home"
    active = tmp_path / "active-codex"
    orca = tmp_path / "orca-codex"
    for root, text in (
        (home / ".codex", "default"),
        (active, "active"),
        (orca, "orca"),
    ):
        (root / "memories").mkdir(parents=True)
        (root / "memories/MEMORY.md").write_text(text)
        (root / "memories_extensions/chronicle").mkdir(parents=True)
        (root / "memories_extensions/chronicle/keep.md").write_text("keep")

    inventory = scan_memory_inventory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
    )

    assert {store.path for store in inventory.stores} == {
        home / ".codex/memories",
        active / "memories",
        orca / "memories",
    }
    assert inventory.file_count == 3
    assert inventory.scope == "all known Codex homes"
    assert all(
        "memories_extensions" not in str(store.path)
        for store in inventory.stores
    )


def test_codex_inventory_rejects_symlinked_active_home(tmp_path):
    home = tmp_path / "home"
    active_target = tmp_path / "active-target"
    store = active_target / "memories"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("memory")
    active_link = tmp_path / "active-link"
    active_link.symlink_to(active_target, target_is_directory=True)

    inventory = scan_memory_inventory(
        client="codex",
        home=home,
        codex_home=active_link,
        orca_codex_home=tmp_path / "orca",
    )

    assert inventory.stores == ()
    assert any("symlink" in warning for warning in inventory.warnings)


def test_codex_inventory_rejects_parent_traversal_before_symlink_inspection(
    tmp_path,
):
    linked_target = tmp_path / "linked-target/nested"
    linked_target.mkdir(parents=True)
    active_link = tmp_path / "active-link"
    active_link.symlink_to(linked_target, target_is_directory=True)

    inventory = scan_memory_inventory(
        client="codex",
        home=tmp_path / "home",
        codex_home=active_link / ".." / "active",
        orca_codex_home=tmp_path / "orca",
    )

    assert inventory.stores == ()
    assert any("parent traversal" in warning for warning in inventory.warnings)


def test_codex_inventory_rejects_parent_traversal_after_missing_component(
    tmp_path,
):
    target = tmp_path / "target"
    store = target / "memories"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("memory")
    active_link = tmp_path / "active-link"
    active_link.symlink_to(target, target_is_directory=True)

    inventory = scan_memory_inventory(
        client="codex",
        home=tmp_path / "home",
        codex_home=tmp_path / "missing" / ".." / active_link.name,
        orca_codex_home=tmp_path / "orca",
    )

    assert inventory.stores == ()
    assert any("parent traversal" in warning for warning in inventory.warnings)


def test_claude_inventory_finds_all_project_memory_and_custom_store(tmp_path):
    config = tmp_path / ".claude"
    first = config / "projects/repo-a/memory"
    second = config / "projects/repo-b/memory"
    custom = tmp_path / "custom-memory"
    for store in (first, second, custom):
        store.mkdir(parents=True)
        (store / "MEMORY.md").write_text("memory")
    (config / "agent-memory/reviewer").mkdir(parents=True)
    (config / "settings.json").write_text(
        json.dumps(
            {
                "autoMemoryEnabled": True,
                "autoMemoryDirectory": str(custom),
            }
        )
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert {store.path for store in inventory.stores} == {
        first,
        second,
        custom,
    }
    assert inventory.file_count == 3
    assert inventory.scope == "all Claude auto-memory stores"


def test_claude_inventory_rejects_symlinked_config_root(tmp_path):
    config_target = tmp_path / "config-target"
    store = config_target / "projects/repo/memory"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("memory")
    config_link = tmp_path / "config-link"
    config_link.symlink_to(config_target, target_is_directory=True)

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config_link,
    )

    assert inventory.stores == ()
    assert any("symlink" in warning for warning in inventory.warnings)


def test_claude_inventory_rejects_nonempty_custom_store_without_marker(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    custom = tmp_path / "custom-memory"
    custom.mkdir()
    (custom / "notes.txt").write_text("not auto-memory")
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(custom)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("MEMORY.md" in warning for warning in inventory.warnings)


def test_claude_inventory_rejects_custom_store_with_parent_traversal(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "MEMORY.md").write_text("memory")
    linked_target = tmp_path / "linked-target/nested"
    linked_target.mkdir(parents=True)
    memory_link = tmp_path / "memory-link"
    memory_link.symlink_to(linked_target, target_is_directory=True)
    configured_path = memory_link / ".." / victim.name
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(configured_path)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("parent traversal" in warning for warning in inventory.warnings)


def test_claude_inventory_accepts_empty_custom_store(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    custom = tmp_path / "custom-memory"
    custom.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(custom)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert tuple(store.path for store in inventory.stores) == (custom,)
    assert inventory.file_count == 0


def test_claude_inventory_does_not_require_marker_for_project_store(tmp_path):
    config = tmp_path / ".claude"
    store = config / "projects/repo/memory"
    store.mkdir(parents=True)
    (store / "notes.txt").write_text("project memory")

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert tuple(item.path for item in inventory.stores) == (store,)
    assert inventory.file_count == 1


@pytest.mark.parametrize(
    ("settings", "warning"),
    [
        ("{not-json", "invalid Claude settings"),
        (
            json.dumps({"autoMemoryDirectory": "relative"}),
            "must be absolute",
        ),
        (
            json.dumps({"autoMemoryDirectory": "/"}),
            "unsafe broad path",
        ),
    ],
)
def test_claude_inventory_reports_unsafe_settings(
    tmp_path,
    settings,
    warning,
):
    config = tmp_path / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(settings)

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert any(warning in item for item in inventory.warnings)


def test_inventory_rejects_symlink_store(tmp_path):
    config = tmp_path / ".claude"
    project = config / "projects/repo"
    project.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "memory").symlink_to(outside, target_is_directory=True)

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("symlink" in item for item in inventory.warnings)


def test_inventory_rejects_store_below_symlinked_parent(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    outside = tmp_path / "outside"
    store = outside / "memory"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("memory")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(outside, target_is_directory=True)
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(linked_parent / "memory")})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("symlink" in item for item in inventory.warnings)


def test_inventory_does_not_follow_symlinks_inside_store(tmp_path):
    store = tmp_path / ".codex/memories"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("memory")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.md").write_text("outside")
    (store / "linked").symlink_to(outside, target_is_directory=True)

    inventory = scan_memory_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
    )

    assert inventory.file_count == 1
    assert any("symlink" in item for item in inventory.warnings)


def test_inventory_reports_memory_path_with_wrong_file_type(tmp_path):
    memory_path = tmp_path / ".codex/memories"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text("not a directory")

    inventory = scan_memory_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
    )

    assert inventory.stores == ()
    assert any("not a directory" in item for item in inventory.warnings)


def test_inventory_reports_settings_with_wrong_file_type(tmp_path):
    config = tmp_path / ".claude"
    (config / "settings.json").mkdir(parents=True)

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("not a regular file" in item for item in inventory.warnings)
