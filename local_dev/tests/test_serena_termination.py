import signal

from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid


def test_terminate_pid_sends_sigterm_then_sigkill_when_process_survives(monkeypatch):
    calls = []
    alive_checks = iter([True, True, False])
    sleeps = []

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.os.killpg",
        lambda pid, sig: calls.append(("killpg", pid, sig)),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.pid_is_alive",
        lambda pid: next(alive_checks),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.process_identity",
        lambda pid: "owned identity",
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.time.time",
        iter([0.0, 0.1, 0.2, 10.0]).__next__,
    )

    terminate_pid(123, timeout=0.15, expected_identity="owned identity")

    assert calls == [
        ("killpg", 123, signal.SIGTERM),
        ("killpg", 123, signal.SIGKILL),
    ]
    assert sleeps == [0.1]


def test_terminate_pid_falls_back_to_individual_pid_on_permission_error(monkeypatch):
    calls = []

    def fake_killpg(pid, sig):
        calls.append(("killpg", pid, sig))
        raise PermissionError("no process group permission")

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.termination.os.killpg", fake_killpg)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.os.kill",
        lambda pid, sig: calls.append(("kill", pid, sig)),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.termination.pid_is_alive", lambda pid: False)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.process_identity",
        lambda pid: "owned identity",
    )

    terminate_pid(123, expected_identity="owned identity")

    assert calls == [
        ("killpg", 123, signal.SIGTERM),
        ("kill", 123, signal.SIGTERM),
    ]


def test_terminate_pid_falls_back_to_individual_pid_when_process_group_missing(monkeypatch):
    calls = []
    alive_checks = iter([True, False])

    def fake_killpg(pid, sig):
        calls.append(("killpg", pid, sig))
        raise ProcessLookupError("missing process group")

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.termination.os.killpg", fake_killpg)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.os.kill",
        lambda pid, sig: calls.append(("kill", pid, sig)),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.pid_is_alive",
        lambda pid: next(alive_checks),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.process_identity",
        lambda pid: "owned identity",
    )

    terminate_pid(123, expected_identity="owned identity")

    assert calls == [
        ("killpg", 123, signal.SIGTERM),
        ("kill", 123, signal.SIGTERM),
    ]


def test_terminate_pid_skips_sigkill_when_expected_identity_changes(monkeypatch):
    calls = []
    identities = iter(["owned identity", "different identity"])

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.os.killpg",
        lambda pid, sig: calls.append(("killpg", pid, sig)),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.pid_is_alive",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.process_identity",
        lambda pid: next(identities),
        raising=False,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.time.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.time.time",
        iter([0.0, 0.1, 10.0]).__next__,
    )

    terminate_pid(123, timeout=0.15, expected_identity="owned identity")

    assert calls == [("killpg", 123, signal.SIGTERM)]
