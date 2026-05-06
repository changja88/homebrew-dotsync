import os
import time

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    remove_lease,
    touch_lease,
)


def test_registry_adds_and_removes_lease(tmp_path):
    scope = Scope(tmp_path, "codex")
    record = ServerRecord(
        server_pid=123,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(tmp_path.resolve()),
        client_type="codex",
        started_at=1.0,
        leases={},
    )

    with locked_registry(scope) as registry:
        registry.record = record
        touch_lease(registry, Lease("lease-a", os.getpid(), time.time()))

    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert "lease-a" in registry.record.leases
        remove_lease(registry, "lease-a")

    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert registry.record.leases == {}


def test_registry_missing_file_loads_no_record(tmp_path):
    scope = Scope(tmp_path, "claude")

    with locked_registry(scope) as registry:
        assert registry.record is None


def test_registry_treats_corrupt_json_as_no_record(tmp_path):
    scope = Scope(tmp_path, "codex")
    path = tmp_path / ".serena" / "dotsync-mcp" / "codex" / "registry.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    with locked_registry(scope) as registry:
        assert registry.record is None
