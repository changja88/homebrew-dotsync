import json
import os
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    CountStats,
    FileIdentity,
    scan_inventory,
    snapshot_open_rollouts,
)


NOW = 2_000_000_000.0
ROOT_A = "00000000-0000-4000-8000-000000000001"
ROOT_B = "00000000-0000-4000-8000-000000000002"
CHILD_A = "00000000-0000-4000-8000-000000000003"
GRANDCHILD_A = "00000000-0000-4000-8000-000000000004"
MISSING_PARENT = "00000000-0000-4000-8000-000000000099"


def _session_meta(session_id: str, parent_id: str | None = None) -> dict:
    payload: dict = {"id": session_id, "cwd": "/repo"}
    if parent_id is not None:
        payload["source"] = {
            "subagent": {"thread_spawn": {"parent_thread_id": parent_id}}
        }
    return {"type": "session_meta", "payload": payload}


def _write_jsonl(
    path: Path,
    rows: list[dict],
    *,
    age_days: float = 0,
    tail: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row) + "\n" for row in rows) + tail
    path.write_text(text)
    timestamp = NOW - age_days * 86400
    os.utime(path, (timestamp, timestamp))


def test_scan_claude_counts_all_projects_without_memory_or_subagents(tmp_path):
    config = tmp_path / ".claude"
    old = config / "projects" / "-repo-a" / "old.jsonl"
    new = config / "projects" / "-repo-b" / "new.jsonl"
    subagent = (
        config
        / "projects"
        / "-repo-a"
        / "old"
        / "subagents"
        / "agent.jsonl"
    )
    memory = config / "projects" / "-repo-a" / "memory" / "MEMORY.md"
    _write_jsonl(old, [_session_meta(ROOT_A)], age_days=6)
    _write_jsonl(new, [_session_meta(ROOT_B)], age_days=1)
    _write_jsonl(subagent, [_session_meta(CHILD_A)], age_days=6)
    memory.parent.mkdir(parents=True)
    memory.write_text("keep")

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
    )

    assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)
    assert inventory.records == CountStats(total=2, to_delete=1, to_keep=1)
    assert inventory.criteria == "sessions: all projects + native retention 5d"
    assert not hasattr(inventory, "memory")
    assert inventory.codex_targets == ()
    assert memory.read_text() == "keep"


def test_scan_claude_uses_strict_five_day_cutoff(tmp_path):
    config = tmp_path / ".claude"
    boundary = config / "projects" / "-repo" / "boundary.jsonl"
    older = config / "projects" / "-repo" / "older.jsonl"
    _write_jsonl(boundary, [_session_meta(ROOT_A)], age_days=5)
    _write_jsonl(older, [_session_meta(ROOT_B)], age_days=5)
    older_ns = older.stat().st_mtime_ns - 1
    os.utime(older, ns=(older_ns, older_ns))

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
    )

    assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)


def test_scan_claude_uses_absolute_custom_config_root(tmp_path):
    custom = tmp_path / "custom-claude"
    old = custom / "projects" / "-repo" / "old.jsonl"
    _write_jsonl(old, [_session_meta(ROOT_A)], age_days=6)

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=custom,
        now=NOW,
    )

    assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
    assert inventory.scanned_paths == (old,)


def test_scan_claude_rejects_relative_config_root(tmp_path):
    with pytest.raises(ValueError, match="claude_config_dir must be absolute"):
        scan_inventory(
            client="claude",
            home=tmp_path,
            codex_home=tmp_path / ".codex",
            claude_config_dir=Path("relative"),
            now=NOW,
        )


def test_scan_rejects_unknown_client(tmp_path):
    with pytest.raises(ValueError, match="unsupported client"):
        scan_inventory(
            client="other",
            home=tmp_path,
            codex_home=tmp_path / ".codex",
            now=NOW,
        )


def test_scan_codex_groups_all_homes_and_uses_descendant_activity(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = (
        tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    )
    root = default_home / "sessions/2026/07/01/root.jsonl"
    bridged = orca_home / "sessions/2026/07/01/root.jsonl"
    child = orca_home / "sessions/2026/07/02/child.jsonl"
    _write_jsonl(root, [_session_meta(ROOT_A)], age_days=8)
    bridged.parent.mkdir(parents=True)
    os.link(root, bridged)
    _write_jsonl(child, [_session_meta(CHILD_A, ROOT_A)], age_days=1)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=0, to_keep=1)
    assert inventory.records == CountStats(total=3, to_delete=0, to_keep=3)
    assert inventory.criteria == (
        "sessions: all known homes + inactive longer than 5d"
    )
    assert inventory.codex_targets == ()
    assert set(inventory.scanned_paths) == {root, bridged, child}


def test_scan_codex_builds_source_before_orca_delete_plan(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = (
        tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    )
    source = default_home / "sessions/2026/07/01/root.jsonl"
    bridged = orca_home / "sessions/2026/07/01/root.jsonl"
    child = orca_home / "sessions/2026/07/01/child.jsonl"
    _write_jsonl(source, [_session_meta(ROOT_A)], age_days=6)
    bridged.parent.mkdir(parents=True)
    os.link(source, bridged)
    _write_jsonl(child, [_session_meta(CHILD_A, ROOT_A)], age_days=6)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
    assert inventory.records == CountStats(total=3, to_delete=3, to_keep=0)
    target = inventory.codex_targets[0]
    assert target.root_id == ROOT_A
    assert [
        (owner.codex_home, owner.local_delete_ids, owner.is_orca)
        for owner in target.owners
    ] == [
        (default_home, (ROOT_A,), False),
        (orca_home, (CHILD_A, ROOT_A), True),
    ]


def test_scan_codex_reads_only_first_jsonl_record(tmp_path):
    rollout = tmp_path / ".codex/sessions/2026/07/01/root.jsonl"
    _write_jsonl(
        rollout,
        [_session_meta(ROOT_A)],
        age_days=1,
        tail="{" * 1_000_000,
    )

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=0, to_keep=1)
    assert inventory.warnings == ()


