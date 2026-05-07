import io
import os
from unittest import mock

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher


def test_main_dispatches_to_v2_when_env_set(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_TUI", "v2")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    called = {}

    def fake_v2(args):
        called["v2"] = args
        return 0

    monkeypatch.setattr(launcher, "_main_v2", fake_v2, raising=False)
    monkeypatch.setattr(launcher, "_main_v1", lambda args: pytest.fail("v1 should not run"),
                        raising=False)
    rc = launcher.main(["--help"])
    assert rc == 0
    assert called["v2"] == ["--help"]


def test_main_dispatches_to_v1_when_env_unset(monkeypatch):
    monkeypatch.delenv("SERENA_AGENT_TUI", raising=False)
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    called = {}

    def fake_v1(args):
        called["v1"] = args
        return 0

    monkeypatch.setattr(launcher, "_main_v1", fake_v1, raising=False)
    monkeypatch.setattr(launcher, "_main_v2",
                        lambda args: pytest.fail("v2 should not run"), raising=False)
    rc = launcher.main([])
    assert rc == 0
    assert called["v1"] == []


def test_v2_preflight_renders_box_with_cleanup_and_serena(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_TUI", "v2")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 103 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

    out = io.StringIO()
    answers = iter(["n"])  # Abort

    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = out.getvalue()
    assert "0 to delete . 103 to keep" in text
    assert "0 files to reset" in text
    assert "preflight" in text
    assert "codex" in text
    assert rc == 130  # abort -> non-zero


def test_v2_preflight_returns_zero_on_run_confirm(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_TUI", "v2")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "missing")

    out = io.StringIO()
    answers = iter(["y"])
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    assert rc == 0
    assert "not installed" in out.getvalue()  # graphify warn surfaced


def test_v2_preflight_marks_serena_warn_when_missing(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_TUI", "v2")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

    out = io.StringIO()
    answers = iter(["y"])
    launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    assert "project config missing" in out.getvalue()
