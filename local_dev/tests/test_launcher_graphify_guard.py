"""launcher: 설치 후 재검증과 setup guard로 "매번 다시 묻기"를 구조적으로 막는다."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import graphify_setup_guard as guard_module
from local_dev.serena_mcp_management import serena_agent_launcher as launcher

# conftest의 autouse stub이 바꾸기 전에 잡아 둔 실제 구현.
REAL_POPULATE = launcher._populate_graphify_preflight_environ
REAL_COMPONENT_STATE = launcher._graphify_component_state
REAL_SETUP_SUPPRESSED = launcher._graphify_setup_suppressed

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture
def opted_in_project(monkeypatch, tmp_path):
    """graph.json이 있는 claude 프로젝트, 통합/hook 상태는 테스트가 정한다."""
    project = tmp_path / "project"
    (project / "graphify-out").mkdir(parents=True)
    (project / "graphify-out" / "graph.json").write_text("{}")
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("SERENA_AGENT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(project))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "built")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "installed")
    monkeypatch.setattr(launcher, "graphify_command", lambda: ["/fake/graphify"])
    monkeypatch.setattr(launcher, "_graphify_installed_version", lambda: "0.9.53")
    monkeypatch.setattr(launcher, "_run_graphify_version_check_v2", lambda **kw: "current")
    monkeypatch.setattr(launcher, "_run_node_runtime_check_v2", lambda *a, **kw: None)
    monkeypatch.setattr(launcher, "_is_linked_worktree", lambda root: False)
    # 이 파일의 테스트는 실제 재검증·가드 경로를 tmp runtime root에서 돌린다.
    monkeypatch.setattr(launcher, "_graphify_component_state", REAL_COMPONENT_STATE)
    monkeypatch.setattr(launcher, "_graphify_setup_suppressed", REAL_SETUP_SUPPRESSED)
    monkeypatch.setattr(
        launcher.graphify_probe, "integration_fingerprint", lambda root, client: "fp-int"
    )
    monkeypatch.setattr(launcher.graphify_probe, "hook_fingerprint", lambda root: "fp-hook")
    return project


def _run(answers, **overrides):
    out = io.StringIO()
    replies = iter(answers)
    kwargs = dict(
        stream=out,
        input_fn=lambda: next(replies),
        serena_state="managed",
        install_graphify_integration=lambda root, client: 0,
        install_graphify_hooks=lambda root: 0,
        is_git_repo=lambda root: True,
    )
    kwargs.update(overrides)
    rc = launcher._run_preflight_v2(**kwargs)
    return rc, _plain(out.getvalue())


# ------------------------------------------------- install then re-verify


def test_integration_install_confirmed_by_probe_keeps_success_row(
    opted_in_project, monkeypatch, tmp_path,
):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setattr(launcher.graphify_probe, "integration_status", lambda r, c: "installed")

    rc, text = _run(["y"])

    assert rc == 0
    assert "CLAUDE.md + .claude/settings.json registered" in text
    assert "can't confirm" not in text
    assert not (tmp_path / "runtime" / guard_module.GUARD_FILE_NAME).exists()


def test_integration_install_unconfirmed_by_probe_warns_and_records_guard(
    opted_in_project, monkeypatch, tmp_path,
):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setattr(launcher.graphify_probe, "integration_status", lambda r, c: "missing")
    installs: list[str] = []

    rc, text = _run(
        ["y"],
        install_graphify_integration=lambda root, client: installs.append(client) or 0,
    )

    assert rc == 0
    assert installs == ["claude"]
    assert "CLAUDE.md + .claude/settings.json registered" in text
    assert "dotsync probe can't confirm it (graphify 0.9.53)" in text
    assert "won't ask again" in text
    entries = json.loads((tmp_path / "runtime" / guard_module.GUARD_FILE_NAME).read_text())
    assert list(entries) == [f"{opted_in_project.resolve()}::integration::0.9.53"]
    assert entries[list(entries)[0]]["fingerprint"] == "fp-int"


def test_integration_install_failure_does_not_record_guard(
    opted_in_project, monkeypatch, tmp_path,
):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setattr(launcher.graphify_probe, "integration_status", lambda r, c: "missing")

    rc, text = _run(["y"], install_graphify_integration=lambda root, client: 3)

    assert "integration install failed (exit 3)" in text
    assert not (tmp_path / "runtime" / guard_module.GUARD_FILE_NAME).exists()


def test_unconfirmed_integration_still_lets_hook_setup_proceed(
    opted_in_project, monkeypatch,
):
    """graphify 기준으로는 설치됐으므로 hook 단계까지 이어져야 한다."""
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "missing")
    monkeypatch.setattr(launcher.graphify_probe, "integration_status", lambda r, c: "missing")
    hook_installs: list[Path] = []

    rc, text = _run(
        ["y", ""],  # integration: yes, hooks: default (yes)
        install_graphify_hooks=lambda root: hook_installs.append(root) or 0,
    )

    assert hook_installs == [opted_in_project.resolve()]
    assert "post-commit + post-checkout hooks installed" in text


def test_hook_install_unconfirmed_by_probe_warns_and_records_guard(
    opted_in_project, monkeypatch, tmp_path,
):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "missing")
    monkeypatch.setattr(launcher.graphify_probe, "hook_status", lambda r: "missing")

    rc, text = _run([""])

    assert "post-commit + post-checkout hooks installed" in text
    assert "dotsync probe can't confirm it (graphify 0.9.53)" in text
    entries = json.loads((tmp_path / "runtime" / guard_module.GUARD_FILE_NAME).read_text())
    assert list(entries) == [f"{opted_in_project.resolve()}::hook::0.9.53"]


# ------------------------------------------------------ guard suppresses


def test_guard_suppresses_integration_prompt_for_same_version_and_files(
    opted_in_project, monkeypatch,
):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "missing")
    guard_module.record(opted_in_project, "integration", "0.9.53", "fp-int")
    hook_installs: list[Path] = []

    rc, text = _run(
        [""],  # only the hook prompt may consume an answer
        install_graphify_integration=lambda root, client: pytest.fail("must not re-run"),
        install_graphify_hooks=lambda root: hook_installs.append(root) or 0,
    )

    assert rc == 0
    assert "not wired into this project" not in text
    assert "not asking again" in text
    assert hook_installs == [opted_in_project.resolve()]


def test_guard_asks_again_when_graphify_version_changes(opted_in_project, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    guard_module.record(opted_in_project, "integration", "0.9.52", "fp-int")

    rc, text = _run([""])  # bare Enter declines

    assert "not wired into this project" in text
    assert "[y/N]" in text


def test_guard_asks_again_when_integration_files_change(opted_in_project, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    guard_module.record(opted_in_project, "integration", "0.9.53", "fp-old")

    rc, text = _run([""])

    assert "not wired into this project" in text


def test_guard_suppresses_hook_prompt(opted_in_project, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "missing")
    guard_module.record(opted_in_project, "hook", "0.9.53", "fp-hook")

    rc, text = _run(
        [],
        install_graphify_hooks=lambda root: pytest.fail("must not re-run"),
    )

    assert rc == 0
    assert "Install graphify hooks" not in text
    assert "not asking again" in text


# --------------------------------------------------- refresh after upgrade


def test_refresh_after_upgrade_reverifies_and_records_when_unconfirmed(
    opted_in_project, monkeypatch, tmp_path,
):
    monkeypatch.setattr(launcher, "_run_graphify_version_check_v2", lambda **kw: "upgraded")
    monkeypatch.setattr(launcher.graphify_probe, "integration_status", lambda r, c: "missing")
    monkeypatch.setattr(launcher.graphify_probe, "hook_status", lambda r: "installed")

    rc, text = _run(
        [],
        install_graphify_global=lambda client: 0,
    )

    assert rc == 0
    assert "project integration refreshed" in text
    assert "hooks refreshed" in text
    assert text.count("can't confirm") == 1
    entries = json.loads((tmp_path / "runtime" / guard_module.GUARD_FILE_NAME).read_text())
    assert list(entries) == [f"{opted_in_project.resolve()}::integration::0.9.53"]


# ------------------------------------------------------- env population


def test_main_populates_graphify_environ_before_overview_when_interactive(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setattr(launcher, "find_project_root", lambda cwd: tmp_path)
    monkeypatch.setattr(launcher, "find_real_binary", lambda client: "/usr/bin/true")
    calls: list = []
    monkeypatch.setattr(
        launcher,
        "_populate_graphify_preflight_environ",
        lambda root, client: calls.append(("populate", root, client)),
    )
    monkeypatch.setattr(
        launcher, "_render_preflight_overview_v2", lambda: calls.append("overview")
    )
    monkeypatch.setattr(launcher, "serena_opted_in", lambda root: False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2", lambda **kwargs: "skipped")
    monkeypatch.setattr(launcher, "_run_preflight_v2", lambda **kwargs: 0)
    monkeypatch.setattr(launcher, "_run_worktree_setup_v2", lambda root: None, raising=False)
    monkeypatch.setattr(launcher, "_run_session_choice_v2", lambda: "keep")
    monkeypatch.setattr(launcher, "_launch_bare_child", lambda *args, **kwargs: 0)

    assert launcher._main_v2([]) == 0
    assert calls == [("populate", tmp_path, "claude"), "overview"]


def test_main_skips_graphify_probe_when_not_interactive(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "0")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setattr(launcher, "find_project_root", lambda cwd: tmp_path)
    monkeypatch.setattr(launcher, "find_real_binary", lambda client: "/usr/bin/true")
    monkeypatch.setattr(
        launcher,
        "_populate_graphify_preflight_environ",
        lambda root, client: pytest.fail("non-interactive launch must not probe"),
    )
    monkeypatch.setattr(launcher, "serena_opted_in", lambda root: False)
    monkeypatch.setattr(launcher, "_launch_bare_child", lambda *args, **kwargs: 7)

    assert launcher._main_v2([]) == 7


@pytest.mark.no_subprocess_block
def test_populate_fills_missing_keys_from_real_probe(monkeypatch, tmp_path):
    for key in (
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS",
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS",
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS",
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "built")
    project = tmp_path / "plain"
    project.mkdir()

    REAL_POPULATE(project, "claude")

    import os
    assert os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS"] == "built"
    assert os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS"] == "missing"
    assert os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS"] == "missing"
    assert os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS"] in {"installed", "missing"}
