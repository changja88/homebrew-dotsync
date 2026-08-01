from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_dev.serena_mcp_management import claude_reset
from local_dev.serena_mcp_management.memory_management import (
    ClientProcess,
    MemoryStore,
)


class CommandRecorder:
    def __init__(
        self,
        responses: dict[tuple[str, ...], tuple[int, str, str]],
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        key = tuple(command)
        self.calls.append((key, kwargs))
        code, stdout, stderr = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(command, code, stdout, stderr)


def test_result_succeeds_only_without_error():
    assert claude_reset.ClaudeResetResult().succeeded is True
    assert claude_reset.ClaudeResetResult(error="failed").succeeded is False


def test_missing_official_purge_capability_fails_before_mutation(
    monkeypatch,
    tmp_path,
):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    settings = config_dir / "settings.json"
    settings.write_text('{"theme":"dark"}', encoding="utf-8")
    generated = config_dir / "plans/plan.md"
    generated.parent.mkdir()
    generated.write_text("keep until reset starts", encoding="utf-8")
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --dry-run",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is False
    assert "--yes" in (result.error or "")
    assert settings.read_text(encoding="utf-8") == '{"theme":"dark"}'
    assert generated.read_text(encoding="utf-8") == "keep until reset starts"
    assert [call[0] for call in recorder.calls] == [
        ("/real/claude", "project", "purge", "--help"),
    ]


def test_invalid_custom_global_json_fails_before_capability_probe(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    global_config = config_dir / ".claude.json"
    global_config.write_text("not-json", encoding="utf-8")
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is False
    assert str(global_config) in (result.error or "")
    assert recorder.calls == []


def test_capability_probe_preserves_custom_config_environment(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"theme": "dark"}),
        encoding="utf-8",
    )
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
    )

    assert result.succeeded is True
    assert all(
        kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(config_dir)
        for command, kwargs in recorder.calls
        if command[0] == "/real/claude"
    )


def test_managed_auto_memory_policy_fails_closed_before_mutation(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    custom_memory = tmp_path / "user-memory"
    custom_memory.mkdir()
    (custom_memory / "MEMORY.md").write_text("keep", encoding="utf-8")
    settings = config_dir / "settings.json"
    settings_bytes = json.dumps(
        {"autoMemoryDirectory": str(custom_memory), "theme": "dark"}
    ).encode()
    settings.write_bytes(settings_bytes)
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
        _managed_policy_checker=lambda **kwargs: (
            "managed autoMemoryDirectory is active and cannot be reset safely"
        ),
    )

    assert result.succeeded is False
    assert "managed autoMemoryDirectory" in (result.error or "")
    assert recorder.calls == []
    assert settings.read_bytes() == settings_bytes
    assert (custom_memory / "MEMORY.md").read_text(encoding="utf-8") == "keep"


def test_file_managed_auto_memory_policy_is_detected(tmp_path):
    policy_dir = tmp_path / "ClaudeCode"
    drop_ins = policy_dir / "managed-settings.d"
    drop_ins.mkdir(parents=True)
    (policy_dir / "managed-settings.json").write_text(
        json.dumps({"theme": "dark"}),
        encoding="utf-8",
    )
    (drop_ins / "20-memory.json").write_text(
        json.dumps({"autoMemoryDirectory": "~/managed-memory"}),
        encoding="utf-8",
    )

    error = claude_reset._managed_auto_memory_policy_error(
        policy_dir=policy_dir,
        defaults_reader=lambda: {},
    )

    assert error is not None
    assert "managed autoMemoryDirectory" in error


def test_dynamic_policy_helper_fails_closed_even_without_static_memory_path(tmp_path):
    policy_dir = tmp_path / "ClaudeCode"
    policy_dir.mkdir()
    (policy_dir / "managed-settings.json").write_text(
        json.dumps({"policyHelper": {"path": "/managed/policy-helper"}}),
        encoding="utf-8",
    )

    error = claude_reset._managed_auto_memory_policy_error(
        policy_dir=policy_dir,
        defaults_reader=lambda: {},
    )

    assert error is not None
    assert "policyHelper" in error


def test_server_managed_settings_cache_memory_redirect_is_detected(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / "remote-settings.json").write_text(
        json.dumps(
            {
                "settings": {
                    "autoMemoryDirectory": "~/server-managed-memory",
                }
            }
        ),
        encoding="utf-8",
    )

    error = claude_reset._managed_auto_memory_policy_error(
        config_dir=config_dir,
        policy_dir=tmp_path / "missing-policy-dir",
        defaults_reader=lambda: {},
    )

    assert error is not None
    assert "server-managed" in error
    assert "autoMemoryDirectory" in error