def test_scan_codex_excludes_archived_sessions(tmp_path):
    archived = tmp_path / ".codex/archived_sessions/archived.jsonl"
    _write_jsonl(archived, [_session_meta(ROOT_A)], age_days=10)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=0, to_delete=0, to_keep=0)
    assert archived.exists()


def test_scan_codex_keeps_open_logical_group(tmp_path):
    rollout = tmp_path / ".codex/sessions/2026/07/01/root.jsonl"
    _write_jsonl(rollout, [_session_meta(ROOT_A)], age_days=6)
    stat = rollout.stat()

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        open_file_identities=frozenset(
            {FileIdentity(device=stat.st_dev, inode=stat.st_ino)}
        ),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=0, to_keep=1)
    assert inventory.codex_targets == ()


def test_scan_codex_treats_missing_parent_as_cleanup_root(tmp_path):
    sessions = tmp_path / ".codex/sessions/2026/07/01"
    orphan = sessions / "orphan.jsonl"
    descendant = sessions / "descendant.jsonl"
    grandchild = sessions / "grandchild.jsonl"
    _write_jsonl(
        orphan,
        [_session_meta(ROOT_A, MISSING_PARENT)],
        age_days=10,
    )
    _write_jsonl(
        descendant,
        [_session_meta(CHILD_A, ROOT_A)],
        age_days=10,
    )
    _write_jsonl(
        grandchild,
        [_session_meta(GRANDCHILD_A, CHILD_A)],
        age_days=10,
    )

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
    target = inventory.codex_targets[0]
    assert target.root_id == MISSING_PARENT
    assert target.owners[0].local_delete_ids == (
        GRANDCHILD_A,
        CHILD_A,
        ROOT_A,
    )
    assert not any("missing parent" in warning for warning in inventory.warnings)


def test_scan_codex_keeps_unsafe_graphs_with_warnings(tmp_path):
    sessions = tmp_path / ".codex/sessions/2026/07/01"
    malformed = sessions / "malformed.jsonl"
    cycle_a = sessions / "cycle-a.jsonl"
    cycle_b = sessions / "cycle-b.jsonl"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("not-json\n")
    _write_jsonl(cycle_a, [_session_meta(ROOT_B, CHILD_A)], age_days=10)
    _write_jsonl(cycle_b, [_session_meta(CHILD_A, ROOT_B)], age_days=10)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions.to_delete == 0
    assert inventory.codex_targets == ()
    assert any("malformed" in warning for warning in inventory.warnings)
    assert any("cycle" in warning for warning in inventory.warnings)


def test_scan_codex_keeps_conflicting_parent_copy(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = (
        tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    )
    _write_jsonl(
        default_home / "sessions/2026/07/01/child.jsonl",
        [_session_meta(CHILD_A, ROOT_A)],
        age_days=10,
    )
    _write_jsonl(
        orca_home / "sessions/2026/07/01/child.jsonl",
        [_session_meta(CHILD_A, ROOT_B)],
        age_days=10,
    )

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions.to_delete == 0
    assert any("conflicting parent" in warning for warning in inventory.warnings)


def test_scan_codex_uses_local_root_for_descendant_fragment(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = (
        tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    )
    _write_jsonl(
        default_home / "sessions/2026/07/01/root.jsonl",
        [_session_meta(ROOT_A)],
        age_days=6,
    )
    _write_jsonl(
        orca_home / "sessions/2026/07/01/child.jsonl",
        [_session_meta(CHILD_A, ROOT_A)],
        age_days=6,
    )

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )

    target = inventory.codex_targets[0]
    assert [
        (owner.codex_home, owner.local_delete_ids) for owner in target.owners
    ] == [
        (default_home, (ROOT_A,)),
        (orca_home, (CHILD_A,)),
    ]


def test_scan_codex_deduplicates_canonical_home_paths(tmp_path):
    default_home = tmp_path / ".codex"
    default_home.mkdir()
    alias = tmp_path / "codex-alias"
    alias.symlink_to(default_home, target_is_directory=True)
    rollout = default_home / "sessions/2026/07/01/root.jsonl"
    _write_jsonl(rollout, [_session_meta(ROOT_A)], age_days=1)

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=alias,
        orca_codex_home=alias,
        now=NOW,
        open_file_identities=frozenset(),
    )

    assert inventory.sessions.total == 1
    assert inventory.session_dirs == (default_home / "sessions",)


def test_snapshot_open_rollouts_parses_lsof_paths(tmp_path):
    rollout = tmp_path / "sessions/root.jsonl"
    _write_jsonl(rollout, [_session_meta(ROOT_A)])

    def run(cmd, **kwargs):
        assert "+D" in cmd
        return subprocess.CompletedProcess(cmd, 0, f"p123\nn{rollout}\n", "")

    result = snapshot_open_rollouts((tmp_path / "sessions",), runner=run)

    stat = rollout.stat()
    assert result == frozenset(
        {FileIdentity(device=stat.st_dev, inode=stat.st_ino)}
    )


def test_snapshot_open_rollouts_fails_closed_on_lsof_error(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    def fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, "", "permission denied")

    with pytest.raises(ActiveSessionScanError, match="permission denied"):
        snapshot_open_rollouts((sessions,), runner=fail)
