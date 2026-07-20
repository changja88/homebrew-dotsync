import json
import shutil
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.memory_management import (
    delete_all_memory,
    running_client_processes,
    scan_memory_inventory,
)


PS_IDENTITY_COMMAND = ["/bin/ps", "-axo", "pid=,ppid=,comm="]
PS_ARGUMENT_COMMAND = ["/bin/ps", "-axo", "pid=,args="]


def _split_legacy_ps(output):
    identity_lines = []
    argument_lines = []
    for line in output.splitlines():
        fields = line.strip().split(maxsplit=3)
        if len(fields) < 3:
            continue
        pid, ppid, executable = fields[:3]
        arguments = fields[3] if len(fields) == 4 else executable
        identity_lines.append(f"{pid} {ppid} {executable}")
        argument_lines.append(f"{pid} {arguments}")
    identity_output = "\n".join(identity_lines)
    argument_output = "\n".join(argument_lines)
    if identity_output:
        identity_output += "\n"
        argument_output += "\n"
    return identity_output, argument_output


def fake_ps(output, *, args_output=None, returncode=0):
    if args_output is None:
        identity_output, argument_output = _split_legacy_ps(output)
    else:
        identity_output = output
        argument_output = args_output

    def run_command(command, **kwargs):
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "check": False,
        }
        if command == PS_IDENTITY_COMMAND:
            command_output = identity_output
        elif command == PS_ARGUMENT_COMMAND:
            command_output = argument_output
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=command_output,
            stderr="",
        )

    return run_command


def build_codex_memory_fixture(tmp_path):
    home = tmp_path / "home"
    active = tmp_path / "active-codex"
    orca = tmp_path / "orca-codex"
    for root in (home / ".codex", active, orca):
        store = root / "memories"
        store.mkdir(parents=True)
        (store / "MEMORY.md").write_text("memory")
    return home, active, orca


def case_insensitive_alias(path):
    alias = path.with_name(path.name.swapcase())
    try:
        aliases_same_file = alias.samefile(path)
    except OSError:
        aliases_same_file = False
    if not aliases_same_file:
        pytest.skip("filesystem does not provide case-insensitive path aliases")
    return alias


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


def test_codex_inventory_deduplicates_case_aliases_of_one_home(tmp_path):
    home = tmp_path / "UserHome"
    store = home / ".codex/memories"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("memory")
    active_home = case_insensitive_alias(home) / ".codex"
    assert active_home.samefile(home / ".codex")

    inventory = scan_memory_inventory(
        client="codex",
        home=home,
        codex_home=active_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert tuple(item.path for item in inventory.stores) == (store,)
    assert inventory.file_count == 1


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


def test_claude_inventory_deduplicates_project_and_custom_case_aliases(
    tmp_path,
):
    config = tmp_path / ".claude"
    project = config / "projects/Repo/memory"
    project.mkdir(parents=True)
    (project / "MEMORY.md").write_text("memory")
    alias = case_insensitive_alias(project.parent) / "memory"
    assert alias.samefile(project)
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(alias)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert tuple(item.path for item in inventory.stores) == (project,)
    assert inventory.file_count == 1


def test_claude_markerless_project_exact_custom_duplicate_skips_marker_check(
    tmp_path,
):
    home = tmp_path / "home"
    config = home / ".claude"
    project = config / "projects/repo/memory"
    project.mkdir(parents=True)
    (project / "notes.md").write_text("project memory")
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(project)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config,
    )

    assert tuple(store.path for store in inventory.stores) == (project,)
    assert inventory.file_count == 1
    assert not any(
        "requires MEMORY.md" in warning for warning in inventory.warnings
    )


def test_claude_markerless_project_alias_skips_custom_marker_validation(
    tmp_path,
):
    home = tmp_path / "home"
    config = home / ".claude"
    project = config / "projects/Repo/memory"
    project.mkdir(parents=True)
    (project / "notes.md").write_text("project memory")
    alias = case_insensitive_alias(project.parent) / "memory"
    assert alias.samefile(project)
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(alias)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config,
    )
    remove_calls = []

    def fake_remove_tree(path):
        assert path == project
        remove_calls.append(path)
        shutil.rmtree(path)

    result = delete_all_memory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config,
        run_command=fake_ps(""),
        remove_tree=fake_remove_tree,
    )

    assert tuple(store.path for store in inventory.stores) == (project,)
    assert inventory.file_count == 1
    assert not any(
        "requires MEMORY.md" in warning for warning in inventory.warnings
    )
    assert result.succeeded
    assert result.deleted_stores == 1
    assert result.deleted_files == 1
    assert remove_calls == [project]
    assert not project.exists()