def test_server_managed_settings_cache_wrong_type_fails_closed(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / "remote-settings.json").mkdir()

    with pytest.raises(RuntimeError, match="server-managed"):
        claude_reset._managed_auto_memory_policy_error(
            config_dir=config_dir,
            policy_dir=tmp_path / "missing-policy-dir",
            defaults_reader=lambda: {},
        )


def test_managed_plans_directory_fails_closed(tmp_path):
    policy_dir = tmp_path / "ClaudeCode"
    policy_dir.mkdir()
    (policy_dir / "managed-settings.json").write_text(
        json.dumps({"plansDirectory": "./managed-plans"}),
        encoding="utf-8",
    )

    error = claude_reset._managed_auto_memory_policy_error(
        policy_dir=policy_dir,
        defaults_reader=lambda: {},
    )

    assert error is not None
    assert "plansDirectory" in error


def test_broad_claude_config_root_fails_before_capability_probe(tmp_path):
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=tmp_path,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is False
    assert "too broad" in (result.error or "")
    assert recorder.calls == []


def test_user_plans_directory_fails_before_capability_probe(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    settings = config_dir / "settings.json"
    settings.write_text(
        json.dumps({"plansDirectory": "./plans"}),
        encoding="utf-8",
    )
    project_plans = tmp_path / "repo/plans"
    project_plans.mkdir(parents=True)
    sentinel = project_plans / "keep.md"
    sentinel.write_text("plan", encoding="utf-8")
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
        _managed_policy_checker=lambda **kwargs: None,
    )

    assert result.succeeded is False
    assert "plansDirectory" in (result.error or "")
    assert recorder.calls == []
    assert sentinel.read_text(encoding="utf-8") == "plan"


def test_current_project_plans_directory_fails_before_capability_probe(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    project = tmp_path / "repo"
    project_settings = project / ".claude/settings.local.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        json.dumps({"plansDirectory": "./plans"}),
        encoding="utf-8",
    )
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
        current_project_root=project,
        _managed_policy_checker=lambda **kwargs: None,
    )

    assert result.succeeded is False
    assert "plansDirectory" in (result.error or "")
    assert str(project_settings) in (result.error or "")
    assert recorder.calls == []


@pytest.mark.parametrize("settings_checkout", ("worktree", "main"))
def test_linked_worktree_checkout_plans_directory_fails_preflight(
    tmp_path,
    settings_checkout,
):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    worktree_root = tmp_path / "worktree"
    current_project = worktree_root / "nested-project"
    current_project.mkdir(parents=True)
    main_checkout = tmp_path / "main-checkout"
    settings_root = (
        worktree_root if settings_checkout == "worktree" else main_checkout
    )
    checkout_settings = settings_root / ".claude/settings.local.json"
    checkout_settings.parent.mkdir(parents=True)
    checkout_settings.write_text(
        json.dumps({"plansDirectory": "./plans"}),
        encoding="utf-8",
    )
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        current_project_root=current_project,
        run_command=recorder,
        _managed_policy_checker=lambda **kwargs: None,
        _git_checkout_roots_resolver=lambda project: (
            (worktree_root, main_checkout),
            None,
        ),
    )

    assert result.succeeded is False
    assert "plansDirectory" in (result.error or "")
    assert str(checkout_settings) in (result.error or "")
    assert recorder.calls == []


def test_git_root_resolution_failure_with_marker_fails_closed(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "repo/nested"
    project.mkdir(parents=True)
    (tmp_path / "repo/.git").write_text("gitdir: unavailable", encoding="utf-8")
    monkeypatch.setattr(
        claude_reset.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            128,
            "",
            "dubious ownership",
        ),
    )

    roots, error = claude_reset._git_checkout_roots(project)

    assert roots == ()
    assert error is not None
    assert "dubious ownership" in error


@pytest.mark.parametrize("change_phase", ("purge", "memory"))
def test_current_project_plans_directory_added_during_reset_fails(
    tmp_path,
    change_phase,
):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    project = tmp_path / "repo"
    project.mkdir()
    project_settings = project / ".claude/settings.local.json"

    def add_plans_directory() -> None:
        project_settings.parent.mkdir(exist_ok=True)
        project_settings.write_text(
            json.dumps({"plansDirectory": "./plans"}),
            encoding="utf-8",
        )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if (
            change_phase == "purge"
            and command[-4:] == ["project", "purge", "--all", "--yes"]
        ):
            add_plans_directory()
        return subprocess.CompletedProcess(command, 0, "", "")

    def memory_deleter(**kwargs):
        if change_phase == "memory":
            add_plans_directory()
        return claude_reset.MemoryDeleteResult()

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        current_project_root=project,
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=memory_deleter,
    )

    assert result.succeeded is False
    assert "plansDirectory" in (result.error or "")


