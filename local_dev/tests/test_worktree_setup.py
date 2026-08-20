"""Behavioral coverage for managed future-worktree setup."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import worktree_setup
from local_dev.serena_mcp_management.worktree_setup import (
    END_MARKER,
    START_MARKER,
    WorktreeSetupError,
    install_worktree_setup_hook,
    worktree_setup_available,
    worktree_setup_installed,
)


ZERO_SHA = "0" * 40


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repository(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Dotsync Tests")
    _git(root, "config", "user.email", "dotsync@example.test")
    (root / "tracked.txt").write_text("tracked\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "initial")


def _write_opted_in_assets(root: Path) -> None:
    (root / ".env.local").write_text("SECRET=test-only\n")
    (root / ".env.local").chmod(0o640)

    memories = root / ".serena" / "memories"
    memories.mkdir(parents=True)
    (root / ".serena" / "project.yml").write_text("project_name: sample\n")
    (root / ".serena" / "project.local.yml").write_text("languages: [python]\n")
    (memories / "context.md").write_text("shared memory\n")

    graph = root / "graphify-out"
    (graph / "reflections").mkdir(parents=True)
    (graph / "cache").mkdir()
    (graph / "memory").mkdir()
    (graph / "2026-08-20").mkdir()
    (graph / "graph.json").write_text('{"graph": true}\n')
    (graph / "GRAPH_REPORT.md").write_text("# Report\n")
    (graph / ".graphify_python").write_text("/opt/homebrew/bin/python3.12\n")
    (graph / "reflections" / "LESSONS.md").write_text("# Lessons\n")
    (graph / ".graphify_root").write_text(str(root))
    (graph / "manifest.json").write_text("{}\n")
    (graph / "cost.json").write_text("{}\n")
    (graph / "cache" / "stat-index.json").write_text("{}\n")
    (graph / "memory" / "query.md").write_text("query\n")
    (graph / "2026-08-20" / "graph.json").write_text("{}\n")

    (root / ".codex").mkdir()
    (root / ".codex" / "hooks.json").write_text('{"hooks": {}}\n')
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text('{"hooks": {}}\n')


def _add_worktree(root: Path, target: Path, branch: str = "feature") -> None:
    _git(root, "worktree", "add", "-b", branch, str(target))


def test_initial_linked_worktree_receives_only_approved_local_assets(tmp_path):
    """Removing a copy/link action must leave the new worktree unusable or stale."""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_repository(primary)
    _write_opted_in_assets(primary)

    install_worktree_setup_hook(primary)
    _add_worktree(primary, linked)

    assert (linked / ".env.local").read_text() == "SECRET=test-only\n"
    assert stat.S_IMODE((linked / ".env.local").stat().st_mode) == 0o640
    assert (linked / ".serena" / "project.yml").read_text() == (
        "project_name: sample\n"
    )
    assert (linked / ".serena" / "project.local.yml").read_text() == (
        "languages: [python]\n"
    )
    memories = linked / ".serena" / "memories"
    assert memories.is_symlink()
    assert memories.resolve() == (primary / ".serena" / "memories").resolve()

    assert (linked / "graphify-out" / "graph.json").read_text() == (
        '{"graph": true}\n'
    )
    assert (linked / "graphify-out" / "GRAPH_REPORT.md").is_file()
    assert (linked / "graphify-out" / ".graphify_python").is_file()
    assert (linked / "graphify-out" / "reflections" / "LESSONS.md").is_file()
    assert (linked / ".codex" / "hooks.json").is_file()
    assert (linked / ".claude" / "settings.json").is_file()

    for excluded in (
        "graphify-out/.graphify_root",
        "graphify-out/manifest.json",
        "graphify-out/cost.json",
        "graphify-out/cache",
        "graphify-out/memory",
        "graphify-out/2026-08-20",
    ):
        assert not (linked / excluded).exists(), excluded


def test_serena_and_graphify_stay_independently_opted_in(tmp_path):
    """Checking only a parent directory must not opt an unmarked tool in."""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_repository(primary)
    (primary / ".env.local").write_text("ENV=present\n")
    (primary / ".serena" / "memories").mkdir(parents=True)
    (primary / ".serena" / "project.yml").write_text("project_name: only-serena\n")
    (primary / "graphify-out").mkdir()
    (primary / "graphify-out" / "GRAPH_REPORT.md").write_text("not opted in\n")
    (primary / ".codex").mkdir()
    (primary / ".codex" / "hooks.json").write_text("must not copy\n")

    install_worktree_setup_hook(primary)
    _add_worktree(primary, linked)

    assert (linked / ".env.local").is_file()
    assert (linked / ".serena" / "project.yml").is_file()
    assert not (linked / "graphify-out").exists()
    assert not (linked / ".codex").exists()


def test_graphify_only_does_not_create_absent_optional_asset_directories(tmp_path):
    """Preparing optional targets unconditionally must leave empty config dirs."""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_repository(primary)
    (primary / "graphify-out").mkdir()
    (primary / "graphify-out" / "graph.json").write_text("{}\n")

    install_worktree_setup_hook(primary)
    _add_worktree(primary, linked)

    assert (linked / "graphify-out" / "graph.json").is_file()
    assert not (linked / ".serena").exists()
    assert not (linked / ".codex").exists()
    assert not (linked / ".claude").exists()
    assert not (linked / "graphify-out" / "reflections").exists()


def test_hook_never_overwrites_existing_worktree_asset(tmp_path):
    """Removing the destination guard must overwrite a developer's local edit."""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_repository(primary)
    (primary / ".serena").mkdir()
    (primary / ".serena" / "project.yml").write_text("project_name: sample\n")
    (primary / ".env.local").write_text("SOURCE=value\n")
    install_worktree_setup_hook(primary)
    _add_worktree(primary, linked)
    (linked / ".env.local").write_text("LOCAL=keep-me\n")

    hook = install_worktree_setup_hook(primary)
    head = _git(linked, "rev-parse", "HEAD").stdout.strip()
    subprocess.run(
        [str(hook), ZERO_SHA, head, "1"],
        cwd=linked,
        check=True,
    )

    assert (linked / ".env.local").read_text() == "LOCAL=keep-me\n"


