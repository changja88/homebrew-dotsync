import json
import os
import subprocess
from pathlib import Path

from local_dev.serena_mcp_management.session_cleanup import (
    CLAUDE_RETENTION_JSON,
    claude_retention_args,
    cleanup_codex_inventory,
)
from local_dev.serena_mcp_management.session_inventory import scan_inventory


NOW = 2_000_000_000.0
ROOT_A = "00000000-0000-4000-8000-000000000001"
CHILD_A = "00000000-0000-4000-8000-000000000003"


def _session_meta(session_id: str, parent_id: str | None = None) -> dict:
    payload: dict = {"id": session_id, "cwd": "/repo"}
    if parent_id is not None:
        payload["source"] = {
            "subagent": {"thread_spawn": {"parent_thread_id": parent_id}}
        }
    return {"type": "session_meta", "payload": payload}


def _write_old_session(path: Path, session_id: str, parent_id: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_session_meta(session_id, parent_id)) + "\n")
    timestamp = NOW - 6 * 86400
    os.utime(path, (timestamp, timestamp))


def _bridged_inventory(tmp_path: Path):
    default_home = tmp_path / ".codex"
    orca_home = (
        tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    )
    source = default_home / "sessions/2026/07/01/root.jsonl"
    bridged = orca_home / "sessions/2026/07/01/root.jsonl"
    child = orca_home / "sessions/2026/07/01/child.jsonl"
    _write_old_session(source, ROOT_A)
    bridged.parent.mkdir(parents=True)
    os.link(source, bridged)
    _write_old_session(child, CHILD_A, ROOT_A)
    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=orca_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )
    return inventory, default_home, orca_home, source


def test_cleanup_calls_official_delete_source_before_orca(tmp_path):
    inventory, default_home, orca_home, _source = _bridged_inventory(tmp_path)
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]["CODEX_HOME"]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=run,
        open_file_snapshot=lambda _: frozenset(),
    )

    assert calls[0][0] == ["/fake/codex", "delete", "--help"]
    assert calls[1] == (
        ["/fake/codex", "delete", "--force", ROOT_A],
        str(default_home),
    )
    assert calls[2] == (
        ["/fake/codex", "delete", "--force", CHILD_A],
        str(orca_home),
    )
    assert calls[3] == (
        ["/fake/codex", "delete", "--force", ROOT_A],
        str(orca_home),
    )
    assert result.deleted == 1
    assert result.warnings == ()


def test_cleanup_does_not_delete_when_capability_probe_fails(tmp_path):
    inventory, _default_home, _orca_home, source = _bridged_inventory(tmp_path)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 2, "", "unknown command delete")

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=run,
        open_file_snapshot=lambda _: frozenset(),
    )

    assert calls == [["/fake/codex", "delete", "--help"]]
    assert result.deleted == 0
    assert source.exists()
    assert any("does not support" in warning for warning in result.warnings)


def test_cleanup_source_failure_preserves_orca_copy(tmp_path):
    inventory, default_home, orca_home, source = _bridged_inventory(tmp_path)
    delete_homes = []

    def run(cmd, **kwargs):
        if cmd[-1] == "--help":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        delete_homes.append(kwargs["env"]["CODEX_HOME"])
        return subprocess.CompletedProcess(cmd, 1, "", "busy")

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=run,
        open_file_snapshot=lambda _: frozenset(),
    )

    assert delete_homes == [str(default_home)]
    assert str(orca_home) not in delete_homes
    assert result.deleted == 0
    assert source.exists()


def test_cleanup_does_not_claim_partial_managed_delete(tmp_path):
    inventory, default_home, orca_home, _source = _bridged_inventory(tmp_path)

    def run(cmd, **kwargs):
        if cmd[-1] == "--help":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        code = 1 if kwargs["env"]["CODEX_HOME"] == str(orca_home) else 0
        return subprocess.CompletedProcess(cmd, code, "", "managed failed")

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=run,
        open_file_snapshot=lambda _: frozenset(),
    )

    assert str(default_home) != str(orca_home)
    assert result.deleted == 0
    assert any("managed failed" in warning for warning in result.warnings)


def test_cleanup_skips_all_when_path_set_changes_after_scan(tmp_path):
    inventory, _default_home, orca_home, _source = _bridged_inventory(tmp_path)
    _write_old_session(
        orca_home / "sessions/2026/07/01/new-child.jsonl",
        CHILD_A,
        ROOT_A,
    )
    calls = []

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=lambda *args, **kwargs: calls.append(args),
        open_file_snapshot=lambda _: frozenset(),
    )

    assert calls == []
    assert result.deleted == 0
    assert any("changed after inventory" in warning for warning in result.warnings)


def test_cleanup_skips_when_known_home_session_dir_appears_after_scan(tmp_path):
    default_home = tmp_path / ".codex"
    orca_home = (
        tmp_path / "Library/Application Support/orca/codex-runtime-home/home"
    )
    source = default_home / "sessions/2026/07/01/root.jsonl"
    _write_old_session(source, ROOT_A)
    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=default_home,
        orca_codex_home=orca_home,
        now=NOW,
        open_file_identities=frozenset(),
    )
    bridged = orca_home / "sessions/2026/07/01/root.jsonl"
    bridged.parent.mkdir(parents=True)
    os.link(source, bridged)
    calls = []

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=lambda *args, **kwargs: calls.append(args),
        open_file_snapshot=lambda _: frozenset(),
    )

    assert calls == []
    assert result.deleted == 0
    assert any("changed after inventory" in warning for warning in result.warnings)


def test_cleanup_skips_group_when_fingerprint_changes(tmp_path):
    inventory, _default_home, _orca_home, source = _bridged_inventory(tmp_path)
    source.write_text(source.read_text() + "changed\n")
    calls = []

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=lambda *args, **kwargs: calls.append(args),
        open_file_snapshot=lambda _: frozenset(),
    )

    assert calls == []
    assert result.deleted == 0
    assert any("changed after inventory" in warning for warning in result.warnings)


def test_cleanup_skips_group_that_is_now_open(tmp_path):
    inventory, _default_home, _orca_home, _source = _bridged_inventory(tmp_path)
    open_identity = inventory.codex_targets[0].files[0].fingerprint.identity
    calls = []

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=lambda *args, **kwargs: calls.append(args),
        open_file_snapshot=lambda _: frozenset({open_identity}),
    )

    assert calls == []
    assert result.deleted == 0
    assert any("currently open" in warning for warning in result.warnings)


def test_cleanup_converts_delete_timeout_to_warning(tmp_path):
    inventory, _default_home, _orca_home, _source = _bridged_inventory(tmp_path)

    def run(cmd, **kwargs):
        if cmd[-1] == "--help":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=run,
        open_file_snapshot=lambda _: frozenset(),
    )

    assert result.deleted == 0
    assert any("timed out" in warning for warning in result.warnings)


def test_claude_retention_args_arms_native_five_day_policy():
    assert CLAUDE_RETENTION_JSON == '{"cleanupPeriodDays":5}'
    assert claude_retention_args([]) == ["--settings", CLAUDE_RETENTION_JSON]
    assert claude_retention_args(["-c"]) == [
        "--settings",
        CLAUDE_RETENTION_JSON,
        "-c",
    ]


def test_claude_retention_args_preserves_user_settings():
    explicit = ["--settings", "/tmp/custom.json", "-c"]
    inline = ["--settings={\"model\":\"opus\"}", "-r", "session"]

    assert claude_retention_args(explicit) == explicit
    assert claude_retention_args(inline) == inline
