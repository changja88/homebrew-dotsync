from pathlib import Path
import os
import shlex
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


@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_hooks_check_resolves_linked_worktree_common_dir(
    tmp_path,
):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(
        tmp_path
    )
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    subprocess.run(
        ["git", "-C", str(repository), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(worktree),
        ],
        check=True,
        capture_output=True,
    )
    hooks_dir = repository / ".git" / "hooks"
    (hooks_dir / "post-commit").write_text(
        "#!/bin/sh\n# graphify-hook-start\n"
    )
    (hooks_dir / "post-checkout").write_text(
        "#!/bin/sh\n# graphify-checkout-hook-start\n"
    )

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_hooks_installed {worktree}; "
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
def test_zsh_shim_project_root_prefers_nested_worktree_boundary(tmp_path):
    parent = tmp_path / "parent"
    nested = parent / "worktrees" / "feature"
    child = nested / "src"
    (parent / ".serena").mkdir(parents=True)
    (parent / ".serena" / "project.yml").write_text("project_name: parent\n")
    nested.mkdir(parents=True)
    (nested / ".git").write_text("gitdir: /tmp/fake\n")
    child.mkdir()
    shim_path, *_ = _write_zsh_fixture(tmp_path)

    result = subprocess.run(
        [
            "zsh",
            "-df",
            "-c",
            'source "$1"; cd "$2"; _dotsync_agent_project_root "$PWD"',
            "zsh",
            str(shim_path),
            str(child),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == str(nested)


@pytest.mark.no_subprocess_block
def test_zsh_shim_should_manage_tty_session_commands_only(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                "_dotsync_agent_should_manage_launch 1 codex; print managed_empty=$?; "
                "_dotsync_agent_should_manage_launch 1 codex exec; print managed_args=$?; "
                "_dotsync_agent_should_manage_launch 0 codex; print managed_notty=$?"
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


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "client,args",
    [
        ("codex", ""),
        ("codex", "resume"),
        ("codex", "fork"),
        ("claude", ""),
        ("claude", "-c"),
        ("claude", "--continue"),
        ("claude", "-r session-id"),
        ("claude", "--resume session-id"),
    ],
)
def test_zsh_matcher_accepts_session_managing_interactive_commands(
    tmp_path, client, args
):
    shim_path, *_ = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            f"source {shlex.quote(str(shim_path))}; "
            f"_dotsync_agent_should_manage_launch 1 {client} {args}",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "interactive,client,args",
    [
        ("0", "codex", ""),
        ("1", "codex", "exec"),
        ("1", "codex", "--version"),
        ("1", "claude", "-p prompt"),
        ("1", "claude", "--help"),
        ("1", "claude", "-r session-id --settings /tmp/custom.json"),
        ("1", "claude", "--resume session-id --settings={}"),
    ],
)
def test_zsh_matcher_bypasses_non_session_or_user_settings_commands(
    tmp_path, interactive, client, args
):
    shim_path, *_ = _write_zsh_fixture(tmp_path)
    result = subprocess.run(
        [
            "zsh",
            "-fc",
            f"source {shlex.quote(str(shim_path))}; "
            f"_dotsync_agent_should_manage_launch {interactive} {client} {args}",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0


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
