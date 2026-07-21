import json
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.memory_management import ClientProcess
from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    CountStats,
    FileIdentity,
    scan_inventory,
    snapshot_active_claude_sessions,
)


SESSION_A = "00000000-0000-4000-8000-000000000101"
SESSION_B = "00000000-0000-4000-8000-000000000102"
PROC_START = "Tue Jul 21 05:29:32 2026"


def _write(path: Path, value: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def test_scan_claude_all_inactive_groups_exact_session_bundles(tmp_path):
    config = tmp_path / ".claude"
    transcript = _write(config / "projects/-repo" / f"{SESSION_A}.jsonl")
    subagent = _write(
        config / "projects/-repo" / SESSION_A / "subagents/agent.jsonl"
    )
    history = _write(config / "file-history" / SESSION_A / "file.txt")
    memory = _write(config / "projects/-repo/memory/MEMORY.md", "keep")
    settings = _write(config / "settings.json", "{}")

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
    target = inventory.claude_targets[0]
    assert target.session_id == SESSION_A
    assert set(target.roots) == {
        transcript,
        config / "projects/-repo" / SESSION_A,
        config / "file-history" / SESSION_A,
    }
    manifest_paths = {entry.path for entry in target.manifest}
    assert subagent in manifest_paths
    assert history in manifest_paths
    assert memory not in manifest_paths
    assert settings not in manifest_paths


def test_scan_claude_all_inactive_preserves_marker_or_open_bundle(tmp_path):
    config = tmp_path / ".claude"
    _write(config / "projects/-repo" / f"{SESSION_A}.jsonl")
    opened = _write(config / "projects/-repo" / f"{SESSION_B}.jsonl")
    opened_stat = opened.stat()

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset({SESSION_A}),
        open_file_identities=frozenset(
            {
                FileIdentity(
                    device=opened_stat.st_dev,
                    inode=opened_stat.st_ino,
                )
            }
        ),
    )

    assert inventory.sessions == CountStats(total=2, to_delete=0, to_keep=2)
    assert inventory.active_sessions == 2
    assert inventory.claude_targets == ()


def _marker(config: Path, *, session_id: str, pid: int, start: str) -> Path:
    path = config / "sessions" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sessionId": session_id, "pid": pid, "procStart": start})
    )
    return path


def test_snapshot_active_claude_sessions_requires_matching_start(tmp_path):
    config = tmp_path / ".claude"
    _marker(config, session_id=SESSION_A, pid=42, start=PROC_START)
    process = ClientProcess(
        pid=42,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    def run(command, **kwargs):
        assert command == ["/bin/ps", "-p", "42", "-o", "lstart="]
        return subprocess.CompletedProcess(command, 0, PROC_START + "\n", "")

    assert snapshot_active_claude_sessions(
        config,
        processes=(process,),
        run_command=run,
    ) == frozenset({SESSION_A})


def test_snapshot_active_claude_sessions_ignores_dead_stale_marker(tmp_path):
    config = tmp_path / ".claude"
    marker = _marker(config, session_id=SESSION_A, pid=42, start=PROC_START)

    assert snapshot_active_claude_sessions(
        config,
        processes=(),
        run_command=lambda *args, **kwargs: pytest.fail("ps must not run"),
    ) == frozenset()
    assert marker.exists()


def test_snapshot_active_claude_sessions_rejects_malformed_marker(tmp_path):
    marker = tmp_path / ".claude/sessions/broken.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("not-json")

    with pytest.raises(ActiveSessionScanError, match="broken.json"):
        snapshot_active_claude_sessions(
            tmp_path / ".claude",
            processes=(),
        )


def test_snapshot_active_claude_sessions_rejects_reused_pid(tmp_path):
    config = tmp_path / ".claude"
    _marker(config, session_id=SESSION_A, pid=42, start=PROC_START)
    process = ClientProcess(
        pid=42,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "Tue Jul 21 06:00:00 2026\n",
            "",
        )

    assert snapshot_active_claude_sessions(
        config,
        processes=(process,),
        run_command=run,
    ) == frozenset()


def test_scan_claude_all_inactive_rejects_bundle_symlink(tmp_path):
    config = tmp_path / ".claude"
    bundle = config / "projects/-repo" / SESSION_A
    bundle.mkdir(parents=True)
    outside = _write(tmp_path / "outside.txt", "keep")
    (bundle / "escape").symlink_to(outside)

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )

    assert inventory.claude_targets == ()
    assert any("symlink" in warning for warning in inventory.warnings)
    assert outside.read_text() == "keep"
