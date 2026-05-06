from pathlib import Path
import os
import subprocess
import pytest

from local_dev.serena_mcp_management.serena_zsh_shim import (
    default_python_executable,
    install_zshrc_shim,
    main,
    render_zsh_shim,
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
    assert "_dotsync_agent_ensure_serena" in text
    assert "serena project create" in text
    assert '--effort xhigh' not in text


def test_render_zsh_shim_preserves_agent_cleanup_preflight():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    assert "cleanup" in text
    assert "memory" in text
    assert "read -r reply" in text
    assert "_dotsync_agent_cleanup_codex" in text
    assert "_dotsync_agent_cleanup_claude" in text
    assert "sessions delete/keep" in text
    assert "memory reset" in text
    assert "find \"$HOME/.claude/projects" in text
    assert "find \"$codex_home/memories\"" in text
    assert "session_meta" in text
    assert "jq -e" in text


def test_render_zsh_shim_includes_graphify_status_guidance():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    assert "_dotsync_agent_graphify_available" in text
    assert "_dotsync_agent_graphify_preflight_state" in text
    assert "command -v graphify" in text
    assert "graphify" in text
    assert "/graphify ." in text


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
    assert codex_body.index("_dotsync_agent_cleanup_codex") < codex_body.index('SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"')
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
    assert claude_body.index("_dotsync_agent_cleanup_claude") < claude_body.index('SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"')
    assert claude_body.index('SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive"') < claude_body.index('"$SERENA_AGENT_PYTHON" "$SERENA_AGENT_LAUNCHER" "$@"')


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


def test_render_zsh_shim_prompts_for_serena_after_preflight_before_cleanup():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    codex_body = text.split("\ncodex() {", 1)[1]
    claude_body = text.split("\nclaude() {", 1)[1].split("\ncodex() {", 1)[0]

    assert "_dotsync_agent_graphify_preflight_state" in codex_body
    assert codex_body.index("_dotsync_agent_graphify_preflight_state") < codex_body.index("_dotsync_agent_preflight")
    assert codex_body.index("_dotsync_agent_preflight") < codex_body.index("_dotsync_agent_ensure_serena")
    assert codex_body.index("_dotsync_agent_ensure_serena") < codex_body.index("_dotsync_agent_cleanup_codex")
    assert "_dotsync_agent_graphify_preflight_state" in claude_body
    assert claude_body.index("_dotsync_agent_graphify_preflight_state") < claude_body.index("_dotsync_agent_preflight")
    assert claude_body.index("_dotsync_agent_preflight") < claude_body.index("_dotsync_agent_ensure_serena")
    assert claude_body.index("_dotsync_agent_ensure_serena") < claude_body.index("_dotsync_agent_cleanup_claude")


def test_render_zsh_shim_marks_missing_serena_project_in_preflight():
    text = render_zsh_shim(
        launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
        python_executable=Path("/repo/.venv/bin/python3"),
        codex_binary=Path("/opt/homebrew/bin/codex"),
        claude_binary=Path("/opt/homebrew/bin/claude"),
    )

    assert "project config missing" in text
    assert "managed by scoped launcher" in text


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
def test_zsh_shim_marks_graphify_installed_in_preflight(tmp_path):
    shim_path = tmp_path / "shim.zsh"
    shim_path.write_text(
        render_zsh_shim(
            launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
            python_executable=Path("/usr/bin/python3"),
            codex_binary=Path("/opt/homebrew/bin/codex"),
            claude_binary=Path("/opt/homebrew/bin/claude"),
        )
    )
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    graphify = path_dir / "graphify"
    graphify.write_text("#!/bin/sh\nexit 0\n")
    graphify.chmod(0o755)

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"PATH={path_dir}:$PATH; "
                'graphify_info="$(_dotsync_agent_graphify_preflight_state)"; '
                'graphify_state="${graphify_info%%|*}"; '
                'graphify_phrase="${graphify_info#*|}"; '
                f"_dotsync_agent_preflight 081 codex {tmp_path} "
                "'sessions total=0 delete=0 keep=0' "
                "'memory reset files=0' "
                "codex active 'managed by scoped launcher' "
                '"$graphify_state" "$graphify_phrase" </dev/null'
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "graphify" in result.stdout
    assert "installed . run /graphify . when you want a project graph" in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_marks_graphify_missing_in_preflight(tmp_path):
    shim_path = tmp_path / "shim.zsh"
    shim_path.write_text(
        render_zsh_shim(
            launcher_path=Path("/repo/local_dev/serena_mcp_management/serena_agent_launcher.py"),
            python_executable=Path("/usr/bin/python3"),
            codex_binary=Path("/opt/homebrew/bin/codex"),
            claude_binary=Path("/opt/homebrew/bin/claude"),
        )
    )
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"PATH={empty_path}; "
                'graphify_info="$(_dotsync_agent_graphify_preflight_state)"; '
                'graphify_state="${graphify_info%%|*}"; '
                'graphify_phrase="${graphify_info#*|}"; '
                f"_dotsync_agent_preflight 081 codex {tmp_path} "
                "'sessions total=0 delete=0 keep=0' "
                "'memory reset files=0' "
                "codex active 'managed by scoped launcher' "
                '"$graphify_state" "$graphify_phrase" </dev/null'
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "graphify" in result.stdout
    assert "not installed . install graphify, then run /graphify ." in result.stdout


@pytest.mark.no_subprocess_block
def test_zsh_shim_declines_missing_project_config_and_runs_real_binary(tmp_path):
    shim_path, real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    path_dir = tmp_path / "path"
    path_dir.mkdir()

    result = subprocess.run(
        [
            "/bin/zsh",
            "-fc",
            f"source {shim_path}; _dotsync_agent_ensure_serena codex {tmp_path} </dev/null || codex --help",
        ],
        env={**os.environ, "HOME": str(tmp_path), "PATH": f"{path_dir}:/bin:/usr/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "launching codex without Serena project config" in result.stdout
    assert result.stdout.endswith(f"REAL {real_codex} --help\n")


@pytest.mark.no_subprocess_block
def test_zsh_shim_creates_missing_project_config_when_confirmed(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    serena = path_dir / "serena"
    serena.write_text(
        "#!/bin/sh\n"
        "printf 'SERENA %s\\n' \"$*\"\n"
        "mkdir -p \"$3/.serena\"\n"
        "printf 'name: test\\n' > \"$3/.serena/project.yml\"\n"
    )
    serena.chmod(0o755)

    result = subprocess.run(
        [
            "/bin/zsh",
            "-fc",
            f"source {shim_path}; printf 'y\\n' | _dotsync_agent_ensure_serena codex {tmp_path}",
        ],
        env={**os.environ, "HOME": str(tmp_path), "PATH": f"{path_dir}:/bin:/usr/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert f"SERENA project create {tmp_path}" in result.stdout
    assert (tmp_path / ".serena" / "project.yml").exists()


@pytest.mark.no_subprocess_block
def test_zsh_shim_accepts_serena_project_create_defaults_for_extra_language_prompts(tmp_path):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(tmp_path)
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    serena = path_dir / "serena"
    serena.write_text(
        "#!/bin/sh\n"
        "printf 'Enable ruby (1.39%% of source files)? [y/N] '\n"
        "IFS= read -r reply || exit 7\n"
        "[ -z \"$reply\" ] || exit 8\n"
        "mkdir -p \"$3/.serena\"\n"
        "printf 'languages:\\n- python\\n' > \"$3/.serena/project.yml\"\n"
    )
    serena.chmod(0o755)

    result = subprocess.run(
        [
            "/bin/zsh",
            "-fc",
            f"source {shim_path}; printf 'y\\n' | _dotsync_agent_ensure_serena codex {tmp_path}",
        ],
        env={**os.environ, "HOME": str(tmp_path), "PATH": f"{path_dir}:/bin:/usr/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Enable ruby" in result.stdout
    assert (tmp_path / ".serena" / "project.yml").read_text() == "languages:\n- python\n"


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
    assert "old" not in text
    assert "_dotsync_agent_ensure_serena" in text
    assert (tmp_path / ".zshrc.dotsync-serena.bak").read_text().startswith("before\n")


def test_zsh_shim_cli_installs_into_selected_rc_path(monkeypatch, tmp_path, capsys):
    rc_path = tmp_path / ".zshrc"
    rc_path.write_text("existing\n")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.shutil.which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_zsh_shim.sys.executable", "/opt/homebrew/bin/python3.12")

    assert main(["--install-zshrc", "--rc-path", str(rc_path)]) == 0

    output = capsys.readouterr().out
    assert f"installed Serena zsh shim into {rc_path}" in output
    assert "_dotsync_agent_ensure_serena" in rc_path.read_text()


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
