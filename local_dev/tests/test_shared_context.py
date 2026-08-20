"""Behavioral contract tests for the bundled shared Serena context."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp import paths


EXPECTED_EXCLUDED_TOOLS = {
    "create_text_file",
    "read_file",
    "execute_shell_command",
    "replace_content",
    "find_file",
    "list_dir",
    "search_for_pattern",
}
EXPECTED_EXCLUDED_TOOL_LIST = [
    "create_text_file",
    "read_file",
    "execute_shell_command",
    "replace_content",
    "find_file",
    "list_dir",
    "search_for_pattern",
]
EXPECTED_PROMPT = (
    "You are connected to a single project through a CLI coding agent that already\n"
    "provides basic file operations, text search, line-based edits, and shell commands.\n"
    "Use Serena for symbolic code understanding, reference analysis, and symbol-level\n"
    "edits when those capabilities materially improve correctness or efficiency.\n"
)


class SharedContextTests(unittest.TestCase):
    def test_shared_context_resolves_to_the_openai_compatible_contract(self) -> None:
        """The shared context must expose the client-neutral tool contract."""

        context_path = paths.shared_context_path()
        context = _parse_context(context_path)

        self.assertTrue(context_path.is_absolute())
        self.assertTrue(context_path.is_file())
        self.assertEqual(context_path.stem, "oaicompat-agent")
        self.assertEqual(paths.SHARED_CONTEXT_PROFILE, "dotsync-shared-cli-v1")
        self.assertEqual(context["name"], "oaicompat-agent")
        self.assertEqual(
            context["description"],
            "Shared single-worktree context for Codex and Claude CLI agents",
        )
        self.assertEqual(context["prompt"], EXPECTED_PROMPT)
        self.assertTrue(context["single_project"])
        self.assertFalse(context["structured_tool_output"])
        self.assertEqual(set(context["excluded_tools"]), EXPECTED_EXCLUDED_TOOLS)
        self.assertEqual(context["excluded_tools"], EXPECTED_EXCLUDED_TOOL_LIST)
        self.assertEqual(context["included_optional_tools"], [])
        self.assertEqual(context["fixed_tools"], [])
        self.assertEqual(context["tool_description_overrides"], {})

        context_text = context_path.read_text()
        self.assertIn("name: oaicompat-agent", context_text)
        self.assertIn("single_project: true", context_text)
        self.assertIn("structured_tool_output: false", context_text)

    @unittest.skipUnless(shutil.which("serena"), "Serena CLI is not installed")
    def test_installed_serena_accepts_the_bundled_single_project_context(self) -> None:
        """An installed Serena CLI must interpret the shared context cleanly."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            project_root = temporary_root / "project"
            (project_root / ".serena").mkdir(parents=True)
            (project_root / ".serena" / "project.yml").write_text(
                "project_name: context-validation\nlanguage_servers: []\n"
            )
            environment = os.environ | {"SERENA_HOME": str(temporary_root / "serena-home")}
            result = subprocess.run(
                [
                    "serena",
                    "print-system-prompt",
                    str(project_root),
                    "--context",
                    str(paths.shared_context_path()),
                    "--only-instructions",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            full_prompt = subprocess.run(
                [
                    "serena",
                    "print-system-prompt",
                    str(project_root),
                    "--context",
                    str(paths.shared_context_path()),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<context>", result.stdout)
        self.assertIn("single project through a CLI coding agent", result.stdout)
        self.assertNotIn("unknown field", result.stderr.lower())
        self.assertNotIn("activate_project", result.stdout)
        self.assertEqual(full_prompt.returncode, 0, full_prompt.stderr)
        self.assertNotIn("activate_project", full_prompt.stdout)
        for excluded_tool in EXPECTED_EXCLUDED_TOOL_LIST:
            self.assertNotIn(excluded_tool, full_prompt.stdout)


def _parse_context(path: Path) -> dict[str, object]:
    """Parse the small, launcher-owned context format without PyYAML."""

    context: dict[str, object] = {}
    lines = path.read_text().splitlines()
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if not line or line.startswith(" "):
            line_index += 1
            continue

        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"invalid context entry: {line}")
        value = value.strip()
        if value == "|":
            literal_lines: list[str] = []
            line_index += 1
            while line_index < len(lines) and lines[line_index].startswith("  "):
                literal_lines.append(lines[line_index][2:])
                line_index += 1
            context[key] = "\n".join(literal_lines) + "\n"
            continue
        if not value:
            values: list[str] = []
            line_index += 1
            while line_index < len(lines) and lines[line_index].startswith("  - "):
                values.append(lines[line_index][4:])
                line_index += 1
            context[key] = values
            continue
        context[key] = _parse_scalar(value)
        line_index += 1
    return context


def _parse_scalar(value: str) -> object:
    """Parse scalar forms used by the owned context contract."""

    if value == "true":
        return True
    if value == "false":
        return False
    if value == "[]":
        return []
    if value == "{}":
        return {}
    return value


if __name__ == "__main__":
    unittest.main()
