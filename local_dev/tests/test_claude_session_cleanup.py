import fcntl
import os
from dataclasses import replace
from pathlib import Path

import pytest

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


def test_cleanup_claude_counts_initially_active_sessions(tmp_path):
    inventory_a = _inventory(tmp_path)
    active_transcript = _write(
        tmp_path / ".claude/projects/-repo" / f"{SESSION_B}.jsonl"
    )
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset({SESSION_B}),
        open_file_identities=frozenset(),
    )

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset({SESSION_B}),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert len(inventory_a.claude_targets) == 1
    assert inventory.active_sessions == 1
    assert result.succeeded
    assert result.deleted == 1
    assert result.preserved_running == 1
    assert active_transcript.exists()


def test_cleanup_claude_refreshes_active_before_bundle_mutation(tmp_path):
    inventory = _inventory(tmp_path)
    snapshots = iter((frozenset(), frozenset({SESSION_A})))

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: next(snapshots),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 0
    assert result.preserved_running == 1
    assert all(path.exists() for path in inventory.claude_targets[0].roots)


def test_cleanup_claude_refreshes_open_files_before_bundle_mutation(tmp_path):
    inventory = _inventory(tmp_path)
    transcript = next(
        path
        for path in inventory.claude_targets[0].roots
        if path.suffix == ".jsonl"
    )
    stat_result = transcript.stat()
    identity = FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
    )
    snapshots = iter((frozenset(), frozenset({identity})))

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: next(snapshots),
    )

    assert result.succeeded
    assert result.deleted == 0
    assert result.preserved_running == 1
    assert transcript.exists()


def test_cleanup_claude_rejects_new_uuid_root_before_bundle_mutation(tmp_path):
    inventory = _inventory(tmp_path)
    config = tmp_path / ".claude"
    new_root = config / "debug" / f"{SESSION_A}.txt"
    snapshot_count = 0

    def active_snapshot(config_dir):
        nonlocal snapshot_count
        snapshot_count += 1
        if snapshot_count == 2:
            _write(new_root, "new")
        return frozenset()

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=active_snapshot,
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert "roots changed" in result.error
    assert new_root.read_text() == "new"
    assert all(path.exists() for path in inventory.claude_targets[0].roots)


def test_cleanup_claude_prevalidates_all_target_root_sets_before_delete(
    tmp_path,
):
    _inventory(tmp_path)
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
    targets = {target.session_id: target for target in inventory.claude_targets}
    new_root = _write(config / "debug" / f"{SESSION_B}.txt", "new")

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert result.deleted == 0
    assert "roots changed" in result.error
    assert all(path.exists() for path in targets[SESSION_A].roots)
    assert all(path.exists() for path in targets[SESSION_B].roots)
    assert new_root.read_text() == "new"


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


def test_cleanup_claude_does_not_follow_swapped_ancestor(
    tmp_path,
    monkeypatch,
):
    inventory = _inventory(tmp_path)
    config = tmp_path / ".claude"
    project_dir = config / "projects/-repo"
    parked_project = tmp_path / "parked-project"
    outside_project = tmp_path / "outside-project"
    outside_file = _write(
        outside_project / SESSION_A / "outside.txt",
        "keep",
    )
    transcript = config / "projects/-repo" / f"{SESSION_A}.jsonl"
    original_unlink = os.unlink
    swapped = False

    def unlink(path, *args, **kwargs):
        nonlocal swapped
        original_unlink(path, *args, **kwargs)
        if swapped or Path(path).name != transcript.name:
            return
        project_dir.rename(parked_project)
        project_dir.symlink_to(outside_project, target_is_directory=True)
        swapped = True

    monkeypatch.setattr(os, "unlink", unlink)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert swapped
    assert not result.succeeded
    assert outside_file.read_text() == "keep"