def test_hook_ignores_normal_branch_checkout(tmp_path):
    """Dropping the zero-SHA gate must copy secrets on ordinary checkout events."""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_repository(primary)
    (primary / ".serena").mkdir()
    (primary / ".serena" / "project.yml").write_text("project_name: sample\n")
    (primary / ".env.local").write_text("SOURCE=value\n")
    _add_worktree(primary, linked)
    hook = install_worktree_setup_hook(primary)
    head = _git(linked, "rev-parse", "HEAD").stdout.strip()

    subprocess.run([str(hook), head, head, "1"], cwd=linked, check=True)

    assert not (linked / ".env.local").exists()


def test_install_preserves_personal_hook_and_precedes_graphify_block(tmp_path):
    """Replacing the full hook must erase personal and Graphify behavior."""
    primary = tmp_path / "primary"
    _init_repository(primary)
    hook = primary / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        "#!/bin/sh\n"
        "echo personal-hook\n"
        "# graphify-checkout-hook-start\n"
        "echo graphify-hook\n"
        "# graphify-checkout-hook-end\n"
    )
    hook.chmod(0o744)

    installed = install_worktree_setup_hook(primary)
    text = installed.read_text()

    assert "echo personal-hook" in text
    assert "echo graphify-hook" in text
    assert text.count(START_MARKER) == 1
    assert text.count(END_MARKER) == 1
    assert text.index(START_MARKER) < text.index("# graphify-checkout-hook-start")
    assert stat.S_IMODE(installed.stat().st_mode) == 0o744


def test_install_honors_core_hooks_path(tmp_path):
    """Hardcoding `.git/hooks` must install a hook Git never invokes."""
    primary = tmp_path / "primary"
    _init_repository(primary)
    _git(primary, "config", "core.hooksPath", ".githooks")

    installed = install_worktree_setup_hook(primary)

    assert installed == primary / ".githooks" / "post-checkout"
    assert installed.is_file()
    assert not (primary / ".git" / "hooks" / "post-checkout").exists()


@pytest.mark.parametrize(
    "malformed",
    [
        f"#!/bin/sh\n{START_MARKER}\necho partial\n",
        (
            f"#!/bin/sh\n{START_MARKER}\necho one\n{END_MARKER}\n"
            f"{START_MARKER}\necho two\n{END_MARKER}\n"
        ),
        f"#!/bin/sh\n{END_MARKER}\n{START_MARKER}\n",
    ],
)
def test_install_rejects_malformed_or_duplicate_owned_markers(
    tmp_path,
    malformed,
):
    """Broad marker replacement must silently corrupt an ambiguous hook."""
    primary = tmp_path / "primary"
    _init_repository(primary)
    hook = primary / ".git" / "hooks" / "post-checkout"
    hook.write_text(malformed)

    with pytest.raises(WorktreeSetupError, match="managed marker"):
        install_worktree_setup_hook(primary)

    assert hook.read_text() == malformed


def test_install_rejects_symlink_hook_without_touching_target(tmp_path):
    """Following a hook symlink must allow mutation outside the Git hook path."""
    primary = tmp_path / "primary"
    _init_repository(primary)
    hook = primary / ".git" / "hooks" / "post-checkout"
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("do not change\n")
    hook.symlink_to(sentinel)

    with pytest.raises(WorktreeSetupError, match="symlink"):
        install_worktree_setup_hook(primary)

    assert hook.is_symlink()
    assert sentinel.read_text() == "do not change\n"


def test_availability_requires_primary_git_checkout_and_tool_marker(tmp_path):
    """Failing any eligibility gate must cause an unwanted project hook prompt."""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    plain = tmp_path / "plain"
    _init_repository(primary)
    plain.mkdir()

    assert worktree_setup_available(primary) is False
    (primary / ".serena").mkdir()
    (primary / ".serena" / "project.yml").write_text("project_name: sample\n")
    assert worktree_setup_available(primary) is True
    assert worktree_setup_available(plain) is False

    _add_worktree(primary, linked)
    assert worktree_setup_available(linked) is False

    assert worktree_setup_installed(primary) is False
    install_worktree_setup_hook(primary)
    assert worktree_setup_installed(primary) is True


@pytest.mark.parametrize(
    "probe_result",
    [ValueError("synthetic subprocess boundary failure"), type("Result", (), {"returncode": 0})()],
)
def test_availability_fails_closed_when_git_probe_cannot_return_a_path(
    monkeypatch,
    tmp_path,
    probe_result,
):
    """Letting an optional Git probe error escape must abort the agent launch."""
    (tmp_path / ".serena").mkdir()
    (tmp_path / ".serena" / "project.yml").write_text("project_name: sample\n")
    if isinstance(probe_result, BaseException):
        monkeypatch.setattr(
            worktree_setup.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(probe_result),
        )
    else:
        monkeypatch.setattr(
            worktree_setup.subprocess,
            "run",
            lambda *args, **kwargs: probe_result,
        )

    assert worktree_setup_available(tmp_path) is False
