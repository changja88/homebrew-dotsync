"""실제 설치된 graphify가 쓰는 파일을 dotsync probe가 알아보는지 확인한다.

Graphify의 hook 문구는 릴리스 노트 없이 바뀐다. 이 테스트가 이 머신의 graphify
버전에 대해 probe 계약을 검증하므로, 업그레이드 뒤 `make -C local_dev test`가
사용자보다 먼저 드리프트를 잡는다. graphify가 PATH에 없으면 건너뛴다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import graphify_probe as probe

GRAPHIFY = shutil.which("graphify")

pytestmark = [
    pytest.mark.no_subprocess_block,
    pytest.mark.skipif(GRAPHIFY is None, reason="graphify CLI not on PATH"),
]


@pytest.fixture
def project(tmp_path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    (root / "CLAUDE.md").write_text("# project\n")
    (root / "AGENTS.md").write_text("# project\n")
    return root, home


def _graphify(root: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    # HOME을 격리해 사용자 스킬 디렉터리 등 홈 아래 부수 효과를 tmp에 가둔다.
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [GRAPHIFY, *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_claude_install_and_uninstall_are_recognised(project):
    root, home = project
    assert probe.integration_status(root, "claude") == "missing"

    result = _graphify(root, home, "claude", "install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.integration_status(root, "claude") == "installed"

    result = _graphify(root, home, "claude", "uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.integration_status(root, "claude") == "missing"


def test_codex_install_is_recognised(project):
    root, home = project

    result = _graphify(root, home, "codex", "install")
    assert result.returncode == 0, result.stdout + result.stderr

    assert probe.integration_status(root, "codex") == "installed"


def test_hook_install_and_uninstall_are_recognised(project):
    root, home = project
    assert probe.hook_status(root) == "missing"

    result = _graphify(root, home, "hook", "install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.hook_status(root) == "installed"

    result = _graphify(root, home, "hook", "uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.hook_status(root) == "missing"
