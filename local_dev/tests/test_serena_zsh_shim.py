from pathlib import Path
import os
import subprocess
import pytest

from local_dev.serena_mcp_management.serena_zsh_shim import (
    default_python_executable,
    install_zshrc_shim,
    main,
    render_zsh_shim,
    uninstall_zshrc_shim,
)


def test_render_zsh_shim_defines_codex_and_claude_functions():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    assert 'SERENA_AGENT_LAUNCHER="/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"' in text
    assert 'SERENA_AGENT_PYTHON="/repo/.venv/bin/python3"' in text
    assert "codex() {" in text
    assert "claude() {" in text
    assert "SERENA_AGENT_CLIENT=codex" in text
    assert "SERENA_AGENT_QUIET=1" in text
    assert 'SERENA_AGENT_INTERACTIVE="$interactive"' in text
    assert "SERENA_REAL_CODEX=/opt/homebrew/bin/codex" in text
    assert "SERENA_AGENT_CLIENT=claude" in text
    assert "SERENA_REAL_CLAUDE=/opt/homebrew/bin/claude" in text
    assert '"$SERENA_AGENT_PYTHON" "$SERENA_AGENT_LAUNCHER" "$@"' in text
    assert "_dotsync_agent_serena_project_available" in text
    assert '--effort xhigh' not in text


def test_render_zsh_shim_defines_graphify_split_helpers():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The four preflight rows must be checked individually, otherwise a
    # missing global skill or graph cannot surface. A single combined
    # probe was insufficient and showed false ✓ when run outside a project.
    assert "_dotsync_agent_graphify_global_installed" in text
    assert "_dotsync_agent_graphify_graph_built" in text
    assert "_dotsync_agent_graphify_integration_installed" in text
    assert "_dotsync_agent_graphify_hooks_installed" in text


def test_render_zsh_shim_graphify_global_helper_branches_on_client():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The user-level skill lives under ~/.claude/skills/graphify for claude
    # and ~/.codex/skills/graphify for codex (graphifyy 0.8.x
    # `graphify install --platform codex` 실측 경로).
    assert "$HOME/.claude/skills/graphify" in text
    assert "$HOME/.codex/skills/graphify" in text


def test_render_zsh_shim_graphify_graph_helper_checks_graph_json():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The graph row reflects whether `graphify` ran in this project root.
    assert "graphify-out/graph.json" in text


def test_render_zsh_shim_graphify_integration_helper_branches_on_client():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # claude integration: CLAUDE.md + .claude/settings.json
    # codex integration: AGENTS.md + .codex/hooks.json
    assert "CLAUDE.md" in text
    assert ".claude/settings.json" in text
    assert "AGENTS.md" in text
    assert ".codex/hooks.json" in text


def test_render_zsh_shim_graphify_integration_helper_checks_file_content():
    """File existence alone is too loose — projects with their own CLAUDE.md
    or .claude/settings.json (unrelated to graphify) get falsely marked as
    'integration installed'. The check must also confirm graphify-specific
    content, mirroring the hook-marker pattern used by hooks_installed.
    """
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The markdown section references `graphify-out`; Codex hook files use
    # `graphify hook-check`, while Claude settings still mention graphify-out.
    assert "grep -q" in text
    assert "graphify-out" in text
    assert "hook-check" in text


@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_integration_returns_missing_when_files_lack_graphify(tmp_path):
    """Empty {} settings.json + dotsync's own CLAUDE.md must NOT be detected
    as a graphify integration."""
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# CLAUDE.md\n\nThis project has nothing to do with graphify.\n")
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}\n")

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_integration_installed {project} claude; "
                "print integration=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "integration=1" in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_integration_returns_installed_when_content_present(tmp_path):
    """When CLAUDE.md has the `## graphify` section AND .claude/settings.json
    references graphify-out, the integration is genuinely installed."""
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(
        "# CLAUDE.md\n\n## graphify\n\nThis project has a graphify knowledge graph at graphify-out/.\n"
    )
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        '{"hooks":{"PreToolUse":[{"hooks":[{"command":"graphify-out/graph.json"}]}]}}\n'
    )

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_integration_installed {project} claude; "
                "print integration=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "integration=0" in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_integration_codex_returns_missing_when_files_lack_graphify(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# AGENTS.md\n\nUnrelated content.\n")
    codex_dir = project / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text("{}\n")

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_integration_installed {project} codex; "
                "print integration=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "integration=1" in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_integration_codex_returns_installed_when_hook_check_present(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        "# AGENTS.md\n\n"
        "## graphify\n\n"
        "This project has a graphify knowledge graph at graphify-out/.\n"
    )
    codex_dir = project / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text(
        '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command",'
        '"command":"/Users/hyun/.local/bin/graphify hook-check"}]}]}}\n'
    )

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_integration_installed {project} codex; "
                "print integration=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "integration=0" in result.stdout