def test_claude_inventory_and_delete_refuse_case_alias_of_home(tmp_path):
    home = tmp_path / "UserHome"
    home.mkdir()
    (home / "MEMORY.md").write_text("unrelated home file")
    alias = case_insensitive_alias(home)
    config = tmp_path / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(alias)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )
    remove_calls = []
    result = delete_all_memory(
        client="claude",
        home=home,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
        run_command=fake_ps(""),
        remove_tree=lambda path: remove_calls.append(path),
    )

    assert inventory.stores == ()
    assert any("unsafe broad path" in warning for warning in inventory.warnings)
    assert not result.succeeded
    assert remove_calls == []
    assert (home / "MEMORY.md").read_text() == "unrelated home file"


def test_claude_inventory_retains_lexical_guard_if_identity_comparison_misses(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / "MEMORY.md").write_text("unrelated home file")
    config = tmp_path / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(home)})
    )
    monkeypatch.setattr(Path, "samefile", lambda path, other: False)

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )
    remove_calls = []
    result = delete_all_memory(
        client="claude",
        home=home,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
        run_command=fake_ps(""),
        remove_tree=lambda path: remove_calls.append(path),
    )

    assert inventory.stores == ()
    assert any("unsafe broad path" in warning for warning in inventory.warnings)
    assert not result.succeeded
    assert remove_calls == []


def test_claude_inventory_fails_closed_if_identity_inspection_fails(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    config = tmp_path / ".claude"
    custom = tmp_path / "custom-memory"
    config.mkdir()
    custom.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(custom)})
    )
    monkeypatch.setattr(
        Path,
        "samefile",
        lambda path, other: (_ for _ in ()).throw(PermissionError("denied")),
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("unsafe broad path" in warning for warning in inventory.warnings)


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


def test_claude_inventory_rejects_absolute_remainder_after_tilde(tmp_path):
    home = tmp_path / "home"
    config = tmp_path / ".claude"
    config.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "MEMORY.md").write_text("memory")
    configured_path = f"~/{victim}"
    assert configured_path.startswith("~//")
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": configured_path})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("after ~/" in warning for warning in inventory.warnings)


