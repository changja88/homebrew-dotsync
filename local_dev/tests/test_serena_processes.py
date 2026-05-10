import shlex
from types import SimpleNamespace

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.processes import (
    list_serena_mcp_processes,
    parse_serena_mcp_process,
    process_matches_scope,
)


def test_parse_serena_mcp_process_accepts_space_separated_options(tmp_path):
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {tmp_path} --context codex --port 12345"
    )

    proc = parse_serena_mcp_process(111, command)

    assert proc is not None
    assert proc.pid == 111
    assert proc.project_root == tmp_path.resolve()
    assert proc.context == "codex"


def test_parse_serena_mcp_process_accepts_equals_options(tmp_path):
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project={tmp_path} --context=claude-code --port 12345"
    )

    proc = parse_serena_mcp_process(222, command)

    assert proc is not None
    assert proc.pid == 222
    assert proc.project_root == tmp_path.resolve()
    assert proc.context == "claude-code"


def test_parse_serena_mcp_process_accepts_quoted_project_with_spaces(tmp_path):
    project = tmp_path / "repo with spaces"
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {shlex.quote(str(project))} --context codex --port 12345"
    )

    proc = parse_serena_mcp_process(333, command)

    assert proc is not None
    assert proc.project_root == project.resolve()


def test_parse_serena_mcp_process_accepts_unquoted_project_with_spaces(tmp_path):
    project = tmp_path / "repo with spaces"
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {project} --context codex --port 12345"
    )

    proc = parse_serena_mcp_process(334, command)

    assert proc is not None
    assert proc.project_root == project.resolve()


def test_parse_serena_mcp_process_accepts_equals_project_with_unquoted_spaces(tmp_path):
    project = tmp_path / "repo with spaces"
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project={project} --context codex --port 12345"
    )

    proc = parse_serena_mcp_process(335, command)

    assert proc is not None
    assert proc.project_root == project.resolve()


def test_parse_serena_mcp_process_fails_closed_without_context(tmp_path):
    command = f"/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server --project {tmp_path}"

    assert parse_serena_mcp_process(444, command) is None


def test_parse_serena_mcp_process_fails_closed_on_bad_quoting():
    command = "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server --project 'unterminated"

    assert parse_serena_mcp_process(555, command) is None


def test_process_matches_scope_uses_canonical_project_and_context(tmp_path):
    scope = Scope(tmp_path / "repo", "claude")
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {scope.project_root} --context claude-code"
    )
    proc = parse_serena_mcp_process(666, command)

    assert proc is not None
    assert process_matches_scope(proc, scope) is True
    assert process_matches_scope(proc, Scope(scope.project_root, "codex")) is False


def test_list_serena_mcp_processes_ignores_unparseable_rows(monkeypatch, tmp_path):
    output = (
        "111 /usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {tmp_path} --context codex\n"
        "222 /usr/bin/python unrelated\n"
        "bad row\n"
    )

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.processes.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )

    processes = list_serena_mcp_processes()

    assert [proc.pid for proc in processes] == [111]


def test_list_serena_mcp_processes_attaches_process_identity(monkeypatch, tmp_path):
    output = (
        "111 /usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {tmp_path} --context codex\n"
    )

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.processes.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.processes.process_identity",
        lambda pid: "Fri May  8 10:00:00 2026 serena start-mcp-server",
        raising=False,
    )

    processes = list_serena_mcp_processes()

    assert processes[0].identity == "Fri May  8 10:00:00 2026 serena start-mcp-server"


def test_list_serena_mcp_processes_returns_empty_when_ps_cannot_run(monkeypatch):
    def fake_run(*args, **kwargs):
        raise PermissionError("ps blocked")

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.processes.subprocess.run",
        fake_run,
    )

    assert list_serena_mcp_processes() == []