def test_render_zsh_shim_graphify_hooks_check_uses_project_root():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The probe must accept the resolved project root (not $PWD) so that the
    # status reflects the same scope used elsewhere in the preflight.
    assert '_dotsync_agent_graphify_hooks_installed "$project_root"' in text
    assert "config core.hooksPath" in text
    assert 'hooks_dir="$project_root/.git/hooks"' in text
    assert 'pc="$hooks_dir/post-commit"' in text
    assert 'pco="$hooks_dir/post-checkout"' in text
    assert "graphify-hook-start" in text
    assert "graphify-checkout-hook-start" in text


@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_hooks_check_respects_core_hooks_path(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    project = tmp_path / "project"
    hooks_dir = project / ".githooks"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "core.hooksPath", ".githooks"],
        check=True,
        capture_output=True,
    )
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "post-commit").write_text("#!/bin/sh\n# graphify-hook-start\n")
    (hooks_dir / "post-checkout").write_text("#!/bin/sh\n# graphify-checkout-hook-start\n")

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_hooks_installed {project}; "
                "print hooks=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "hooks=0" in result.stdout


def test_render_zsh_shim_defers_clear_to_launcher_after_codex_cleanup():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    codex_body = text.split("\ncodex() {", 1)[1]

    assert "printf '\\e[3J\\e[H\\e[2J'" not in codex_body
    assert 'SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"' in codex_body
    assert 'SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"' in codex_body
    assert codex_body.index('SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"') < codex_body.index('"$SERENA_AGENT_PYTHON" "$SERENA_AGENT_LAUNCHER" "$@"')


def test_render_zsh_shim_defers_clear_to_launcher_after_claude_cleanup():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    claude_body = text.split("\nclaude() {", 1)[1].split("\ncodex() {", 1)[0]

    assert "printf '\\e[3J\\e[H\\e[2J'" not in claude_body
    assert 'SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"' in claude_body
    assert claude_body.rindex(
        'SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"'
    ) < claude_body.rindex('"$SERENA_AGENT_PYTHON" "$SERENA_AGENT_LAUNCHER" "$@"')


def test_render_zsh_shim_does_not_depend_on_path_wrapper_installation():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    assert "~/.local/bin" not in text
    assert "install_serena_agent_wrappers" not in text
    assert "SERENA_MCP_SUPERVISOR_DIR" not in text
    assert "_acquire_serena_mcp_instance" not in text
    assert "_configure_codex_serena_mcp" not in text
    assert "_configure_claude_serena_mcp" not in text


def test_render_zsh_shim_marks_missing_serena_project_in_preflight():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The shim detects serena availability and sets SERENA_AGENT_PREFLIGHT_SERENA_STATUS
    # to "managed" or "missing" — the text representation is handled by the Python launcher.
    assert "SERENA_AGENT_PREFLIGHT_SERENA_STATUS" in text
    assert 'serena_status="managed"' in text
    assert 'serena_status="missing"' in text


def test_zsh_shim_cli_prints_installed_launcher_snippet(monkeypatch, capsys):
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.shutil.which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.executable", "/opt/homebrew/bin/python3.12")

    assert main([]) == 0

    output = capsys.readouterr().out
    assert 'SERENA_AGENT_LAUNCHER="' in output
    assert "local_dev/serena_mcp_management/serena_agent_launcher.py" in output
    assert 'SERENA_AGENT_PYTHON="/opt/homebrew/bin/python3.12"' in output
    assert "SERENA_REAL_CODEX=/opt/homebrew/bin/codex" in output
    assert "SERENA_REAL_CLAUDE=/opt/homebrew/bin/claude" in output


def test_default_python_executable_prefers_python_312_when_current_is_too_old(monkeypatch, tmp_path):
    python312 = tmp_path / "python3.12"
    python312.write_text("")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.version_info", (3, 9, 6))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.PYTHON_CANDIDATES", (python312,))

    assert default_python_executable() == python312