def test_claude_inventory_rejects_empty_remainder_after_tilde(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": "~/"})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("after ~/" in warning for warning in inventory.warnings)


def test_claude_inventory_rejects_tilde_store_below_symlinked_home(tmp_path):
    real_home = tmp_path / "real-home"
    custom = real_home / "custom"
    custom.mkdir(parents=True)
    (custom / "MEMORY.md").write_text("memory")
    home_link = tmp_path / "home-link"
    home_link.symlink_to(real_home, target_is_directory=True)
    config = tmp_path / "independent-config"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": "~/custom"})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=home_link,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any("symlink" in warning for warning in inventory.warnings)


@pytest.mark.parametrize(
    "malformed_name",
    ["nul\0path", "surrogate\ud800path"],
    ids=["embedded-nul", "lone-surrogate"],
)
def test_claude_inventory_warns_for_malformed_filesystem_path(
    tmp_path,
    malformed_name,
):
    config = tmp_path / ".claude"
    config.mkdir()
    configured_path = str(tmp_path / malformed_name)
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": configured_path})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert inventory.stores == ()
    assert any(
        "cannot inspect memory path" in warning
        for warning in inventory.warnings
    )


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


def test_running_client_processes_excludes_launcher_ancestors():
    ps = (
        "10 1 zsh zsh\n"
        "20 10 python3 python3 serena_agent_launcher.py\n"
        "30 1 codex codex\n"
    )

    result = running_client_processes(
        "codex",
        run_command=fake_ps(ps),
        current_pid=20,
    )

    assert [process.pid for process in result] == [30]


def test_process_scan_ignores_claude_desktop_but_finds_claude_code():
    ps = (
        "50 1 /Applications/Claude.app/Contents/MacOS/Claude "
        "/Applications/Claude.app/Contents/MacOS/Claude\n"
        "60 1 claude /Users/me/.local/bin/claude\n"
    )

    result = running_client_processes(
        "claude",
        run_command=fake_ps(ps),
        current_pid=20,
    )

    assert [process.pid for process in result] == [60]


def test_process_scan_preserves_spaced_comm_and_ignores_chatgpt_helpers():
    identities = (
        "2176 1 /Applications/ChatGPT.app/Contents/Frameworks/"
        "Codex Framework.framework/Helpers/browser_crashpad_handler\n"
        "2419 1 /Applications/ChatGPT.app/Contents/Frameworks/"
        "Codex Framework.framework/Helpers/Codex (Service).app/Contents/"
        "MacOS/Codex (Service)\n"
        "2502 1 /Applications/ChatGPT.app/Contents/Resources/codex\n"
        "2612 1 /Users/me/.codex/computer-use/"
        "Codex Computer Use.app/Contents/MacOS/SkyComputerUseService\n"
    )
    arguments = (
        "2176 browser_crashpad_handler --monitor-self\n"
        "2419 Codex (Service) --type=gpu-process\n"
        "2502 /Applications/ChatGPT.app/Contents/Resources/codex app-server\n"
        "2612 SkyComputerUseService\n"
    )

    result = running_client_processes(
        "codex",
        run_command=fake_ps(identities, args_output=arguments),
        current_pid=9999,
    )

    assert [(process.pid, Path(process.executable).name) for process in result] == [
        (2502, "codex")
    ]


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_process_scan_ignores_test_commands_that_mention_clients(client):
    ps = (
        "70 1 python3 python3 -m pytest tests/test_codex_claude.py\n"
        "80 1 rg rg codex claude\n"
    )

    result = running_client_processes(
        client,
        run_command=fake_ps(ps),
        current_pid=20,
    )

    assert result == ()


@pytest.mark.parametrize(
    ("client", "process_line"),
    [
        (
            "codex",
            "90 1 node /opt/homebrew/bin/node "
            "/opt/lib/node_modules/@openai/codex/bin/codex.js\n",
        ),
        (
            "claude",
            "91 1 node /opt/homebrew/bin/node "
            "/opt/lib/node_modules/@anthropic-ai/claude-code/cli.js\n",
        ),
    ],
)
def test_process_scan_finds_official_node_client_wrappers(
    client,
    process_line,
):
    result = running_client_processes(
        client,
        run_command=fake_ps(process_line),
        current_pid=20,
    )

    assert len(result) == 1


def test_delete_all_memory_removes_only_validated_stores(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    sibling = active / "sessions/keep.jsonl"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("keep")

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps(""),
    )

    assert result.succeeded
    assert result.deleted_stores == 3
    assert result.deleted_files == 3
    assert sibling.read_text() == "keep"
    assert not (home / ".codex/memories").exists()
    assert not (active / "memories").exists()
    assert not (orca / "memories").exists()


def test_delete_all_claude_memory_removes_project_and_custom_stores_only(
    tmp_path,
):
    home = tmp_path / "home"
    config = home / ".claude"
    project_root = config / "projects/repo"
    project_store = project_root / "memory"
    custom_store = tmp_path / "custom-memory"
    for store in (project_store, custom_store):
        store.mkdir(parents=True)
        (store / "MEMORY.md").write_text("memory")
    (project_store / "details.md").write_text("project detail")
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(custom_store)})
    )

    preserved = {
        project_root / "transcript.jsonl": b"transcript\n",
        project_root / "sessions/keep.jsonl": b"session\n",
        config / "agent-memory/reviewer/keep.md": b"subagent\n",
        config / "CLAUDE.md": b"instructions\n",
        tmp_path / "custom-sibling.txt": b"custom sibling\n",
    }
    for path, content in preserved.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config,
    )
    remove_calls = []

    def fake_remove_tree(path):
        assert path in {project_store, custom_store}
        remove_calls.append(path)
        shutil.rmtree(path)

    result = delete_all_memory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config,
        run_command=fake_ps(""),
        remove_tree=fake_remove_tree,
    )

    assert tuple(store.path for store in inventory.stores) == (
        project_store,
        custom_store,
    )
    assert inventory.file_count == 3
    assert result.succeeded
    assert result.deleted_stores == 2
    assert result.deleted_files == 3
    assert remove_calls == [project_store, custom_store]
    assert not project_store.exists()
    assert not custom_store.exists()
    assert {path: path.read_bytes() for path in preserved} == preserved