@pytest.mark.parametrize(
    "config_dir",
    (
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var"),
        Path("/private/var"),
        Path("/Volumes/external"),
    ),
)
def test_shared_or_shallow_claude_config_root_is_rejected(config_dir, tmp_path):
    assert claude_reset._config_root_error(config_dir, home=tmp_path) is not None


def test_claude_config_root_rejects_wrong_type_and_symlink(tmp_path):
    wrong_type = tmp_path / "config-file"
    wrong_type.write_text("not a directory", encoding="utf-8")
    real_config = tmp_path / "real-config"
    real_config.mkdir()
    linked_config = tmp_path / "linked-config"
    linked_config.symlink_to(real_config, target_is_directory=True)

    wrong_type_error = claude_reset._config_root_error(
        wrong_type,
        home=tmp_path,
    )
    symlink_error = claude_reset._config_root_error(
        linked_config,
        home=tmp_path,
    )

    assert wrong_type_error is not None
    assert "directory" in wrong_type_error
    assert symlink_error is not None
    assert "symlink" in symlink_error


def test_runtime_quiescence_stops_daemon_then_identity_pinned_cli():
    events: list[str] = []
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )
    scans = iter(((process,), (process,), ()))

    def run_command(command, **kwargs):
        assert command == ["/real/claude", "daemon", "stop", "--any"]
        assert kwargs["env"] == {"CLAUDE_CONFIG_DIR": "/tmp/claude"}
        events.append("daemon")
        return subprocess.CompletedProcess(command, 0, "", "")

    def process_scanner(*args, **kwargs):
        assert args == ("claude",)
        events.append("scan")
        return next(scans)

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={"CLAUDE_CONFIG_DIR": "/tmp/claude"},
        run_command=run_command,
        process_scanner=process_scanner,
        identity_reader=lambda pid: "start-1",
        process_terminator=lambda pid, **kwargs: events.append(
            f"terminate:{pid}:{kwargs['expected_identity']}"
        ),
        process_alive=lambda pid: False,
    )

    assert termination.error is None
    assert termination.terminated == 1
    assert termination.warnings == ()
    assert events[0] == "daemon"
    assert events.count("scan") == 3
    assert "terminate:4242:start-1" in events


def test_runtime_quiescence_reports_process_identity_inspection_error():
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    def identity_reader(pid):
        raise OSError("ps unavailable")

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "",
            "",
        ),
        process_scanner=lambda *args, **kwargs: (process,),
        identity_reader=identity_reader,
        process_terminator=lambda *args, **kwargs: None,
        process_alive=lambda pid: False,
    )

    assert termination.error is not None
    assert "identity" in (termination.error or "")
    assert "ps unavailable" in (termination.error or "")


def test_runtime_quiescence_reports_post_termination_liveness_error():
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )
    scans = iter(((process,), (process,)))

    def process_alive(pid):
        raise OSError("kill probe unavailable")

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "",
            "",
        ),
        process_scanner=lambda *args, **kwargs: next(scans),
        identity_reader=lambda pid: "start-1",
        process_terminator=lambda *args, **kwargs: None,
        process_alive=process_alive,
    )

    assert termination.error is not None
    assert "liveness" in termination.error
    assert "kill probe unavailable" in termination.error


def test_runtime_quiescence_rejects_four_consecutive_respawns():
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "",
            "",
        ),
        process_scanner=lambda *args, **kwargs: (process,),
        identity_reader=lambda pid: "start-1",
        process_terminator=lambda *args, **kwargs: None,
        process_alive=lambda pid: False,
    )

    assert termination.terminated == 4
    assert termination.error is not None
    assert "respawning" in termination.error


def test_runtime_quiescence_keeps_daemon_failure_as_warning_when_scan_is_empty():
    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            "daemon unavailable",
        ),
        process_scanner=lambda *args, **kwargs: (),
        identity_reader=lambda pid: "unused",
        process_terminator=lambda *args, **kwargs: None,
        process_alive=lambda pid: False,
    )

    assert termination.error is None
    assert termination.terminated == 0
    assert termination.warnings == (
        "could not stop Claude daemon: daemon unavailable",
    )