def test_default_python_executable_prefers_stable_candidate_over_ephemeral_venv(monkeypatch, tmp_path):
    # `make install-shim` may run this generator under an ephemeral uv/venv
    # python (3.12+) that uv can later garbage-collect, which leaves
    # SERENA_AGENT_PYTHON dangling (the v0.1.x uv-3.13 breakage). A durable
    # homebrew/framework candidate must win over the running interpreter even
    # when that interpreter is new enough to run the launcher itself.
    stable = tmp_path / "python3.12"
    stable.write_text("")
    ephemeral_venv = tmp_path / ".venv" / "bin" / "python3"
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.version_info", (3, 13, 1))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.executable", str(ephemeral_venv))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.PYTHON_CANDIDATES", (stable,))

    assert default_python_executable() == stable


def test_zsh_shim_cli_install_honors_explicit_python_executable(monkeypatch, tmp_path):
    # Auto-detection would record the interpreter currently running the
    # generator; an explicit --python-executable must override it so the shim
    # can point at a self-contained venv instead of whichever python ran make.
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_zsh_shim.sys.executable",
        "/Users/me/Desktop/homebrew-dotsync/.venv/bin/python3",
    )
    rc = tmp_path / ".zshrc"
    rc.write_text("# existing\n")

    chosen = "/Users/me/Desktop/dotsync_config/agent_launcher/.venv/bin/python3"
    assert main(["--install-zshrc", "--rc-path", str(rc), "--python-executable", chosen]) == 0

    text = rc.read_text()
    assert f'SERENA_AGENT_PYTHON="{chosen}"' in text
    assert "/homebrew-dotsync/.venv/bin/python3" not in text


@pytest.mark.no_subprocess_block
def test_zsh_shim_passes_argument_commands_directly_to_real_binary(tmp_path):
    shim_path, real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            f"source {shim_path}; codex --help",
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == f"REAL {real_codex} --help\n"