@pytest.mark.parametrize("root_kind", ("file", "directory"))
def test_cleanup_claude_does_not_delete_final_name_replacement(
    tmp_path,
    monkeypatch,
    root_kind,
):
    config = tmp_path / ".claude"
    project_dir = config / "projects/-repo"
    project_dir.mkdir(parents=True)
    if root_kind == "file":
        root = _write(project_dir / f"{SESSION_A}.jsonl", "expected")
        replacement = _write(tmp_path / "replacement.jsonl", "replacement")
    else:
        root = project_dir / SESSION_A
        root.mkdir()
        replacement = tmp_path / "replacement-directory"
        replacement.mkdir()
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )
    replacement_stat = replacement.stat()
    replacement_identity = (
        replacement_stat.st_dev,
        replacement_stat.st_ino,
    )
    parked_original = tmp_path / f"parked-{root_kind}"
    original_rename = os.rename
    original_unlink = os.unlink
    original_rmdir = os.rmdir
    swapped = False

    def swap_final_name():
        nonlocal swapped
        original_rename(root, parked_original)
        original_rename(replacement, root)
        swapped = True

    def rename(src, dst, *args, **kwargs):
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and Path(src).name == root.name
        ):
            swap_final_name()
        original_rename(src, dst, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        if not swapped and Path(path).name == root.name:
            swap_final_name()
        original_unlink(path, *args, **kwargs)

    def rmdir(path, *args, **kwargs):
        if not swapped and Path(path).name == root.name:
            swap_final_name()
        original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "rmdir", rmdir)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    surviving_identities = {
        (candidate_stat.st_dev, candidate_stat.st_ino)
        for candidate in tmp_path.rglob("*")
        if (candidate_stat := candidate.stat(follow_symlinks=False))
    }
    assert swapped
    assert replacement_identity in surviving_identities
    assert not result.succeeded
    assert result.deleted == 0
    assert parked_original.exists()


def test_cleanup_claude_reports_last_verified_path_after_ancestor_remap(
    tmp_path,
    monkeypatch,
):
    config = tmp_path / ".claude"
    project_dir = config / "projects/-repo"
    root = _write(project_dir / f"{SESSION_A}.jsonl", "expected")
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )
    remapped_project = tmp_path / "remapped-project"
    original_rename = os.rename
    remapped = False

    def rename(src, dst, *args, **kwargs):
        nonlocal remapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not remapped
            and kwargs.get("src_dir_fd") is not None
            and Path(src).name == root.name
        ):
            original_rename(project_dir, remapped_project)
            project_dir.mkdir()
            remapped = True

    monkeypatch.setattr(os, "rename", rename)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    quarantine = next(remapped_project.glob(".claude-cleanup-*"))
    recovery_path = quarantine / root.name
    stale_path = project_dir / quarantine.name / root.name
    assert remapped
    assert not result.succeeded
    assert result.deleted == 0
    assert recovery_path.read_text() == "expected"
    assert not stale_path.exists()
    assert f"last-verified lexical path={recovery_path}" in result.error
    assert str(stale_path) not in result.error
    assert "current namespace location is not guaranteed" in result.error


def test_cleanup_claude_labels_path_after_post_validation_ancestor_remap(
    tmp_path,
    monkeypatch,
):
    config = tmp_path / ".claude"
    project_dir = config / "projects/-repo"
    root = _write(project_dir / f"{SESSION_A}.jsonl", "expected")
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )
    first_remapped_project = tmp_path / "first-remapped-project"
    final_remapped_project = tmp_path / "final-remapped-project"
    original_rename = os.rename
    original_stat = os.stat
    isolation_remapped = False
    post_validation_remapped = False

    def rename(src, dst, *args, **kwargs):
        nonlocal isolation_remapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not isolation_remapped
            and kwargs.get("src_dir_fd") is not None
            and Path(src).name == root.name
        ):
            original_rename(project_dir, first_remapped_project)
            project_dir.mkdir()
            isolation_remapped = True

    def stat_path(path, *args, **kwargs):
        nonlocal post_validation_remapped
        result = original_stat(path, *args, **kwargs)
        if (
            isolation_remapped
            and not post_validation_remapped
            and isinstance(path, Path)
            and path.name == root.name
            and first_remapped_project in path.parents
            and kwargs.get("follow_symlinks") is False
        ):
            original_rename(first_remapped_project, final_remapped_project)
            first_remapped_project.mkdir()
            post_validation_remapped = True
        return result

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(os, "stat", stat_path)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    quarantine = next(final_remapped_project.glob(".claude-cleanup-*"))
    actual_recovery_path = quarantine / root.name
    last_verified_path = (
        first_remapped_project / quarantine.name / root.name
    )
    quarantine_stat = quarantine.stat(follow_symlinks=False)
    isolated_stat = actual_recovery_path.stat(follow_symlinks=False)
    assert isolation_remapped
    assert post_validation_remapped
    assert not result.succeeded
    assert actual_recovery_path.read_text() == "expected"
    assert not last_verified_path.exists()
    assert f"last-verified lexical path={last_verified_path}" in result.error
    assert f"quarantine name={quarantine.name}" in result.error
    assert (
        "quarantine identity="
        f"device:{quarantine_stat.st_dev},inode:{quarantine_stat.st_ino}"
        in result.error
    )
    assert f"isolated entry name={root.name}" in result.error
    assert (
        "isolated identity="
        f"device:{isolated_stat.st_dev},inode:{isolated_stat.st_ino}"
        in result.error
    )
    assert (
        "isolated fingerprint="
        f"size:{isolated_stat.st_size},mtime_ns:{isolated_stat.st_mtime_ns}"
        in result.error
    )
    assert "current namespace location is not guaranteed" in result.error
    assert "isolated entry preserved at" not in result.error


