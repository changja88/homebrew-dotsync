from tools.install_serena_agent_wrappers import install_wrappers


def test_install_wrappers_writes_codex_and_claude_scripts(tmp_path):
    launcher = tmp_path / "repo" / "tools" / "serena_agent_launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# launcher\n")
    bin_dir = tmp_path / "bin"

    install_wrappers(
        bin_dir=bin_dir,
        launcher_path=launcher,
        real_binaries={"codex": "/opt/homebrew/bin/codex", "claude": "/opt/homebrew/bin/claude"},
        python_executable="/opt/homebrew/bin/python3.12",
    )

    codex = bin_dir / "codex"
    claude = bin_dir / "claude"
    assert codex.exists()
    assert claude.exists()
    assert "SERENA_AGENT_CLIENT=codex" in codex.read_text()
    assert "SERENA_AGENT_CLIENT=claude" in claude.read_text()
    assert "SERENA_REAL_CODEX=/opt/homebrew/bin/codex" in codex.read_text()
    assert "SERENA_REAL_CLAUDE=/opt/homebrew/bin/claude" in claude.read_text()
    assert 'exec "/opt/homebrew/bin/python3.12"' in codex.read_text()
    assert 'exec "/opt/homebrew/bin/python3.12"' in claude.read_text()