@pytest.mark.no_subprocess_block
def test_zsh_shim_does_not_cleanup_without_interactive_confirmation(tmp_path):
    shim_path, real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    codex_home = tmp_path / ".codex"
    memory_file = codex_home / "memories" / "note.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("keep")

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            f"source {shim_path}; codex",
        ],
        env={**os.environ, "HOME": str(tmp_path), "CODEX_HOME": str(codex_home)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == f"REAL {real_codex}\n"
    assert memory_file.exists()


@pytest.mark.no_subprocess_block
def test_zsh_shim_passes_project_root_to_launcher(tmp_path):
    project = tmp_path / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    (project / ".serena").mkdir()
    (project / ".serena" / "project.yml").write_text("name: project\n")
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            f"cd {nested}; source {shim_path}; print root=$(_dotsync_agent_project_root \"$PWD\")",
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert f"root={project}" in result.stdout
    assert 'SERENA_AGENT_PROJECT_ROOT="$project_root"' in shim_path.read_text()


@pytest.mark.no_subprocess_block
def test_zsh_shim_should_manage_only_tty_no_arg_agent_starts(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                "_dotsync_agent_should_manage_launch 1 0; print managed_empty=$?; "
                "_dotsync_agent_should_manage_launch 1 1; print managed_args=$?; "
                "_dotsync_agent_should_manage_launch 0 0; print managed_notty=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "managed_empty=0" in result.stdout
    assert "managed_args=1" in result.stdout
    assert "managed_notty=1" in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_recognizes_interactive_claude_auth_profile_commands(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                "_dotsync_agent_is_claude_profile_command 1 auth login; "
                "print login=$?; "
                "_dotsync_agent_is_claude_profile_command 1 auth status --json; "
                "print status=$?; "
                "_dotsync_agent_is_claude_profile_command 0 auth login; "
                "print notty=$?; "
                "_dotsync_agent_is_claude_profile_command 1 --help; "
                "print help=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "login=0" in result.stdout
    assert "status=0" in result.stdout
    assert "notty=1" in result.stdout
    assert "help=1" in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_recognizes_interactive_claude_session_resume_commands(tmp_path):
    """`claude -c`/`--continue`/`-r`/`--resume` resume an existing session, so
    they must be managed (Serena scoped MCP + preflight + tab profile) just like
    a bare `claude`. Non-session invocations (`-p` pipe, `--version`, `mcp`
    subcommands) stay pass-through, and only a tty session counts."""
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                "_dotsync_agent_is_claude_session_command 1 -c; print continue_short=$?; "
                "_dotsync_agent_is_claude_session_command 1 --continue; print continue_long=$?; "
                "_dotsync_agent_is_claude_session_command 1 -r; print resume_short=$?; "
                "_dotsync_agent_is_claude_session_command 1 -r abc123; print resume_value=$?; "
                "_dotsync_agent_is_claude_session_command 1 --resume; print resume_long=$?; "
                "_dotsync_agent_is_claude_session_command 1 -p hi; print print_mode=$?; "
                "_dotsync_agent_is_claude_session_command 1 --version; print version=$?; "
                "_dotsync_agent_is_claude_session_command 1 mcp list; print mcp=$?; "
                "_dotsync_agent_is_claude_session_command 0 -c; print notty=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "continue_short=0" in result.stdout
    assert "continue_long=0" in result.stdout
    assert "resume_short=0" in result.stdout
    assert "resume_value=0" in result.stdout
    assert "resume_long=0" in result.stdout
    assert "print_mode=1" in result.stdout
    assert "version=1" in result.stdout
    assert "mcp=1" in result.stdout
    assert "notty=1" in result.stdout


def test_render_zsh_shim_routes_claude_auth_through_profile_only_launcher():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    claude_body = text.split("\nclaude() {", 1)[1].split("\ncodex() {", 1)[0]
    assert "_dotsync_agent_is_claude_profile_command" in claude_body
    assert "SERENA_AGENT_PROFILE_ONLY=1" in claude_body


def test_render_zsh_shim_routes_claude_session_resume_through_managed_launcher():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    # The session-resume flags are recognized by a dedicated helper (defined
    # once, above the functions) and the claude() body must consult it so that
    # `claude -c`/`-r` are managed instead of passed straight to the binary.
    assert "-c|--continue|-r|--resume" in text
    claude_body = text.split("\nclaude() {", 1)[1].split("\ncodex() {", 1)[0]
    assert "_dotsync_agent_is_claude_session_command" in claude_body
    # codex has no session-resume concept — the helper is claude-only.
    codex_body = text.split("\ncodex() {", 1)[1]
    assert "_dotsync_agent_is_claude_session_command" not in codex_body


def test_install_zshrc_shim_replaces_managed_block(tmp_path):
    rc_path = tmp_path / ".zshrc"
    rc_path.write_text(
        "before\n"
        "# >>> dotsync serena agent launcher >>>\n"
        "old\n"
        "# <<< dotsync serena agent launcher <<<\n"
        "after\n"
    )

    install_zshrc_shim(
        rc_path=rc_path,
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    text = rc_path.read_text()
    assert "before\n" in text
    assert "after\n" in text
    assert "\nold\n" not in text
    assert (tmp_path / ".zshrc.dotsync-serena.bak").read_text().startswith("before\n")


def test_uninstall_zshrc_shim_removes_managed_block_and_writes_backup(tmp_path):
    rc_path = tmp_path / ".zshrc"
    rc_path.write_text(
        "before\n"
        "# >>> dotsync serena agent launcher >>>\n"
        "managed body\n"
        "# <<< dotsync serena agent launcher <<<\n"
        "after\n"
    )

    backup_path = uninstall_zshrc_shim(rc_path=rc_path)

    text = rc_path.read_text()
    # The managed block is gone; surrounding lines untouched.
    assert "managed body" not in text
    assert "dotsync serena agent launcher" not in text
    assert "before\n" in text
    assert "after\n" in text
    # Backup uses the same convention as install.
    assert backup_path == tmp_path / ".zshrc.dotsync-serena.bak"
    backup_text = backup_path.read_text()
    assert "managed body" in backup_text
    assert "dotsync serena agent launcher" in backup_text


def test_uninstall_zshrc_shim_idempotent_when_block_absent(tmp_path):
    rc_path = tmp_path / ".zshrc"
    original = "alias ll='ls -lah'\nexport FOO=bar\n"
    rc_path.write_text(original)

    backup_path = uninstall_zshrc_shim(rc_path=rc_path)

    # Idempotent: file content unchanged, no error raised, backup still written
    # so the operation always leaves a recoverable snapshot.
    assert rc_path.read_text() == original
    assert backup_path.read_text() == original


def test_uninstall_zshrc_shim_noop_when_rc_missing(tmp_path):
    rc_path = tmp_path / ".zshrc"
    # rc file does not exist; uninstall must not crash and must not create one.
    backup_path = uninstall_zshrc_shim(rc_path=rc_path)

    assert not rc_path.exists()
    assert backup_path is None


def test_zsh_shim_cli_uninstalls_managed_block(monkeypatch, tmp_path, capsys):
    rc_path = tmp_path / ".zshrc"
    rc_path.write_text(
        "keep\n"
        "# >>> dotsync serena agent launcher >>>\n"
        "managed body\n"
        "# <<< dotsync serena agent launcher <<<\n"
        "tail\n"
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.shutil.which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.executable", "/opt/homebrew/bin/python3.12")

    assert main(["--uninstall-zshrc", "--rc-path", str(rc_path)]) == 0

    text = rc_path.read_text()
    assert "managed body" not in text
    assert "dotsync serena agent launcher" not in text
    assert "keep\n" in text
    assert "tail\n" in text
    output = capsys.readouterr().out
    # Confirmation message tells the user what happened so they don't have to
    # diff their rc file to be sure.
    assert f"removed Serena zsh shim from {rc_path}" in output


def test_zsh_shim_cli_installs_into_selected_rc_path(monkeypatch, tmp_path, capsys):
    rc_path = tmp_path / ".zshrc"
    rc_path.write_text("existing\n")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.shutil.which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.executable", "/opt/homebrew/bin/python3.12")

    assert main(["--install-zshrc", "--rc-path", str(rc_path)]) == 0

    output = capsys.readouterr().out
    assert f"installed Serena zsh shim into {rc_path}" in output
    assert "SERENA_AGENT_LAUNCHER" in rc_path.read_text()


def test_render_zsh_shim_packs_preflight_status_env_vars():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )
    assert "SERENA_AGENT_PREFLIGHT_SERENA_STATUS" in text
    # The launcher reads four split graphify statuses; the old combined
    # SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS is no longer consumed and must
    # not be exported (otherwise reviewers will assume it still drives UI).
    assert "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS" in text
    assert "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS" in text
    assert "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS" in text
    assert "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS" in text
    assert "SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS=" not in text


def test_render_zsh_shim_no_longer_exports_cleanup_prediction_env():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    assert "SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE" not in text
    assert "SERENA_AGENT_PREFLIGHT_MEMORY_VALUE" not in text
    assert "jq -e" not in text


def test_render_zsh_shim_no_longer_references_gum():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )
    assert "gum" not in text
    assert "_dotsync_agent_preflight" not in text
    assert "_dotsync_agent_cleanup_claude" not in text


def _write_zsh_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    real_codex = tmp_path / "real-codex"
    real_claude = tmp_path / "real-claude"
    launcher = tmp_path / "launcher.py"
    python = tmp_path / "python"

    real_codex.write_text("#!/bin/sh\nprintf 'REAL %s' \"$0\"\nfor arg in \"$@\"; do printf ' %s' \"$arg\"; done\nprintf '\\n'\n")
    real_claude.write_text("#!/bin/sh\nprintf 'REAL %s' \"$0\"\nfor arg in \"$@\"; do printf ' %s' \"$arg\"; done\nprintf '\\n'\n")
    launcher.write_text("#!/bin/sh\nprintf 'LAUNCHER PROJECT=%s REAL_CODEX=%s REAL_CLAUDE=%s ARGS=%s\\n' \"$SERENA_AGENT_PROJECT_ROOT\" \"$SERENA_REAL_CODEX\" \"$SERENA_REAL_CLAUDE\" \"$*\"\n")
    python.write_text("#!/bin/sh\nscript=\"$1\"\nshift\nexec \"$script\" \"$@\"\n")
    for path in (real_codex, real_claude, launcher, python):
        path.chmod(0o755)

    shim = render_zsh_shim(
        launcher_path=launcher,
        python_executable=python,
        codex_binary=real_codex,
        claude_binary=real_claude,
    )
    shim_path = tmp_path / "shim.zsh"
    shim_path.write_text(shim)
    return shim_path, real_codex, real_claude, launcher


def test_render_zsh_shim_puts_uv_tool_bin_on_path():
    """serena/graphify CLI는 uv tool bin(~/.local/bin)에 산다. 기본 PATH에 없는
    머신에서도 launcher와 그 아래 agent 세션이 같은 CLI를 보도록 managed block이
    PATH를 보강한다."""
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )
    assert 'export PATH="$HOME/.local/bin:$PATH"' in text
