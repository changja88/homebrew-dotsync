import json
import os
from types import SimpleNamespace

from local_dev.serena_mcp_management.serena_mcp.health import (
    dashboard_matches_project,
    http_endpoint_alive,
    normalize_dashboard_url,
    pid_is_alive,
    process_identity,
)


class Response:
    def __init__(self, body: bytes = b"ok", status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def test_pid_is_alive_for_current_process():
    assert pid_is_alive(os.getpid()) is True


def test_process_identity_returns_start_time_from_ps(monkeypatch):
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.health.sys.platform",
        "freebsd",
    )

    def fake_run(cmd, check, text, capture_output):
        # command= must NOT be queried — it is the volatile field that breaks
        # identity across macOS framework-Python re-exec.
        assert cmd == ["ps", "-o", "stat=", "-o", "lstart=", "-p", "1234"]
        assert "command=" not in cmd
        assert check is False
        assert text is True
        assert capture_output is True
        return SimpleNamespace(
            returncode=0,
            stdout="S Fri May  8 10:00:00 2026\n",
        )

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.health.subprocess.run", fake_run)

    assert process_identity(1234) == "ps:Fri May  8 10:00:00 2026"


def test_process_identity_returns_none_for_zombie_status(monkeypatch):
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.health.sys.platform",
        "freebsd",
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.health.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Z Fri May  8 10:00:00 2026 /usr/bin/python launcher\n",
        ),
    )

    assert process_identity(1234) is None


def test_process_identity_returns_none_when_ps_cannot_run(monkeypatch):
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.health.sys.platform",
        "freebsd",
    )
    def fake_run(*args, **kwargs):
        raise OSError("ps unavailable")

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.health.subprocess.run", fake_run)

    assert process_identity(1234) is None


def test_http_endpoint_alive_posts_json(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["method"] = request.get_method()
        seen["timeout"] = timeout
        return Response(b'{"jsonrpc":"2.0","id":1,"result":{}}')

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.health.urlopen", fake_urlopen)

    assert http_endpoint_alive("http://127.0.0.1:9123/mcp")
    assert seen == {"method": "POST", "timeout": 1.0}


def test_dashboard_matches_project_by_active_path(monkeypatch, tmp_path):
    body = json.dumps({"active_project": {"path": str(tmp_path.resolve())}}).encode()
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.health.urlopen", lambda url, timeout: Response(body))

    assert dashboard_matches_project("http://127.0.0.1:24282", tmp_path)


def test_dashboard_rejects_active_project_none(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.health.urlopen",
        lambda url, timeout: Response(b"Active Project: None"),
    )

    assert not dashboard_matches_project("http://127.0.0.1:24282", tmp_path)


def test_normalize_dashboard_url_keeps_only_origin():
    assert (
        normalize_dashboard_url("http://127.0.0.1:24282/dashboard/index.html")
        == "http://127.0.0.1:24282"
    )
