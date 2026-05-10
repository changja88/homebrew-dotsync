import json
import os
import time
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.session_inventory import (
    agent_paths,
    cleanup_inventory,
    encode_claude_project_path,
    scan_inventory,
)


def test_encode_claude_project_path_matches_existing_launcher_encoding():
    assert encode_claude_project_path("/Users/me/repo") == "-Users-me-repo"


def test_codex_paths_use_codex_home_sessions_and_memories(tmp_path):
    paths = agent_paths(
        client="codex",
        cwd="/repo/subdir",
        project_root="/repo",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
    )

    assert paths.sessions_dir == tmp_path / ".codex" / "sessions"
    assert paths.memory_dir == tmp_path / ".codex" / "memories"
    assert paths.criteria == "sessions: same cwd + older than 3d . memory: reset all"


def test_agent_paths_rejects_relative_codex_home(tmp_path):
    with pytest.raises(ValueError, match="codex_home must be absolute"):
        agent_paths(
            client="codex",
            cwd="/repo",
            project_root="/repo",
            home=tmp_path,
            codex_home=Path("relative-codex"),
        )


def test_claude_paths_use_cwd_for_sessions_and_project_root_for_memory(tmp_path):
    paths = agent_paths(
        client="claude",
        cwd="/repo/subdir",
        project_root="/repo",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
    )

    assert paths.sessions_dir == tmp_path / ".claude" / "projects" / "-repo-subdir"
    assert paths.memory_dir == tmp_path / ".claude" / "projects" / "-repo" / "memory"
    assert paths.criteria == "sessions: this project + older than 3d . memory: reset all"


def test_agent_paths_rejects_unknown_client(tmp_path):
    with pytest.raises(ValueError, match="unsupported client"):
        agent_paths(
            client="bad-client",
            cwd="/repo",
            project_root="/repo",
            home=tmp_path,
            codex_home=tmp_path / ".codex",
        )


def _write_jsonl(path: Path, rows: list[dict], *, age_days: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))


def _session_meta(cwd: str) -> dict:
    return {"type": "session_meta", "payload": {"cwd": cwd}}


def test_scan_codex_counts_recursive_matching_sessions_and_memory(tmp_path):
    codex_home = tmp_path / ".codex"
    old_match = codex_home / "sessions" / "2026" / "05" / "01" / "rollout-old.jsonl"
    new_match = codex_home / "sessions" / "2026" / "05" / "10" / "rollout-new.jsonl"
    other = codex_home / "sessions" / "2026" / "05" / "10" / "rollout-other.jsonl"
    _write_jsonl(old_match, [_session_meta("/repo")], age_days=4)
    _write_jsonl(new_match, [_session_meta("/repo")])
    _write_jsonl(other, [_session_meta("/other")], age_days=4)
    (codex_home / "memories").mkdir()
    (codex_home / "memories" / "a.md").write_text("a")
    (codex_home / "memories" / "nested").mkdir()
    (codex_home / "memories" / "nested" / "b.md").write_text("b")

    inventory = scan_inventory(
        client="codex",
        cwd="/repo",
        project_root="/repo",
        home=tmp_path,
        codex_home=codex_home,
        now=time.time(),
    )

    assert inventory.sessions.total == 2
    assert inventory.sessions.to_delete == 1
    assert inventory.sessions.to_keep == 1
    assert inventory.session_delete_paths == [old_match]
    assert inventory.memory.total == 2
    assert inventory.memory.to_reset == 2
    assert inventory.memory.to_keep == 0


def test_scan_codex_skips_unreadable_text_session_files(tmp_path):
    codex_home = tmp_path / ".codex"
    bad = codex_home / "sessions" / "2026" / "05" / "10" / "bad.jsonl"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe\x00")
    good = codex_home / "sessions" / "2026" / "05" / "10" / "good.jsonl"
    _write_jsonl(good, [_session_meta("/repo")])

    inventory = scan_inventory(
        client="codex",
        cwd="/repo",
        project_root="/repo",
        home=tmp_path,
        codex_home=codex_home,
        now=time.time(),
    )

    assert inventory.sessions.total == 1
    assert inventory.session_delete_paths == []


def test_scan_claude_counts_session_dir_and_repo_memory_dir_separately(tmp_path):
    cwd = "/repo/subdir"
    project_root = "/repo"
    session_dir = tmp_path / ".claude" / "projects" / "-repo-subdir"
    memory_dir = tmp_path / ".claude" / "projects" / "-repo" / "memory"
    old = session_dir / "old.jsonl"
    new = session_dir / "new.jsonl"
    _write_jsonl(old, [{"message": "old"}], age_days=4)
    _write_jsonl(new, [{"message": "new"}])
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("index")

    inventory = scan_inventory(
        client="claude",
        cwd=cwd,
        project_root=project_root,
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=time.time(),
    )

    assert inventory.sessions.total == 2
    assert inventory.sessions.to_delete == 1
    assert inventory.sessions.to_keep == 1
    assert inventory.session_delete_paths == [old]
    assert inventory.memory.total == 1
    assert inventory.memory.to_reset == 1
    assert inventory.memory_dir == memory_dir


