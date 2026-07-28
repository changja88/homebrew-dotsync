import os
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    CountStats,
    FileIdentity,
    scan_claude_inventory,
    snapshot_open_rollouts,
)


NOW = 2_000_000_000.0


def _write(path: Path, *, age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    timestamp = NOW - age_days * 86400
    os.utime(path, (timestamp, timestamp))
    return path


def test_scan_claude_counts_all_projects_without_memory_or_subagents(tmp_path):
    config = tmp_path / ".claude"
    _write(config / "projects/-repo-a/old.jsonl", age_days=6)
    _write(config / "projects/-repo-b/new.jsonl", age_days=1)
    _write(
        config / "projects/-repo-a/old/subagents/agent.jsonl",
        age_days=6,
    )
    memory = config / "projects/-repo-a/memory/MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("keep")

    inventory = scan_claude_inventory(home=tmp_path, now=NOW)

    assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)
    assert inventory.records == CountStats(total=2, to_delete=1, to_keep=1)
    assert inventory.criteria == "sessions: all projects + native retention 5d"
    assert memory.read_text() == "keep"


def test_scan_claude_uses_strict_five_day_cutoff(tmp_path):
    config = tmp_path / ".claude"
    boundary = _write(
        config / "projects/-repo/boundary.jsonl",
        age_days=5,
    )
    older = _write(config / "projects/-repo/older.jsonl", age_days=5)
    older_ns = older.stat().st_mtime_ns - 1
    os.utime(older, ns=(older_ns, older_ns))

    inventory = scan_claude_inventory(home=tmp_path, now=NOW)

    assert boundary in inventory.scanned_paths
    assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)


def test_scan_claude_uses_absolute_custom_config_root(tmp_path):
    custom = tmp_path / "custom-claude"
    old = _write(custom / "projects/-repo/old.jsonl", age_days=6)

    inventory = scan_claude_inventory(
        home=tmp_path,
        claude_config_dir=custom,
        now=NOW,
    )

    assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
    assert inventory.scanned_paths == (old,)


def test_scan_claude_rejects_relative_config_root(tmp_path):
    with pytest.raises(ValueError, match="claude_config_dir must be absolute"):
        scan_claude_inventory(
            home=tmp_path,
            claude_config_dir=Path("relative"),
            now=NOW,
        )


def test_scan_rejects_unknown_session_policy(tmp_path):
    with pytest.raises(ValueError, match="unsupported session policy"):
        scan_claude_inventory(
            home=tmp_path,
            now=NOW,
            policy="unknown",
        )


def test_snapshot_open_rollouts_parses_lsof_paths(tmp_path):
    rollout = _write(tmp_path / "sessions/root.jsonl")

    def run(command, **kwargs):
        assert "+D" in command
        return subprocess.CompletedProcess(
            command,
            0,
            f"p123\nn{rollout}\n",
            "",
        )

    result = snapshot_open_rollouts((tmp_path / "sessions",), runner=run)

    stat = rollout.stat()
    assert result == frozenset(
        {FileIdentity(device=stat.st_dev, inode=stat.st_ino)}
    )


def test_snapshot_open_rollouts_fails_closed_on_lsof_error(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    def fail(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            "",
            "permission denied",
        )

    with pytest.raises(ActiveSessionScanError, match="permission denied"):
        snapshot_open_rollouts((sessions,), runner=fail)