def test_supplemental_cleanup_deletes_only_allowlisted_generated_directories(
    tmp_path,
):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    for name in claude_reset._SUPPLEMENTAL_DIRECTORY_NAMES:
        target = config_dir / name
        target.mkdir()
        (target / "trace.txt").write_text(name, encoding="utf-8")
    for name in ("backups", "plugins", "skills", "unrelated"):
        target = config_dir / name
        target.mkdir()
        (target / "keep.txt").write_text(name, encoding="utf-8")
    stats = config_dir / "stats-cache.json"
    stats.write_text("{}", encoding="utf-8")

    targets = claude_reset._discover_supplemental_targets(config_dir)
    result = claude_reset._delete_supplemental_targets(targets)

    assert result.error is None
    assert result.deleted == len(claude_reset._SUPPLEMENTAL_DIRECTORY_NAMES)
    assert all(not (config_dir / name).exists() for name in claude_reset._SUPPLEMENTAL_DIRECTORY_NAMES)
    assert all(
        (config_dir / name / "keep.txt").read_text(encoding="utf-8") == name
        for name in ("backups", "plugins", "skills", "unrelated")
    )
    assert stats.read_text(encoding="utf-8") == "{}"


def test_supplemental_cleanup_unlinks_final_symlink_without_following_it(
    tmp_path,
):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    linked_target = config_dir / "plans"
    linked_target.symlink_to(outside, target_is_directory=True)

    result = claude_reset._delete_supplemental_targets(
        claude_reset._discover_supplemental_targets(config_dir)
    )

    assert result.error is None
    assert result.deleted == 1
    assert not linked_target.exists()
    assert not linked_target.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_supplemental_cleanup_rejects_symlinked_config_root(tmp_path):
    outside_config = tmp_path / "outside-config"
    generated = outside_config / "plans"
    generated.mkdir(parents=True)
    sentinel = generated / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    config_link = tmp_path / ".claude"
    config_link.symlink_to(outside_config, target_is_directory=True)

    result = claude_reset._delete_supplemental_targets(
        claude_reset._discover_supplemental_targets(config_link)
    )

    assert result.error is not None
    assert "symlink" in result.error
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_supplemental_cleanup_prevalidates_all_targets_before_deletion(tmp_path):
    config_dir = tmp_path / ".claude"
    first = config_dir / "agent-memory"
    first.mkdir(parents=True)
    sentinel = first / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    wrong_type = config_dir / "plans"
    wrong_type.write_text("not a directory", encoding="utf-8")

    result = claude_reset._delete_supplemental_targets(
        claude_reset._discover_supplemental_targets(config_dir)
    )

    assert result.deleted == 0
    assert result.error is not None
    assert str(wrong_type) in result.error
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_supplemental_cleanup_reports_partial_delete_failure(tmp_path):
    config_dir = tmp_path / ".claude"
    first = config_dir / "agent-memory"
    second = config_dir / "plans"
    first.mkdir(parents=True)
    second.mkdir()
    removed: list[Path] = []

    def remove_tree(path):
        if path == second:
            raise OSError("read only")
        removed.append(path)
        path.rmdir()

    result = claude_reset._delete_supplemental_targets(
        claude_reset._discover_supplemental_targets(config_dir),
        remove_tree=remove_tree,
    )

    assert result.deleted == 1
    assert result.error is not None
    assert "read only" in result.error
    assert removed == [first]
    assert second.is_dir()


def test_backup_sanitizer_removes_only_generated_project_entries(tmp_path):
    config_dir = tmp_path / ".claude"
    backups = config_dir / "backups"
    backups.mkdir(parents=True)
    backup = backups / ".claude.json.backup.1"
    backup.write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "user-1"},
                "theme": "dark",
                "projects": {"/repo": {"lastSessionId": "session-1"}},
            }
        ),
        encoding="utf-8",
    )
    unrelated = backups / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    result = claude_reset._sanitize_backup_project_entries(config_dir)

    assert result.error is None
    assert result.sanitized == 1
    assert json.loads(backup.read_text(encoding="utf-8")) == {
        "oauthAccount": {"accountUuid": "user-1"},
        "theme": "dark",
    }
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert claude_reset._backup_project_residuals(config_dir) == ()


def test_backup_sanitizer_rejects_recognized_symlink(tmp_path):
    config_dir = tmp_path / ".claude"
    backups = config_dir / "backups"
    backups.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"projects":{"/outside":{}}}', encoding="utf-8")
    linked_backup = backups / ".claude.json.backup.1"
    linked_backup.symlink_to(outside)

    result = claude_reset._sanitize_backup_project_entries(config_dir)

    assert result.error is not None
    assert "symlink" in result.error
    assert outside.read_text(encoding="utf-8") == '{"projects":{"/outside":{}}}'


