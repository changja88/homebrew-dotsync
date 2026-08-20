"""Behavioral tests for Serena project-root discovery."""
from __future__ import annotations

import tempfile
import unittest
from hashlib import sha256
from os import environ
from stat import S_IMODE
from pathlib import Path
from unittest.mock import patch

from local_dev.serena_mcp_management.serena_mcp import paths


class ProjectRootTests(unittest.TestCase):
    def test_nested_worktree_git_file_beats_ancestor_serena_marker(self) -> None:
        """A nested worktree must not inherit its parent's Serena opt-in."""

        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "parent"
            nested = parent / "worktrees" / "feature"
            child = nested / "src"
            (parent / ".serena").mkdir(parents=True)
            (parent / ".serena" / "project.yml").write_text("project_name: parent\n")
            nested.mkdir(parents=True)
            (nested / ".git").write_text("gitdir: /tmp/fake\n")
            child.mkdir()

            self.assertEqual(paths.find_project_root(child), nested.resolve())

    def test_git_directory_is_a_project_boundary(self) -> None:
        """A conventional .git directory is a nearest project boundary."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            child = root / "src"
            (root / ".git").mkdir(parents=True)
            child.mkdir()

            self.assertEqual(paths.find_project_root(child), root.resolve())


class RuntimePathTests(unittest.TestCase):
    def test_scope_state_uses_private_hashed_runtime_root(self) -> None:
        """A repository path or readable scope key must not select mutable state."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            project_root = temporary_root / "project"
            runtime_root = temporary_root / "runtime"
            project_root.mkdir()
            scope = paths.Scope(project_root)
            expected_hash = sha256(scope.key.encode("utf-8")).hexdigest()

            with patch.dict(
                environ,
                {"SERENA_AGENT_RUNTIME_ROOT": str(runtime_root)},
                clear=False,
            ):
                state_dir = paths.state_dir_for(scope)
                paths.ensure_private_runtime_directory(state_dir)

            self.assertEqual(
                state_dir,
                runtime_root / "dotsync-shared-cli-v1" / expected_hash,
            )
            self.assertFalse(state_dir.is_relative_to(project_root))
            self.assertEqual(S_IMODE(runtime_root.stat().st_mode), 0o700)
            self.assertEqual(S_IMODE(state_dir.parent.stat().st_mode), 0o700)
            self.assertEqual(S_IMODE(state_dir.stat().st_mode), 0o700)

    def test_runtime_override_rejects_relative_symlink_and_public_directories(self) -> None:
        """An unsafe override must fail closed before any runtime state is opened."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            project_root = temporary_root / "project"
            project_root.mkdir()
            public_root = temporary_root / "public-runtime"
            public_root.mkdir(mode=0o755)
            actual_root = temporary_root / "actual-runtime"
            actual_root.mkdir(mode=0o700)
            symlink_root = temporary_root / "symlink-runtime"
            symlink_root.symlink_to(actual_root, target_is_directory=True)

            for unsafe in ("relative/runtime", str(public_root), str(symlink_root)):
                with self.subTest(unsafe=unsafe), patch.dict(
                    environ,
                    {"SERENA_AGENT_RUNTIME_ROOT": unsafe},
                    clear=False,
                ):
                    with self.assertRaises((OSError, ValueError)):
                        paths.ensure_private_runtime_directory(
                            paths.state_dir_for(paths.Scope(project_root))
                        )

    def test_runtime_override_cannot_place_mutable_state_inside_project(self) -> None:
        """Even an owner-private override cannot return state to repository control."""

        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw) / "project"
            project_root.mkdir()
            unsafe_root = project_root / ".private-runtime"
            unsafe_root.mkdir(mode=0o700)

            with patch.dict(
                environ,
                {"SERENA_AGENT_RUNTIME_ROOT": str(unsafe_root)},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "outside project root"):
                    paths.state_dir_for(paths.Scope(project_root))

    def test_marker_at_worktree_root_is_opted_in(self) -> None:
        """Only a Serena marker on the selected project root opts it in."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".git").mkdir()
            (root / ".serena").mkdir()
            (root / ".serena" / "project.yml").write_text("project_name: test\n")

            self.assertTrue(paths.serena_opted_in(root))

    def test_ancestor_marker_does_not_opt_in_nested_worktree(self) -> None:
        """An ancestor Serena marker cannot opt in a nested worktree."""

        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "parent"
            nested = parent / "feature"
            (parent / ".serena").mkdir(parents=True)
            (parent / ".serena" / "project.yml").write_text("project_name: parent\n")
            nested.mkdir()
            (nested / ".git").write_text("gitdir: /tmp/fake\n")

            self.assertFalse(paths.serena_opted_in(paths.find_project_root(nested)))

    def test_general_marker_is_used_after_boundary_search(self) -> None:
        """A normal project marker remains a fallback when no boundary exists."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            child = root / "src"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
            child.mkdir()

            self.assertEqual(paths.find_project_root(child), root.resolve())


if __name__ == "__main__":
    unittest.main()