def test_cleanup_inventory_deletes_codex_old_matching_rollouts_and_memory(tmp_path):
    codex_home = tmp_path / ".codex"
    old_match = codex_home / "sessions" / "2026" / "05" / "01" / "rollout-old.jsonl"
    new_match = codex_home / "sessions" / "2026" / "05" / "10" / "rollout-new.jsonl"
    _write_jsonl(old_match, [_session_meta("/repo")], age_days=4)
    _write_jsonl(new_match, [_session_meta("/repo")])
    mem_file = codex_home / "memories" / "MEMORY.md"
    mem_file.parent.mkdir(parents=True)
    mem_file.write_text("memory")

    result = cleanup_inventory(
        client="codex",
        cwd="/repo",
        project_root="/repo",
        home=tmp_path,
        codex_home=codex_home,
        now=time.time(),
    )

    assert result.sessions.to_delete == 1
    assert result.memory.to_reset == 1
    assert not old_match.exists()
    assert new_match.exists()
    assert not mem_file.parent.exists()


def test_cleanup_inventory_deletes_claude_uuid_dir_and_repo_memory(tmp_path):
    session_dir = tmp_path / ".claude" / "projects" / "-repo-subdir"
    memory_dir = tmp_path / ".claude" / "projects" / "-repo" / "memory"
    old = session_dir / "abc.jsonl"
    uuid_dir = session_dir / "abc"
    _write_jsonl(old, [{"message": "old"}], age_days=4)
    uuid_dir.mkdir(parents=True)
    (uuid_dir / "snapshot.txt").write_text("x")
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("memory")

    result = cleanup_inventory(
        client="claude",
        cwd="/repo/subdir",
        project_root="/repo",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=time.time(),
    )

    assert result.sessions.to_delete == 1
    assert result.memory.to_reset == 1
    assert not old.exists()
    assert not uuid_dir.exists()
    assert not memory_dir.exists()


def test_cleanup_inventory_raises_when_memory_reset_fails(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    mem_file = codex_home / "memories" / "MEMORY.md"
    mem_file.parent.mkdir(parents=True)
    mem_file.write_text("memory")

    def fail_rmtree(path: Path, *, ignore_errors: bool = False) -> None:
        if ignore_errors:
            return
        raise OSError(f"cannot remove {path}")

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.session_inventory.shutil.rmtree",
        fail_rmtree,
    )

    with pytest.raises(OSError, match="cannot remove"):
        cleanup_inventory(
            client="codex",
            cwd="/repo",
            project_root="/repo",
            home=tmp_path,
            codex_home=codex_home,
            now=time.time(),
        )


def test_cleanup_inventory_raises_when_session_unlink_fails(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    old = codex_home / "sessions" / "2026" / "05" / "01" / "rollout-old.jsonl"
    _write_jsonl(old, [_session_meta("/repo")], age_days=4)

    def fail_unlink(self, *args, **kwargs):
        raise OSError(f"cannot unlink {self}")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(OSError, match="cannot unlink"):
        cleanup_inventory(
            client="codex",
            cwd="/repo",
            project_root="/repo",
            home=tmp_path,
            codex_home=codex_home,
            now=time.time(),
        )

    assert old.exists()


def test_cleanup_inventory_raises_when_claude_uuid_dir_cleanup_fails(tmp_path, monkeypatch):
    session_dir = tmp_path / ".claude" / "projects" / "-repo"
    memory_dir = session_dir / "memory"
    old = session_dir / "abc.jsonl"
    uuid_dir = session_dir / "abc"
    _write_jsonl(old, [{"message": "old"}], age_days=4)
    uuid_dir.mkdir(parents=True)
    (uuid_dir / "snapshot.txt").write_text("x")
    memory_dir.mkdir()

    def fail_uuid_rmtree(path):
        if path == uuid_dir:
            raise OSError(f"cannot remove {path}")
        raise AssertionError(f"unexpected rmtree path: {path}")

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.session_inventory.shutil.rmtree",
        fail_uuid_rmtree,
    )

    with pytest.raises(OSError, match="cannot remove"):
        cleanup_inventory(
            client="claude",
            cwd="/repo",
            project_root="/repo",
            home=tmp_path,
            codex_home=tmp_path / ".codex",
            now=time.time(),
        )

    assert uuid_dir.exists()