@pytest.mark.parametrize(
    ("purge_action", "should_succeed"),
    (("mutate", False), ("delete", True)),
)
def test_official_purge_cannot_change_preserved_backup_values(
    tmp_path,
    purge_action,
    should_succeed,
):
    config_dir = tmp_path / ".claude-custom"
    backups = config_dir / "backups"
    backups.mkdir(parents=True)
    backup = backups / ".claude.json.backup.1"
    backup.write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "user-1"},
                "theme": "dark",
                "projects": {"/repo": {"lastSessionId": "session-1"}},
            }
        ),
        encoding="utf-8",
    )
    global_config = config_dir / ".claude.json"
    global_config.write_text(
        json.dumps({"projects": {"/repo": {}}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            global_config.write_text(
                json.dumps({"projects": {}}),
                encoding="utf-8",
            )
            if purge_action == "delete":
                backup.unlink()
            else:
                backup.write_text(
                    json.dumps(
                        {
                            "oauthAccount": {"accountUuid": "changed"},
                            "theme": "dark",
                            "projects": {},
                        }
                    ),
                    encoding="utf-8",
                )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is should_succeed
    if should_succeed:
        assert result.error is None
    else:
        assert "backup" in (result.error or "")
        assert "changed" in (result.error or "")


def test_official_purge_cannot_change_unrelated_backup_data(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    backups = config_dir / "backups"
    backups.mkdir(parents=True)
    notes = backups / "notes.txt"
    notes.write_text("keep", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            notes.write_text("changed", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is False
    assert "preserved Claude user data changed" in (result.error or "")


@pytest.mark.parametrize(
    "preserved_target",
    (
        "skills",
        ".credentials.json",
        "remote-settings.json",
        "policy-limits.json",
    ),
)
def test_official_purge_cannot_change_preserved_user_scope_data(
    tmp_path,
    preserved_target,
):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    target = config_dir / preserved_target
    if preserved_target == "skills":
        target.mkdir()
        target = target / "personal.md"
    target.write_text("user-authored", encoding="utf-8")

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            if preserved_target in {"skills", "policy-limits.json"}:
                target.write_text("changed", encoding="utf-8")
            else:
                target.unlink()
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is False
    assert "preserved Claude user data" in (result.error or "")


def test_full_reset_runs_official_purge_then_deletes_residual_state(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    custom_memory = tmp_path / "custom-memory"
    custom_memory.mkdir()
    (custom_memory / "MEMORY.md").write_text("generated", encoding="utf-8")
    settings_bytes = json.dumps(
        {
            "theme": "dark",
            "autoMemoryDirectory": str(custom_memory),
        },
        separators=(",", ":"),
    ).encode()
    (config_dir / "settings.json").write_bytes(settings_bytes)
    global_config = config_dir / ".claude.json"
    global_config.write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "user-1"},
                "projects": {"/repo": {"hasTrustDialogAccepted": True}},
            }
        ),
        encoding="utf-8",
    )
    for name in claude_reset._OFFICIAL_DIRECTORY_NAMES:
        generated = config_dir / name
        generated.mkdir()
        (generated / "trace.txt").write_text(name, encoding="utf-8")
    (config_dir / "history.jsonl").write_text("prompt", encoding="utf-8")
    for name in claude_reset._SUPPLEMENTAL_DIRECTORY_NAMES:
        generated = config_dir / name
        generated.mkdir(exist_ok=True)
        (generated / "trace.txt").write_text(name, encoding="utf-8")
    for name in ("backups", "plugins", "skills"):
        preserved = config_dir / name
        preserved.mkdir()
        (preserved / "keep.txt").write_text(name, encoding="utf-8")
    backup_config = config_dir / "backups/.claude.json.backup.1"
    backup_config.write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "user-1"},
                "theme": "dark",
                "projects": {"/repo": {"lastSessionId": "session-1"}},
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run_command(command, **kwargs):
        calls.append((tuple(command), kwargs))
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            for name in claude_reset._OFFICIAL_DIRECTORY_NAMES:
                shutil.rmtree(config_dir / name)
            (config_dir / "history.jsonl").unlink()
            global_config.write_text(
                json.dumps(
                    {
                        "oauthAccount": {"accountUuid": "user-1"},
                        "projects": {},
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=2),
            warnings=(),
        ),
    )

    assert result.succeeded is True
    assert result.discovered_sessions == 2
    assert result.deleted_sessions == 2
    assert result.deleted_memory_stores == 1
    assert result.deleted_residual_targets == len(
        claude_reset._SUPPLEMENTAL_DIRECTORY_NAMES
    ) + 1
    assert result.terminated_processes == 0
    assert (config_dir / "settings.json").read_bytes() == settings_bytes
    assert not custom_memory.exists()
    assert all(
        (config_dir / name / "keep.txt").read_text(encoding="utf-8") == name
        for name in ("backups", "plugins", "skills")
    )
    assert json.loads(backup_config.read_text(encoding="utf-8")) == {
        "oauthAccount": {"accountUuid": "user-1"},
        "theme": "dark",
    }
    claude_commands = [call for call, _ in calls if call[0] == "/real/claude"]
    assert claude_commands == [
        ("/real/claude", "project", "purge", "--help"),
        ("/real/claude", "daemon", "stop", "--any"),
        ("/real/claude", "project", "purge", "--all", "--yes"),
    ]
    assert all(
        kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(config_dir)
        for command, kwargs in calls
        if command[0] == "/real/claude"
    )


def test_full_reset_tolerates_claude_managed_runtime_mutations(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    settings_bytes = json.dumps(
        {
            "enabledPlugins": {"formatter@example": True},
            "theme": "dark",
        },
        separators=(",", ":"),
    ).encode()
    (config_dir / "settings.json").write_bytes(settings_bytes)
    global_config = config_dir / ".claude.json"
    global_config.write_text(
        json.dumps(
            {
                "cachedExperimentData": {"experiment-a": "control"},
                "cachedExperimentFeatures": {"experiment-a": False},
                "cachedGrowthBookFeatures": {"feature-a": False},
                "cachedGrowthBookFeaturesAt": 1,
                "oauthAccount": {"accountUuid": "user-1"},
                "theme": "dark",
                "projects": {"/repo": {"lastSessionId": "session-1"}},
            }
        ),
        encoding="utf-8",
    )

    for name in claude_reset._OFFICIAL_DIRECTORY_NAMES:
        generated = config_dir / name
        generated.mkdir()
        (generated / "trace.txt").write_text(name, encoding="utf-8")
    (config_dir / "history.jsonl").write_text("prompt", encoding="utf-8")
    for name in ("paste-cache", "session-env"):
        generated = config_dir / name
        generated.mkdir()
        (generated / "trace.txt").write_text(name, encoding="utf-8")

    plugin_cache = config_dir / "plugins/cache/example"
    plugin_cache.mkdir(parents=True)
    cached_plugin = plugin_cache / "plugin.json"
    cached_plugin.write_text("old cache", encoding="utf-8")
    plugin_data = config_dir / "plugins/data/example/state.json"
    plugin_data.parent.mkdir(parents=True)
    plugin_data.write_text("persistent user data", encoding="utf-8")

    backups = config_dir / "backups"
    backups.mkdir()
    rotated_backup = backups / ".claude.json.backup.1"
    rotated_backup.write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "user-1"},
                "theme": "dark",
                "projects": {"/repo": {"lastSessionId": "session-1"}},
            }
        ),
        encoding="utf-8",
    )
    replacement_backup = backups / ".claude.json.backup.2"

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            cached_plugin.write_text("refreshed cache", encoding="utf-8")
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            for name in claude_reset._OFFICIAL_DIRECTORY_NAMES:
                shutil.rmtree(config_dir / name)
            (config_dir / "history.jsonl").unlink()
            global_config.write_text(
                json.dumps(
                    {
                        "cachedExperimentData": {
                            "experiment-a": "treatment"
                        },
                        "cachedExperimentFeatures": {
                            "experiment-a": True
                        },
                        "cachedGrowthBookFeatures": {"feature-a": True},
                        "cachedGrowthBookFeaturesAt": 2,
                        "oauthAccount": {"accountUuid": "user-1"},
                        "theme": "dark",
                        "projects": {},
                    }
                ),
                encoding="utf-8",
            )
            rotated_backup.unlink()
            replacement_backup.write_text(
                json.dumps(
                    {
                        "cachedExperimentData": {
                            "experiment-a": "treatment"
                        },
                        "cachedExperimentFeatures": {
                            "experiment-a": True
                        },
                        "cachedGrowthBookFeatures": {"feature-a": True},
                        "cachedGrowthBookFeaturesAt": 2,
                        "oauthAccount": {"accountUuid": "user-1"},
                        "theme": "dark",
                        "projects": {
                            "/repo": {"lastSessionId": "session-1"}
                        },
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=5),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is True, result.error
    assert result.deleted_sessions == 5
    assert result.deleted_memory_stores == 0
    assert result.deleted_residual_targets == 3
    assert (config_dir / "settings.json").read_bytes() == settings_bytes
    assert plugin_data.read_text(encoding="utf-8") == "persistent user data"
    assert cached_plugin.read_text(encoding="utf-8") == "refreshed cache"
    assert not (config_dir / "paste-cache").exists()
    assert not (config_dir / "session-env").exists()
    assert json.loads(replacement_backup.read_text(encoding="utf-8")) == {
        "cachedExperimentData": {"experiment-a": "treatment"},
        "cachedExperimentFeatures": {"experiment-a": True},
        "cachedGrowthBookFeatures": {"feature-a": True},
        "cachedGrowthBookFeaturesAt": 2,
        "oauthAccount": {"accountUuid": "user-1"},
        "theme": "dark",
    }


def test_full_reset_rejects_plugin_persistent_data_mutation(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    plugin_data = config_dir / "plugins/data/example/state.json"
    plugin_data.parent.mkdir(parents=True)
    plugin_data.write_text("keep", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            plugin_data.write_text("changed", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is False
    assert "plugins/data" in (result.error or "")


def test_full_reset_reports_memory_backend_exception(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def memory_deleter(**kwargs):
        raise OSError("memory volume unavailable")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _memory_deleter=memory_deleter,
    )

    assert result.succeeded is False
    assert "memory volume unavailable" in (result.error or "")


def test_full_reset_rechecks_managed_policy_before_final_success(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    policy_checks = 0
    memory_delete_called = False

    def managed_policy_checker(**kwargs):
        nonlocal policy_checks
        assert kwargs["config_dir"] == config_dir
        policy_checks += 1
        if policy_checks == 3:
            return "Claude server-managed autoMemoryDirectory appeared"
        return None

    def memory_deleter(**kwargs):
        nonlocal memory_delete_called
        memory_delete_called = True
        return claude_reset.MemoryDeleteResult()

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=managed_policy_checker,
        _memory_deleter=memory_deleter,
    )

    assert result.succeeded is False
    assert "server-managed" in (result.error or "")
    assert policy_checks == 3
    assert memory_delete_called is True


def test_full_reset_never_deletes_memory_path_changed_after_preflight(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    original_memory = tmp_path / "original-memory"
    original_memory.mkdir()
    (original_memory / "MEMORY.md").write_text("original", encoding="utf-8")
    changed_memory = tmp_path / "changed-memory"
    changed_memory.mkdir()
    changed_sentinel = changed_memory / "MEMORY.md"
    changed_sentinel.write_text("must survive", encoding="utf-8")
    settings = config_dir / "settings.json"
    settings.write_text(
        json.dumps({"autoMemoryDirectory": str(original_memory)}),
        encoding="utf-8",
    )
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def memory_deleter(**kwargs):
        settings.write_text(
            json.dumps({"autoMemoryDirectory": str(changed_memory)}),
            encoding="utf-8",
        )
        return claude_reset.delete_all_memory(**kwargs)

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
        _memory_deleter=memory_deleter,
    )

    assert result.succeeded is False
    assert "settings changed" in (result.error or "")
    assert not original_memory.exists()
    assert changed_sentinel.read_text(encoding="utf-8") == "must survive"


def test_memory_inventory_must_match_snapshotted_settings_path(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    approved_memory = tmp_path / "approved-memory"
    approved_memory.mkdir()
    approved_sentinel = approved_memory / "MEMORY.md"
    approved_sentinel.write_text("approved", encoding="utf-8")
    raced_memory = tmp_path / "raced-memory"
    raced_memory.mkdir()
    raced_sentinel = raced_memory / "MEMORY.md"
    raced_sentinel.write_text("must survive", encoding="utf-8")
    (config_dir / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(approved_memory)}),
        encoding="utf-8",
    )
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
        _managed_policy_checker=lambda **kwargs: None,
        _memory_scanner=lambda **kwargs: claude_reset.MemoryInventory(
            client="claude",
            stores=(
                MemoryStore(
                    path=raced_memory,
                    source="claude-settings",
                    file_count=1,
                ),
            ),
            file_count=1,
            scope="test",
        ),
    )

    assert result.succeeded is False
    assert "changed during preflight" in (result.error or "")
    assert recorder.calls == []
    assert approved_sentinel.read_text(encoding="utf-8") == "approved"
    assert raced_sentinel.read_text(encoding="utf-8") == "must survive"


def test_official_purge_no_matching_state_continues_supplemental_cleanup(
    tmp_path,
):
    config_dir = tmp_path / ".claude-custom"
    plans = config_dir / "plans"
    plans.mkdir(parents=True)
    sentinel = plans / "stale-plan.md"
    sentinel.write_text("stale", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "No Claude Code project state found under test config.",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=1),
            warnings=(),
        ),
        _managed_policy_checker=lambda **kwargs: None,
    )

    assert result.succeeded is True, result.error
    assert result.deleted_sessions == 1
    assert result.deleted_residual_targets == 1
    assert not plans.exists()


def test_official_purge_failure_prevents_supplemental_deletion(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    plans = config_dir / "plans"
    plans.mkdir(parents=True)
    sentinel = plans / "keep.md"
    sentinel.write_text("keep", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {"/repo": {}}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            return subprocess.CompletedProcess(command, 9, "", "purge denied")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=1),
            warnings=(),
        ),
        _memory_deleter=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("memory deletion must not run after purge failure")
        ),
    )

    assert result.succeeded is False
    assert "purge denied" in (result.error or "")
    assert result.deleted_sessions == 0
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_full_reset_reports_final_memory_scan_exception(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    scans = 0

    def memory_scanner(**kwargs):
        nonlocal scans
        scans += 1
        if scans == 1:
            return claude_reset.MemoryInventory(
                client="claude",
                stores=(),
                file_count=0,
                scope="test",
            )
        raise OSError("memory rescan unavailable")

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _memory_scanner=memory_scanner,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is False
    assert "memory rescan unavailable" in (result.error or "")


def test_memory_preflight_exception_fails_before_capability_probe(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
        _memory_scanner=lambda **kwargs: (_ for _ in ()).throw(
            OSError("memory inventory unavailable")
        ),
    )

    assert result.succeeded is False
    assert "memory inventory unavailable" in (result.error or "")
    assert recorder.calls == []


@pytest.mark.parametrize("purge_returncode", (0, 1))
def test_official_purge_residual_is_failure(tmp_path, purge_returncode):
    config_dir = tmp_path / ".claude-custom"
    residual = config_dir / "projects/repo/session.jsonl"
    residual.parent.mkdir(parents=True)
    residual.write_text("conversation", encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            return subprocess.CompletedProcess(
                command,
                purge_returncode,
                "",
                (
                    "No Claude Code project state found under test config."
                    if purge_returncode == 1
                    else ""
                ),
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=1),
            warnings=(),
        ),
        _memory_deleter=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("memory deletion must wait for purge verification")
        ),
    )

    assert result.succeeded is False
    assert "not empty" in (result.error or "")
    assert residual.read_text(encoding="utf-8") == "conversation"