def test_delete_all_memory_refuses_running_same_product(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps("40 1 codex codex\n"),
    )

    assert not result.succeeded
    assert result.error is not None
    assert "1 running Codex process" in result.error
    assert (active / "memories/MEMORY.md").exists()


def test_delete_all_memory_ignores_other_product_process(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps("40 1 claude claude\n"),
    )

    assert result.succeeded
    assert result.deleted_stores == 3


def test_delete_prevalidates_every_store_before_mutation(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    (orca / "memories").rename(orca / "memories-real")
    (orca / "memories").symlink_to(
        orca / "memories-real",
        target_is_directory=True,
    )
    calls = []

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps(""),
        remove_tree=lambda path: calls.append(path),
    )

    assert not result.succeeded
    assert calls == []


def test_delete_revalidates_every_store_after_process_scan(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    calls = []
    replaced = False

    def replace_store_with_symlink(command, **kwargs):
        nonlocal replaced
        if command == PS_ARGUMENT_COMMAND and not replaced:
            store = orca / "memories"
            store.rename(orca / "memories-real")
            store.symlink_to(orca / "memories-real", target_is_directory=True)
            replaced = True
        return fake_ps("")(command, **kwargs)

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=replace_store_with_symlink,
        remove_tree=lambda path: calls.append(path),
    )

    assert not result.succeeded
    assert result.error is not None
    assert "symlink" in result.error
    assert calls == []


def test_delete_revalidates_store_immediately_before_each_removal(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    default_store = home / ".codex/memories"
    active_store = active / "memories"
    calls = []

    def replace_next_store(path):
        calls.append(path)
        shutil.rmtree(path)
        active_store.rename(active / "memories-real")
        active_store.symlink_to(
            active / "memories-real",
            target_is_directory=True,
        )

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps(""),
        remove_tree=replace_next_store,
    )

    assert not result.succeeded
    assert result.deleted_stores == 1
    assert result.deleted_files == 1
    assert result.error is not None
    assert "symlink" in result.error
    assert calls == [default_store]
    assert (active / "memories-real/MEMORY.md").exists()


def test_delete_revalidates_claude_identity_before_each_removal(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    config = home / ".claude"
    project_store = config / "projects/repo/memory"
    custom_store = tmp_path / "custom-memory"
    for store in (project_store, custom_store):
        store.mkdir(parents=True)
        (store / "MEMORY.md").write_text("memory")
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(custom_store)})
    )

    real_samefile = Path.samefile
    custom_became_home = False

    def changing_identity(path, other):
        if custom_became_home and path == custom_store and other == home:
            return True
        return real_samefile(path, other)

    remove_calls = []

    def fake_remove_tree(path):
        nonlocal custom_became_home
        remove_calls.append(path)
        custom_became_home = True

    monkeypatch.setattr(Path, "samefile", changing_identity)

    result = delete_all_memory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config,
        run_command=fake_ps(""),
        remove_tree=fake_remove_tree,
    )

    assert not result.succeeded
    assert result.deleted_stores == 1
    assert result.deleted_files == 1
    assert result.error is not None
    assert "unsafe broad memory store" in result.error
    assert remove_calls == [project_store]


def test_delete_reports_partial_counts_and_stops(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    calls = []

    def fail_second(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk busy")
        shutil.rmtree(path)

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps(""),
        remove_tree=fail_second,
    )

    assert not result.succeeded
    assert result.deleted_stores == 1
    assert result.deleted_files == 1
    assert result.error is not None
    assert "disk busy" in result.error
    assert len(calls) == 2


def test_delete_refuses_when_process_scan_fails(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    calls = []

    def unavailable_ps(command, **kwargs):
        raise OSError("process table unavailable")

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=unavailable_ps,
        remove_tree=lambda path: calls.append(path),
    )

    assert not result.succeeded
    assert result.error is not None
    assert "process table unavailable" in result.error
    assert calls == []
