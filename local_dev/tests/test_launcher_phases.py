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