def test_completed_session_count_survives_settings_preservation_failure(
    tmp_path,
):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    settings = config_dir / "settings.json"
    settings.write_text('{"theme":"dark"}', encoding="utf-8")
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            settings.write_text('{"theme":"light"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=4),
            warnings=(),
        ),
    )

    assert result.succeeded is False
    assert "settings changed" in (result.error or "")
    assert result.discovered_sessions == 4
    assert result.deleted_sessions == 4


def test_full_reset_reports_all_deleted_memory_stores(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    scans = 0

    def memory_scanner(**kwargs):
        nonlocal scans
        scans += 1
        if scans == 1:
            stores = (
                MemoryStore(
                    path=config_dir / "projects/repo-one/memory",
                    source="claude-project",
                    file_count=3,
                ),
                MemoryStore(
                    path=config_dir / "projects/repo-two/memory",
                    source="claude-project",
                    file_count=2,
                ),
            )
            return claude_reset.MemoryInventory(
                client="claude",
                stores=stores,
                file_count=5,
                scope="test",
            )
        return claude_reset.MemoryInventory(
            client="claude",
            stores=(),
            file_count=0,
            scope="test",
        )

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _memory_scanner=memory_scanner,
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is True
    assert result.deleted_memory_stores == 2


def test_final_process_respawn_makes_reset_fail(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    process = ClientProcess(
        pid=5151,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )
    scans = iter(((), (process,), (process,)))

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: next(scans),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is False
    assert "1 Claude process" in (result.error or "")


def test_process_start_after_state_verification_makes_reset_fail(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"projects": {}}),
        encoding="utf-8",
    )
    process = ClientProcess(
        pid=6161,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )
    scans = iter(((), (), (process,)))

    def run_command(command, **kwargs):
        stdout = (
            "Options: --all --yes"
            if command[-3:] == ["project", "purge", "--help"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: next(scans),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
        _memory_deleter=lambda **kwargs: claude_reset.MemoryDeleteResult(),
    )

    assert result.succeeded is False
    assert "1 Claude process" in (result.error or "")


def test_default_config_keeps_claude_config_dir_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/stale/test/value")
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    global_config = tmp_path / ".claude.json"
    global_config.write_text(
        json.dumps({"theme": "dark", "projects": {"/repo": {}}}),
        encoding="utf-8",
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run_command(command, **kwargs):
        calls.append((tuple(command), kwargs))
        if command[-3:] == ["project", "purge", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Options: --all --yes",
                "",
            )
        if command[-4:] == ["project", "purge", "--all", "--yes"]:
            global_config.write_text(
                json.dumps({"theme": "dark", "projects": {}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=None,
        real_claude_binary="/real/claude",
        run_command=run_command,
        _process_scanner=lambda *args, **kwargs: (),
        _session_scanner=lambda **kwargs: SimpleNamespace(
            sessions=SimpleNamespace(total=0),
            warnings=(),
        ),
    )

    assert result.succeeded is True
    assert all(
        "CLAUDE_CONFIG_DIR" not in kwargs["env"]
        for command, kwargs in calls
        if command[0] == "/real/claude"
    )
