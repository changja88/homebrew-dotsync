from dataclasses import replace
from pathlib import Path

from local_dev.serena_mcp_management.session_cleanup import (
    cleanup_claude_inventory,
)
from local_dev.serena_mcp_management.session_inventory import (
    AgentInventory,
    CountStats,
    FileIdentity,
    scan_inventory,
)


SESSION_A = "00000000-0000-4000-8000-000000000201"
SESSION_B = "00000000-0000-4000-8000-000000000202"


def _write(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _inventory(tmp_path: Path):
    config = tmp_path / ".claude"
    _write(config / "projects/-repo" / f"{SESSION_A}.jsonl")
    _write(config / "projects/-repo" / SESSION_A / "subagents/a.jsonl")
    _write(config / "file-history" / SESSION_A / "file.txt")
    _write(config / "projects/-repo/memory/MEMORY.md", "keep")
    _write(config / "settings.json", "{}")
    return scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )


def test_cleanup_claude_removes_exact_inactive_bundle_only(tmp_path):
    inventory = _inventory(tmp_path)
    config = tmp_path / ".claude"

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 1
    assert not (config / "projects/-repo" / f"{SESSION_A}.jsonl").exists()
    assert not (config / "projects/-repo" / SESSION_A).exists()
    assert not (config / "file-history" / SESSION_A).exists()
    assert (config / "projects/-repo/memory/MEMORY.md").read_text() == "keep"
    assert (config / "settings.json").read_text() == "{}"


def test_cleanup_claude_preserves_bundle_that_becomes_active(tmp_path):
    inventory = _inventory(tmp_path)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset({SESSION_A}),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 0
    assert result.preserved_running == 1
    assert inventory.claude_targets[0].roots[0].exists()


def test_cleanup_claude_fails_before_delete_when_manifest_changes(tmp_path):
    inventory = _inventory(tmp_path)
    target = inventory.claude_targets[0]
    bundle_dir = next(path for path in target.roots if path.is_dir())
    _write(bundle_dir / "new-tool-result.txt")

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert "changed after inventory" in result.error
    assert all(path.exists() for path in target.roots)


def test_cleanup_claude_preserves_freshly_open_transcript(tmp_path):
    inventory = _inventory(tmp_path)
    transcript = next(
        path
        for path in inventory.claude_targets[0].roots
        if path.suffix == ".jsonl"
    )
    stat_result = transcript.stat()

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(
            {
                FileIdentity(
                    device=stat_result.st_dev,
                    inode=stat_result.st_ino,
                )
            }
        ),
    )

    assert result.succeeded
    assert result.preserved_running == 1
    assert transcript.exists()


def test_cleanup_claude_zero_targets_is_success(tmp_path):
    inventory = AgentInventory(
        client="claude",
        policy="all_inactive",
        sessions=CountStats(total=0, to_delete=0, to_keep=0),
        criteria="sessions: all projects + all inactive; running preserved",
        claude_config_dir=tmp_path / ".claude",
    )

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 0


def test_cleanup_claude_inventory_warning_fails_before_delete(tmp_path):
    inventory = _inventory(tmp_path)
    unsafe = replace(inventory, warnings=("unsafe bundle",))

    result = cleanup_claude_inventory(
        unsafe,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert all(path.exists() for path in inventory.claude_targets[0].roots)


def test_cleanup_claude_reports_partial_unlink_failure(tmp_path):
    inventory_a = _inventory(tmp_path)
    config = tmp_path / ".claude"
    _write(config / "projects/-repo" / f"{SESSION_B}.jsonl")
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )

    def unlink(path: Path) -> None:
        if path.name == f"{SESSION_B}.jsonl":
            raise OSError("disk failure")
        path.unlink()

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
        unlink=unlink,
    )

    assert len(inventory_a.claude_targets) == 1
    assert not result.succeeded
    assert result.deleted == 1
    assert "disk failure" in result.error


def test_cleanup_claude_leaves_stale_marker_files(tmp_path):
    inventory = _inventory(tmp_path)
    marker = _write(tmp_path / ".claude/sessions/999.json", "{}")

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert marker.read_text() == "{}"
