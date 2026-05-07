import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.serena_zsh_shim import render_zsh_shim


def _setup_claude_tree(root: Path) -> Path:
    proj_dir = root / "claude" / "-x"
    proj_dir.mkdir(parents=True)
    old = proj_dir / "abc.jsonl"
    old.write_text("x")
    old_uuid_dir = proj_dir / "abc"
    old_uuid_dir.mkdir()
    (old_uuid_dir / "child.txt").write_text("x")
    fresh = proj_dir / "fresh.jsonl"
    fresh.write_text("x")
    mem = proj_dir / "memory"
    mem.mkdir()
    (mem / "m1.txt").write_text("x")
    old_time = time.time() - 4 * 86400
    os.utime(old, (old_time, old_time))
    os.utime(old_uuid_dir, (old_time, old_time))
    return proj_dir


def test_v2_cleanup_claude_matches_v1_zsh_function(tmp_path):
    v1_dir = _setup_claude_tree(tmp_path / "v1")
    v2_dir = _setup_claude_tree(tmp_path / "v2")

    shim_text = render_zsh_shim(
        launcher_path=Path("/tmp/launcher.py"),
        python_executable=Path("/usr/bin/python3"),
        codex_binary=Path("/usr/bin/true"),
        claude_binary=Path("/usr/bin/true"),
    )
    script = (
        shim_text
        + f'\n_dotsync_agent_cleanup_claude "{v1_dir}" >/dev/null\n'
    )
    subprocess.run(["zsh", "-c", script], check=True)

    launcher._run_cleanup_claude(v2_dir)

    def listing(path: Path) -> set[str]:
        return {str(p.relative_to(path)) for p in path.rglob("*")}

    assert listing(v1_dir) == listing(v2_dir)


def test_v2_cleanup_codex_matches_v1_zsh_function_when_jq_present(tmp_path):
    if shutil.which("jq") is None:
        pytest.skip("jq required for parity check")
    cwd = str(tmp_path / "work")
    Path(cwd).mkdir()

    def setup(root: Path) -> Path:
        sess = root / "sessions"
        sess.mkdir(parents=True)
        old = sess / "old.jsonl"
        old.write_text(f'{{"type":"session_meta","payload":{{"cwd":"{cwd}"}}}}\n')
        os.utime(old, (time.time() - 4 * 86400, time.time() - 4 * 86400))
        fresh = sess / "fresh.jsonl"
        fresh.write_text(f'{{"type":"session_meta","payload":{{"cwd":"{cwd}"}}}}\n')
        mem = root / "memories"
        mem.mkdir()
        (mem / "m1.txt").write_text("x")
        return root

    v1_home = setup(tmp_path / "v1")
    v2_home = setup(tmp_path / "v2")

    shim_text = render_zsh_shim(
        launcher_path=Path("/tmp/launcher.py"),
        python_executable=Path("/usr/bin/python3"),
        codex_binary=Path("/usr/bin/true"),
        claude_binary=Path("/usr/bin/true"),
    )
    script = (
        shim_text
        + f'\n_dotsync_agent_cleanup_codex "{v1_home}" "{cwd}" >/dev/null\n'
    )
    subprocess.run(["zsh", "-c", script], check=True)

    launcher._run_cleanup_codex(v2_home, cwd)

    def listing(path: Path) -> set[str]:
        return {str(p.relative_to(path)) for p in path.rglob("*")}

    assert listing(v1_home) == listing(v2_home)