def test_cleanup_claude_reports_durable_details_when_fgetpath_fails(
    tmp_path,
    monkeypatch,
):
    config = tmp_path / ".claude"
    project_dir = config / "projects/-repo"
    root = _write(project_dir / f"{SESSION_A}.jsonl", "expected")
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )
    original_rename = os.rename
    isolated = False

    def rename(src, dst, *args, **kwargs):
        nonlocal isolated
        original_rename(src, dst, *args, **kwargs)
        if (
            not isolated
            and kwargs.get("src_dir_fd") is not None
            and Path(src).name == root.name
        ):
            isolated = True
            raise OSError("post-isolation failure")

    def fail_fgetpath(fd, command, arg=0):
        assert command == fcntl.F_GETPATH
        raise OSError("injected F_GETPATH failure")

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(fcntl, "fcntl", fail_fgetpath)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    quarantine = next(project_dir.glob(".claude-cleanup-*"))
    recovery_path = quarantine / root.name
    quarantine_stat = quarantine.stat(follow_symlinks=False)
    isolated_stat = recovery_path.stat(follow_symlinks=False)
    assert isolated
    assert not result.succeeded
    assert recovery_path.read_text() == "expected"
    assert "post-isolation failure" in result.error
    assert f"last-known lexical path={recovery_path}" in result.error
    assert f"quarantine name={quarantine.name}" in result.error
    assert (
        "quarantine identity="
        f"device:{quarantine_stat.st_dev},inode:{quarantine_stat.st_ino}"
        in result.error
    )
    assert f"isolated entry name={root.name}" in result.error
    assert (
        "isolated identity="
        f"device:{isolated_stat.st_dev},inode:{isolated_stat.st_ino}"
        in result.error
    )
    assert (
        "isolated fingerprint="
        f"size:{isolated_stat.st_size},mtime_ns:{isolated_stat.st_mtime_ns}"
        in result.error
    )
    assert (
        "current-path lookup/F_GETPATH failed: "
        "injected F_GETPATH failure" in result.error
    )
    assert "current namespace location is not guaranteed" in result.error
    assert "remains preserved" not in result.error


def test_cleanup_claude_reports_quarantine_cleanup_failure(
    tmp_path,
    monkeypatch,
):
    config = tmp_path / ".claude"
    project_dir = config / "projects/-repo"
    root = _write(project_dir / f"{SESSION_A}.jsonl", "expected")
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )
    original_rename = os.rename
    original_rmdir = os.rmdir

    def rename(src, dst, *args, **kwargs):
        if (
            kwargs.get("src_dir_fd") is not None
            and Path(src).name == root.name
        ):
            raise OSError("isolation failure")
        original_rename(src, dst, *args, **kwargs)

    def rmdir(path, *args, **kwargs):
        if Path(path).name.startswith(".claude-cleanup-"):
            raise OSError("quarantine cleanup failure")
        original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(os, "rmdir", rmdir)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    quarantine = next(project_dir.glob(".claude-cleanup-*"))
    assert not result.succeeded
    assert result.deleted == 0
    assert root.read_text() == "expected"
    assert list(quarantine.iterdir()) == []
    assert "isolation failure" in result.error
    assert "quarantine cleanup failure" in result.error
    assert str(quarantine) in result.error


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
        active_sessions=2,
    )

    def unexpected_snapshot(*args):
        pytest.fail("zero-target cleanup must not take snapshots")

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=unexpected_snapshot,
        open_file_snapshot=unexpected_snapshot,
    )

    assert result.succeeded
    assert result.deleted == 0
    assert result.preserved_running == 2


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


def test_cleanup_claude_reports_partial_unlink_failure(tmp_path, monkeypatch):
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

    original_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        if Path(path).name == f"{SESSION_B}.jsonl":
            raise OSError("disk failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
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
