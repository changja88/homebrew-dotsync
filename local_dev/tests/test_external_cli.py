from pathlib import Path

from local_dev.serena_mcp_management import external_cli


def _which_map(mapping):
    return lambda name: mapping.get(name)


def _make_tool(home: Path, name: str) -> Path:
    tool = home / ".local" / "bin" / name
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    return tool


def test_graphify_command_prefers_path_hit(tmp_path):
    cmd = external_cli.graphify_command(
        which=_which_map({"graphify": "/somewhere/graphify"}), home=tmp_path
    )
    assert cmd == ["/somewhere/graphify"]


def test_graphify_command_falls_back_to_uv_tool_bin(tmp_path):
    tool = _make_tool(tmp_path, "graphify")
    cmd = external_cli.graphify_command(which=_which_map({}), home=tmp_path)
    assert cmd == [str(tool)]


def test_graphify_command_ignores_non_executable_tool_bin(tmp_path):
    tool = tmp_path / ".local" / "bin" / "graphify"
    tool.parent.mkdir(parents=True)
    tool.write_text("")
    tool.chmod(0o644)
    assert external_cli.graphify_command(which=_which_map({}), home=tmp_path) is None


def test_graphify_command_has_no_uvx_fallback(tmp_path):
    # graphify writes its own absolute path into project hooks
    # (.codex/hooks.json); an ephemeral uvx cache path would rot there.
    cmd = external_cli.graphify_command(
        which=_which_map({"uvx": "/opt/homebrew/bin/uvx"}), home=tmp_path
    )
    assert cmd is None


def test_serena_oneshot_prefers_direct_binary_over_uvx(tmp_path):
    tool = _make_tool(tmp_path, "serena")
    cmd = external_cli.serena_oneshot_command(
        which=_which_map({"uvx": "/opt/homebrew/bin/uvx"}), home=tmp_path
    )
    assert cmd == [str(tool)]


def test_serena_oneshot_falls_back_to_uvx(tmp_path):
    cmd = external_cli.serena_oneshot_command(
        which=_which_map({"uvx": "/opt/homebrew/bin/uvx"}), home=tmp_path
    )
    assert cmd == [
        "/opt/homebrew/bin/uvx",
        "--from",
        external_cli.SERENA_UVX_SPEC,
        "serena",
    ]


def test_serena_oneshot_returns_none_without_any_runner(tmp_path):
    assert external_cli.serena_oneshot_command(which=_which_map({}), home=tmp_path) is None


def test_serena_server_command_uses_direct_binary(tmp_path):
    tool = _make_tool(tmp_path, "serena")
    cmd = external_cli.serena_server_command(which=_which_map({}), home=tmp_path)
    assert cmd == [str(tool)]


def test_serena_server_command_never_uses_uvx(tmp_path):
    # uvx keeps the real server as a child process, so the registry would
    # record the wrapper pid and same-scope orphan cleanup would terminate
    # its own server's child.
    cmd = external_cli.serena_server_command(
        which=_which_map({"uvx": "/opt/homebrew/bin/uvx"}), home=tmp_path
    )
    assert cmd is None


def test_serena_install_command_uses_uv_tool():
    cmd = external_cli.serena_install_command(
        which=_which_map({"uv": "/opt/homebrew/bin/uv"})
    )
    assert cmd == [
        "/opt/homebrew/bin/uv",
        "tool",
        "install",
        "--from",
        external_cli.SERENA_UVX_SPEC,
        "serena-agent",
    ]


def test_graphify_install_command_uses_uv_tool():
    cmd = external_cli.graphify_install_command(
        which=_which_map({"uv": "/opt/homebrew/bin/uv"})
    )
    assert cmd == ["/opt/homebrew/bin/uv", "tool", "install", "graphifyy"]


def test_install_commands_require_uv():
    assert external_cli.serena_install_command(which=_which_map({})) is None
    assert external_cli.graphify_install_command(which=_which_map({})) is None
