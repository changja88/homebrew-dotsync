"""Behavioral parity tests for the generated and managed zsh shims."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_dev.serena_mcp_management.serena_zsh_shim import (
    render_zsh_shim,
)


@unittest.skipUnless(shutil.which("zsh"), "zsh is not installed")
class ZshShimTests(unittest.TestCase):
    def test_rendered_block_chooses_nested_worktree_boundary(self) -> None:
        """The generated shim must prefer a nested .git file over an ancestor marker."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            parent = temporary_root / "parent"
            nested = parent / "worktrees" / "feature"
            child = nested / "src"
            (parent / ".serena").mkdir(parents=True)
            (parent / ".serena" / "project.yml").write_text("project_name: parent\n")
            nested.mkdir(parents=True)
            (nested / ".git").write_text("gitdir: /tmp/fake\n")
            child.mkdir()

            rendered_block = render_zsh_shim(
                launcher_path=Path("/tmp/serena-agent-launcher.py"),
                python_executable=Path("/usr/bin/python3"),
                codex_binary=Path("/usr/bin/codex"),
                claude_binary=Path("/usr/bin/claude"),
            )
            rendered_result = _run_root_function(rendered_block, child)

            self.assertEqual(rendered_result, str(nested))


def _run_root_function(shim: str, child: Path) -> str:
    """Source one shim in a pristine zsh and return its discovered root."""

    with tempfile.TemporaryDirectory() as raw:
        rc_path = Path(raw) / "shim.zsh"
        rc_path.write_text(shim)
        result = subprocess.run(
            [
                "zsh",
                "-df",
                "-c",
                'source "$1"; cd "$2"; _dotsync_agent_project_root "$PWD"',
                "zsh",
                str(rc_path),
                str(child),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
