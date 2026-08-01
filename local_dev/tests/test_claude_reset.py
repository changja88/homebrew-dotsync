from __future__ import annotations

import json
import subprocess

from local_dev.serena_mcp_management import claude_reset


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
    )

    assert result.succeeded is True
    assert recorder.calls[0][1]["env"]["CLAUDE_CONFIG_DIR"] == str(config_dir)


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
