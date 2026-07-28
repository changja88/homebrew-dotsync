import json
import os
import sqlite3
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import codex_reset as codex_reset_module
from local_dev.serena_mcp_management.codex_reset import (
    scan_codex_session_catalog,
)
from local_dev.serena_mcp_management.memory_management import ClientProcess


ROOT_A = "00000000-0000-4000-8000-000000000001"
ROOT_B = "00000000-0000-4000-8000-000000000002"
CHILD_A = "00000000-0000-4000-8000-000000000003"
GUARDIAN = "00000000-0000-4000-8000-000000000004"
_PARSE_PROCESS_CONTEXT = (
    codex_reset_module._parse_codex_process_context
)


@pytest.fixture(autouse=True)
def _stub_process_environment(monkeypatch):
    monkeypatch.setattr(
        codex_reset_module,
        "_process_codex_context",
        lambda pid: ((), {}),
        raising=False,
    )


def _write_rollout(
    path: Path,
    session_id: str,
    *,
    parent_id: str | None = None,
    cwd: str = "/repo",
    mtime: int = 100,
) -> None:
    payload = {
        "id": session_id,
        "timestamp": "2026-07-28T10:00:00Z",
        "cwd": cwd,
    }
    if parent_id is not None:
        payload["parent_thread_id"] = parent_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "session_meta", "payload": payload}) + "\n"
    )
    os.utime(path, (mtime, mtime))


def _write_history(home: Path, rows: list[tuple[str, str]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "history.jsonl").write_text(
        "".join(
            json.dumps({"session_id": session_id, "text": text, "ts": index})
            + "\n"
            for index, (session_id, text) in enumerate(rows)
        )
    )


def _write_state_db(
    codex_home: Path,
    rows: list[
        tuple[str, str, str, str, str, int, int | None, int]
    ],
    *,
    edges: tuple[tuple[str, str, str], ...] = (),
) -> Path:
    path = codex_home / "state_5.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                preview TEXT NOT NULL,
                first_user_message TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                updated_at_ms INTEGER,
                archived INTEGER NOT NULL
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO threads (
                id, cwd, title, preview, first_user_message,
                updated_at, updated_at_ms, archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.executemany(
            """
            INSERT INTO thread_spawn_edges (
                parent_thread_id, child_thread_id, status
            ) VALUES (?, ?, ?)
            """,
            edges,
        )
    return path


def test_process_environment_reads_only_codex_state_variables():
    payload = (
        codex_reset_module.struct.pack("=i", 4)
        + b"/opt/homebrew/bin/codex\0\0"
        + b"codex\0-C\0/private/tmp/path with spaces\0resume\0"
        + b"CODEX_HOME=/tmp/runtime-home\0"
        + b"UNRELATED_SECRET=do-not-return\0"
        + b"CODEX_SQLITE_HOME=/tmp/runtime-state\0"
    )
    assert _PARSE_PROCESS_CONTEXT(payload) == (
        (
            "codex",
            "-C",
            "/private/tmp/path with spaces",
            "resume",
        ),
        {
            "CODEX_HOME": "/tmp/runtime-home",
            "CODEX_SQLITE_HOME": "/tmp/runtime-state",
        },
    )


def test_catalog_lists_active_and_archived_roots_and_groups_descendants(tmp_path):
    codex_home = tmp_path / ".codex"
    root_a = codex_home / "sessions/2026/07/28/root-a.jsonl"
    child_a = codex_home / "sessions/2026/07/28/child-a.jsonl"
    root_b = codex_home / "archived_sessions/root-b.jsonl"
    _write_rollout(root_a, ROOT_A, cwd="/work/alpha", mtime=200)
    _write_rollout(child_a, CHILD_A, parent_id=ROOT_A, mtime=300)
    _write_rollout(root_b, ROOT_B, cwd="/work/beta", mtime=100)
    _write_history(
        codex_home,
        [
            (ROOT_A, "first prompt"),
            (ROOT_A, "  latest\nrequest  "),
            (ROOT_B, "archived work"),
        ],
    )

    catalog = scan_codex_session_catalog(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert [session.root_id for session in catalog.sessions] == [ROOT_A, ROOT_B]
    assert catalog.sessions[0].preview == "latest request"
    assert catalog.sessions[0].cwd == "/work/alpha"
    assert {item.session_id for item in catalog.sessions[0].files} == {
        ROOT_A,
        CHILD_A,
    }
    assert catalog.sessions[1].archived is True


def test_catalog_merges_state_threads_and_lists_state_only_guardian(
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    _write_rollout(
        codex_home / "sessions/root.jsonl",
        ROOT_A,
        cwd="/work/root",
        mtime=200,
    )
    _write_rollout(
        codex_home / "sessions/child.jsonl",
        CHILD_A,
        parent_id=ROOT_A,
        cwd="/work/root",
        mtime=300,
    )
    _write_state_db(
        codex_home,
        [
            (ROOT_A, "/work/root", "root", "root preview", "", 200, None, 0),
            (CHILD_A, "/work/root", "child", "child preview", "", 300, None, 0),
            (
                GUARDIAN,
                "/work/old",
                "guardian",
                "guardian preview",
                "old request",
                100,
                None,
                0,
            ),
        ],
        edges=((ROOT_A, CHILD_A, "open"),),
    )

    catalog = scan_codex_session_catalog(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    by_root = {session.root_id: session for session in catalog.sessions}
    assert set(by_root) == {ROOT_A, GUARDIAN}
    assert {item.session_id for item in by_root[ROOT_A].files} == {
        ROOT_A,
        CHILD_A,
    }
    assert by_root[ROOT_A].owners[0].delete_ids == (CHILD_A, ROOT_A)
    assert by_root[GUARDIAN].files == ()
    assert by_root[GUARDIAN].preview == "guardian preview"
    assert by_root[GUARDIAN].owners[0].delete_ids == (GUARDIAN,)


def test_catalog_rejects_incompatible_state_database(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    with sqlite3.connect(codex_home / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE unrelated (id TEXT)")

    with pytest.raises(RuntimeError, match="state database"):
        scan_codex_session_catalog(
            home=tmp_path,
            codex_home=codex_home,
            orca_codex_home=tmp_path / "orca",
        )


def test_catalog_rejects_symlinked_state_database(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    outside = tmp_path / "outside.sqlite"
    with sqlite3.connect(outside) as connection:
        connection.execute("CREATE TABLE threads (id TEXT)")
    (codex_home / "state_5.sqlite").symlink_to(outside)

    with pytest.raises(RuntimeError, match="unsafe Codex state database"):
        scan_codex_session_catalog(
            home=tmp_path,
            codex_home=codex_home,
            orca_codex_home=tmp_path / "orca",
        )


def test_catalog_excludes_an_unsafe_broad_active_codex_home(tmp_path):
    catalog = scan_codex_session_catalog(
        home=tmp_path,
        codex_home=Path("/"),
        orca_codex_home=tmp_path / "orca",
    )

    assert Path("/") not in catalog.homes
    assert any("unsafe broad Codex home" in warning for warning in catalog.warnings)


def test_full_reset_removes_every_codex_session_and_trace_but_keeps_config(
    monkeypatch,
    tmp_path,
):
    """A reset must invalidate every persisted conversation source.

    This catches the old implementation regressing to selected rollout
    deletion while leaving state-only threads or desktop thread metadata.
    """
    codex_home = tmp_path / ".codex"
    orca_home = tmp_path / "orca"
    _write_rollout(
        codex_home / "sessions/2026/07/28/root-a.jsonl",
        ROOT_A,
    )
    _write_rollout(
        codex_home / "archived_sessions/root-b.jsonl",
        ROOT_B,
    )
    _write_rollout(
        orca_home / "sessions/2026/07/28/guardian.jsonl",
        GUARDIAN,
    )
    _write_state_db(
        codex_home,
        [
            (ROOT_A, "/repo", "root", "root", "", 100, None, 0),
            (ROOT_B, "/repo", "archive", "archive", "", 90, None, 1),
        ],
    )
    _write_state_db(
        orca_home,
        [(GUARDIAN, "/old", "guardian", "guardian", "", 80, None, 0)],
    )

    for store_home in (codex_home, orca_home):
        (store_home / "memories").mkdir(parents=True)
        (store_home / "memories/MEMORY.md").write_text("memory")
        (store_home / "memories_extensions/chronicle").mkdir(parents=True)
        (
            store_home / "memories_extensions/chronicle/MEMORY.md"
        ).write_text("chronicle")
        (store_home / "history.jsonl").write_text("{}\n")
        (store_home / "shell_snapshots").mkdir()
        (store_home / "shell_snapshots/snapshot.sh").write_text("snapshot")
        for trace_directory in (
            "session_snapshots",
            "snapshots",
            "visualizations",
            "log",
            "logs",
        ):
            (store_home / trace_directory).mkdir()
            (store_home / trace_directory / "trace.txt").write_text(
                "conversation trace"
            )
        (store_home / "ambient-suggestions").mkdir()
        (
            store_home / "ambient-suggestions/suggestions.json"
        ).write_text("{}")
        (store_home / "process_manager").mkdir()
        (store_home / "process_manager/chat_processes.json").write_text("{}")
        for database_name in (
            "goals_1.sqlite",
            "goals_1.sqlite-journal",
            "goals_1.sqlite-shm",
            "goals_1.sqlite-wal",
            "logs_2.sqlite",
            "logs_2.sqlite-journal",
            "logs_2.sqlite-shm",
            "logs_2.sqlite-wal",
            "memories_1.sqlite",
            "memories_1.sqlite-journal",
            "memories_1.sqlite-shm",
            "memories_1.sqlite-wal",
            "state_5.sqlite-journal",
            "state_5.sqlite-shm",
            "state_5.sqlite-wal",
        ):
            (store_home / database_name).touch()

        (store_home / "config.toml").write_text("model = 'gpt-test'\n")
        (store_home / "auth.json").write_text("{}")
        (store_home / "plugins").mkdir()
        (store_home / "plugins/keep.txt").write_text("keep")

    global_state = codex_home / ".codex-global-state.json"
    global_state.write_text(
        json.dumps(
            {
                "electron-persisted-atom-state": {
                    "chatgpt-last-selected-model-v1": {
                        "slug": "gpt-test",
                    },
                    "electron:window-zoom": 1.25,
                    "composer-prompt-drafts-v1": {
                        f"local:{ROOT_A}": "unfinished secret prompt",
                    },
                    "prompt-history": {"chatgpt-global": ["secret prompt"]},
                    "heartbeat-thread-permissions-by-id": {
                        ROOT_A: {"approvalPolicy": "on-request"}
                    },
                    "unread-thread-ids-by-host-v1": {
                        "local": [ROOT_A],
                    },
                    f"thread-browser-tabs-v1:{ROOT_A}": {
                        "tabs": [{"restoreUrl": "secret conversation URL"}],
                    },
                },
                "local-projects": {"/work/project": {"name": "keep me"}},
                "electron-main-window-bounds": {
                    "x": 10,
                    "y": 20,
                    "width": 1200,
                    "height": 800,
                },
                "queued-follow-ups": [{"threadId": ROOT_A}],
            }
        )
    )
    (codex_home / ".codex-global-state.json.bak").write_text(
        global_state.read_text()
    )

    desktop_db = codex_home / "sqlite/codex-dev.db"
    desktop_db.parent.mkdir(parents=True)
    with sqlite3.connect(desktop_db) as connection:
        connection.executescript(
            """
            CREATE TABLE automations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE local_app_server_feature_enablement (
                feature_name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE automation_runs (
                thread_id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL
            );
            CREATE TABLE inbox_items (
                id TEXT PRIMARY KEY,
                thread_id TEXT
            );
            CREATE TABLE local_thread_catalog (
                host_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                PRIMARY KEY (host_id, thread_id)
            );
            CREATE TABLE local_thread_catalog_hosts (
                host_id TEXT PRIMARY KEY
            );
            CREATE TABLE local_thread_catalog_metadata (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE local_thread_catalog_sync_state (
                host_id TEXT PRIMARY KEY
            );
            CREATE TABLE thread_timeline_ledger (
                host_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                PRIMARY KEY (host_id, thread_id, sequence)
            );
            INSERT INTO automations VALUES ('automation', 'keep me');
            INSERT INTO local_app_server_feature_enablement VALUES (
                'keep-feature',
                1
            );
            INSERT INTO automation_runs VALUES (
                '00000000-0000-4000-8000-000000000009',
                'automation'
            );
            INSERT INTO inbox_items VALUES (
                'inbox',
                '00000000-0000-4000-8000-000000000009'
            );
            INSERT INTO local_thread_catalog VALUES ('local', 'old-thread');
            INSERT INTO local_thread_catalog_hosts VALUES ('local');
            INSERT INTO local_thread_catalog_metadata VALUES (1);
            INSERT INTO local_thread_catalog_sync_state VALUES ('local');
            INSERT INTO thread_timeline_ledger VALUES (
                'local',
                'old-thread',
                1
            );
            """
        )

    app_log = tmp_path / "Library/Logs/com.openai.codex/2026/07/28/app.log"
    app_log.parent.mkdir(parents=True)
    app_log.write_text("conversation log")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
        raising=False,
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=orca_home,
    )

    assert result.succeeded
    assert result.discovered_sessions == 3
    assert result.deleted_sessions == 3
    for store_home in (codex_home, orca_home):
        for removed_name in (
            "sessions",
            "archived_sessions",
            "memories",
            "memories_extensions",
            "history.jsonl",
            "shell_snapshots",
            "session_snapshots",
            "snapshots",
            "visualizations",
            "log",
            "logs",
            "ambient-suggestions",
            "process_manager",
            "goals_1.sqlite",
            "goals_1.sqlite-journal",
            "goals_1.sqlite-shm",
            "goals_1.sqlite-wal",
            "logs_2.sqlite",
            "logs_2.sqlite-journal",
            "logs_2.sqlite-shm",
            "logs_2.sqlite-wal",
            "memories_1.sqlite",
            "memories_1.sqlite-journal",
            "memories_1.sqlite-shm",
            "memories_1.sqlite-wal",
            "state_5.sqlite",
            "state_5.sqlite-journal",
            "state_5.sqlite-shm",
            "state_5.sqlite-wal",
        ):
            assert (store_home / removed_name).exists() is False
        assert (store_home / "config.toml").exists()
        assert (store_home / "auth.json").exists()
        assert (store_home / "plugins/keep.txt").read_text() == "keep"
    expected_preserved_global_state = {
        "electron-persisted-atom-state": {
            "chatgpt-last-selected-model-v1": {
                "slug": "gpt-test",
            },
            "electron:window-zoom": 1.25,
        },
        "local-projects": {"/work/project": {"name": "keep me"}},
        "electron-main-window-bounds": {
            "x": 10,
            "y": 20,
            "width": 1200,
            "height": 800,
        },
    }
    assert json.loads(global_state.read_text()) == expected_preserved_global_state
    assert json.loads(
        (codex_home / ".codex-global-state.json.bak").read_text()
    ) == expected_preserved_global_state
    assert (tmp_path / "Library/Logs/com.openai.codex").exists() is False

    with sqlite3.connect(desktop_db) as connection:
        assert connection.execute(
            "SELECT id, name FROM automations"
        ).fetchall() == [("automation", "keep me")]
        assert connection.execute(
            """
            SELECT feature_name, enabled
            FROM local_app_server_feature_enablement
            """
        ).fetchall() == [("keep-feature", 1)]
        for table in (
            "automation_runs",
            "inbox_items",
            "local_thread_catalog",
            "local_thread_catalog_hosts",
            "local_thread_catalog_metadata",
            "local_thread_catalog_sync_state",
            "thread_timeline_ledger",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone() == (0,)
    assert b"old-thread" not in desktop_db.read_bytes()


def test_full_reset_terminates_codex_runtimes_including_desktop_app(
    monkeypatch,
    tmp_path,
):
    """A running CLI/app-server must not turn reset into a preservation path."""
    processes = (
        ClientProcess(
            pid=101,
            ppid=1,
            executable="/opt/homebrew/bin/codex",
            command="/opt/homebrew/bin/codex resume",
        ),
        ClientProcess(
            pid=102,
            ppid=100,
            executable="/Applications/Codex.app/Contents/Resources/codex",
            command=(
                "/Applications/Codex.app/Contents/Resources/codex app-server"
            ),
        ),
        ClientProcess(
            pid=100,
            ppid=1,
            executable="/Applications/Codex.app/Contents/MacOS/Codex",
            command="/Applications/Codex.app/Contents/MacOS/Codex",
        ),
    )
    scans = iter((processes, processes, (), (), ()))
    terminated: list[tuple[int, str | None]] = []
    reopened: list[bool] = []
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: next(scans),
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "_process_working_directory",
        lambda pid: tmp_path,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "process_identity",
        lambda pid: f"identity-{pid}",
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "terminate_pid",
        lambda pid, **kwargs: terminated.append(
            (pid, kwargs.get("expected_identity"))
        ),
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "pid_is_alive",
        lambda pid: False,
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "_reopen_codex_desktop",
        lambda: reopened.append(True) or None,
        raising=False,
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded
    assert result.terminated_processes == 3
    assert terminated == [
        (101, "identity-101"),
        (102, "identity-102"),
        (100, "identity-100"),
    ]
    assert result.desktop_restarted
    assert reopened == [True]


def test_full_reset_fails_if_desktop_cannot_reopen(
    monkeypatch,
    tmp_path,
):
    desktop = ClientProcess(
        pid=100,
        ppid=1,
        executable="/Applications/Codex.app/Contents/MacOS/Codex",
        command="/Applications/Codex.app/Contents/MacOS/Codex",
    )
    scans = iter(((desktop,), (desktop,), (), (), ()))
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: next(scans),
    )
    monkeypatch.setattr(
        codex_reset_module,
        "_process_working_directory",
        lambda pid: tmp_path,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "process_identity",
        lambda pid: f"identity-{pid}",
    )
    monkeypatch.setattr(
        codex_reset_module,
        "terminate_pid",
        lambda pid, **kwargs: None,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "pid_is_alive",
        lambda pid: False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "_reopen_codex_desktop",
        lambda: "Codex Desktop did not reappear after restart",
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert result.desktop_restarted is False
    assert "did not reappear" in (result.error or "")


def test_full_reset_does_not_signal_a_reused_pid(
    monkeypatch,
    tmp_path,
):
    process = ClientProcess(
        pid=404,
        ppid=1,
        executable="/opt/homebrew/bin/codex",
        command="/opt/homebrew/bin/codex app-server",
    )
    scans = iter(((process,), (process,), (), (), ()))
    identities = iter(("original-start", "reused-start"))
    termination_attempts: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: next(scans),
    )
    monkeypatch.setattr(
        codex_reset_module,
        "_process_working_directory",
        lambda pid: tmp_path,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "process_identity",
        lambda pid: next(identities),
    )
    monkeypatch.setattr(
        codex_reset_module,
        "terminate_pid",
        lambda pid, **kwargs: termination_attempts.append(
            (pid, kwargs.get("expected_identity"))
        ),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert termination_attempts == []
    assert "identity changed before termination" in (result.error or "")


def test_full_reset_fails_if_codex_runtime_respawns_during_mutation(
    monkeypatch,
    tmp_path,
):
    respawned = ClientProcess(
        pid=202,
        ppid=1,
        executable="/opt/homebrew/bin/codex",
        command="/opt/homebrew/bin/codex app-server",
    )
    scans = iter(((), (respawned,), (respawned,), (), ()))
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: next(scans),
    )
    monkeypatch.setattr(
        codex_reset_module,
        "process_identity",
        lambda pid: f"identity-{pid}",
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "terminate_pid",
        lambda pid, **kwargs: None,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "pid_is_alive",
        lambda pid: False,
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert result.terminated_processes == 1
    assert "appeared during Codex reset" in (result.error or "")


def test_full_reset_clears_unknown_desktop_thread_tables(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    desktop_db = codex_home / "sqlite/codex-dev.db"
    desktop_db.parent.mkdir(parents=True)
    marker = "future-thread-private-prompt"
    with sqlite3.connect(desktop_db) as connection:
        connection.execute(
            """
            CREATE TABLE future_thread_rows (
                thread_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO future_thread_rows VALUES (?, ?)",
            (ROOT_A, marker),
        )
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded
    with sqlite3.connect(desktop_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM future_thread_rows"
        ).fetchone() == (0,)
    assert marker.encode() not in desktop_db.read_bytes()


def test_full_reset_fails_when_desktop_sqlite_wal_is_still_open(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    desktop_db = codex_home / "sqlite/codex-dev.db"
    desktop_db.parent.mkdir(parents=True)
    marker = "open-wal-private-prompt"
    open_connection = sqlite3.connect(desktop_db)
    try:
        assert open_connection.execute(
            "PRAGMA journal_mode = WAL"
        ).fetchone() == ("wal",)
        open_connection.execute(
            """
            CREATE TABLE local_thread_catalog (
                host_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (host_id, thread_id)
            )
            """
        )
        open_connection.execute(
            "INSERT INTO local_thread_catalog VALUES (?, ?, ?)",
            ("local", ROOT_A, marker),
        )
        open_connection.commit()
        open_connection.execute("BEGIN")
        open_connection.execute(
            "SELECT payload FROM local_thread_catalog"
        ).fetchone()
        monkeypatch.setattr(
            codex_reset_module,
            "running_client_processes",
            lambda *args, **kwargs: (),
        )

        result = codex_reset_module.reset_all_codex_data(
            home=tmp_path,
            codex_home=codex_home,
            orca_codex_home=tmp_path / "orca",
        )

        assert result.succeeded is False
        assert (
            "WAL checkpoint is busy" in (result.error or "")
            or "SQLite sidecars" in (result.error or "")
            or "database is locked" in (result.error or "")
        )
    finally:
        open_connection.close()


def test_full_reset_fails_when_a_codex_runtime_survives_termination(
    monkeypatch,
    tmp_path,
):
    process = ClientProcess(
        pid=101,
        ppid=1,
        executable="/opt/homebrew/bin/codex",
        command="/opt/homebrew/bin/codex app-server",
    )
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (process,),
    )
    monkeypatch.setattr(
        codex_reset_module,
        "_process_working_directory",
        lambda pid: tmp_path,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "terminate_pid",
        lambda pid, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "process_identity",
        lambda pid: f"identity-{pid}",
        raising=False,
    )
    monkeypatch.setattr(
        codex_reset_module,
        "pid_is_alive",
        lambda pid: True,
        raising=False,
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert result.terminated_processes == 0
    assert "still running after termination" in (result.error or "")


def test_full_reset_deletes_known_data_but_fails_when_process_scan_is_unknown(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    rollout = codex_home / "sessions/2026/07/28/root.jsonl"
    _write_rollout(rollout, ROOT_A)
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("ps unavailable")
        ),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert rollout.exists() is False
    assert result.succeeded is False
    assert result.deleted_sessions == 1
    assert "cannot inspect running Codex processes" in (result.error or "")


def test_full_reset_fails_when_a_codex_config_cannot_be_parsed(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text("sqlite_home = [")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert "cannot read Codex config" in (result.error or "")
    assert (codex_home / "config.toml").exists()


def test_full_reset_rejects_symlinked_codex_home_without_following_it(
    monkeypatch,
    tmp_path,
):
    outside_home = tmp_path / "outside-codex"
    rollout = outside_home / "sessions/2026/07/28/root.jsonl"
    _write_rollout(rollout, ROOT_A)
    linked_home = tmp_path / ".codex"
    linked_home.symlink_to(outside_home, target_is_directory=True)
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=linked_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert result.discovered_sessions == 0
    assert "symlink component" in (result.error or "")
    assert linked_home.is_symlink()
    assert rollout.exists()


def test_full_reset_preserves_wrong_type_targets_and_fails(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    sqlite_home = tmp_path / "codex-state"
    log_dir = tmp_path / "codex-logs"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        f"sqlite_home = {json.dumps(str(sqlite_home))}\n"
        f"log_dir = {json.dumps(str(log_dir))}\n"
    )
    wrong_history = codex_home / "history.jsonl"
    wrong_history.mkdir()
    (wrong_history / "keep.txt").write_text("not a history file")
    sqlite_home.mkdir()
    wrong_database = sqlite_home / "state_17.sqlite"
    wrong_database.mkdir()
    (wrong_database / "keep.txt").write_text("not a database")
    log_dir.mkdir()
    wrong_log = log_dir / "codex-tui.log"
    wrong_log.mkdir()
    (wrong_log / "keep.txt").write_text("not a log file")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert "expected regular file" in (result.error or "")
    assert (wrong_history / "keep.txt").read_text() == "not a history file"
    assert (wrong_database / "keep.txt").read_text() == "not a database"
    assert (wrong_log / "keep.txt").read_text() == "not a log file"


def test_full_reset_rejects_log_dir_that_overlaps_codex_home(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    auth = codex_home / "auth.json"
    plugin = codex_home / "plugins/keep.txt"
    config.write_text(f"log_dir = {json.dumps(str(codex_home))}\n")
    auth.write_text("{}")
    plugin.parent.mkdir()
    plugin.write_text("keep")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert "overlaps protected Codex home" in (result.error or "")
    assert config.exists()
    assert auth.exists()
    assert plugin.read_text() == "keep"


def test_full_reset_rejects_log_dir_inside_preserved_plugin_tree(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    plugin_log_dir = codex_home / "plugins/example/runtime"
    plugin_log_dir.mkdir(parents=True)
    plugin_log = plugin_log_dir / "codex-tui.log"
    plugin_log.write_text("plugin-owned file")
    (codex_home / "config.toml").write_text(
        f"log_dir = {json.dumps(str(plugin_log_dir))}\n"
    )
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded is False
    assert "overlaps preserved Codex path" in (result.error or "")
    assert plugin_log.read_text() == "plugin-owned file"


def test_full_reset_clears_configured_sqlite_and_log_locations(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    sqlite_home = tmp_path / "codex-state"
    log_dir = tmp_path / "codex-logs"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        f"sqlite_home = {json.dumps(str(sqlite_home))}\n"
        f"log_dir = {json.dumps(str(log_dir))}\n"
    )
    sqlite_home.mkdir()
    (sqlite_home / "state_7.sqlite").write_text("state")
    (sqlite_home / "state_7.sqlite-wal").write_text("wal")
    (sqlite_home / "keep.txt").write_text("unrelated")
    log_dir.mkdir()
    (log_dir / "codex-tui.log").write_text("prompt and response")
    (log_dir / "codex-tui.log.1").write_text("older prompt")
    (log_dir / "session-root.jsonl").write_text("session log")
    (log_dir / "keep.txt").write_text("unrelated")
    nested_unrelated_log = log_dir / "exports/session-copy.jsonl"
    nested_unrelated_log.parent.mkdir()
    nested_unrelated_log.write_text("unrelated nested export")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
        raising=False,
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded
    assert (sqlite_home / "state_7.sqlite").exists() is False
    assert (sqlite_home / "state_7.sqlite-wal").exists() is False
    assert (sqlite_home / "keep.txt").read_text() == "unrelated"
    assert log_dir.exists()
    assert (log_dir / "codex-tui.log").exists() is False
    assert (log_dir / "codex-tui.log.1").exists() is False
    assert (log_dir / "session-root.jsonl").read_text() == "session log"
    assert (log_dir / "keep.txt").read_text() == "unrelated"
    assert nested_unrelated_log.read_text() == "unrelated nested export"


def test_full_reset_clears_cli_override_state_locations(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    launch_directory = tmp_path / "launch"
    effective_directory = tmp_path / "effective"
    cli_sqlite_home = effective_directory / "cli-state"
    cli_log_dir = effective_directory / "cli-logs"
    codex_home.mkdir()
    launch_directory.mkdir()
    cli_sqlite_home.mkdir(parents=True)
    (cli_sqlite_home / "state_13.sqlite").write_text("CLI state")
    (cli_sqlite_home / "keep.txt").write_text("unrelated")
    cli_log_dir.mkdir()
    (cli_log_dir / "codex-tui.log").write_text("CLI prompt")
    (cli_log_dir / "keep.txt").write_text("unrelated")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
        working_directory=launch_directory,
        cli_arguments=(
            "-C",
            "../effective",
            "--config",
            'sqlite_home="cli-state"',
            '--config=log_dir="cli-logs"',
        ),
    )

    assert result.succeeded
    assert (cli_sqlite_home / "state_13.sqlite").exists() is False
    assert (cli_sqlite_home / "keep.txt").read_text() == "unrelated"
    assert (cli_log_dir / "codex-tui.log").exists() is False
    assert (cli_log_dir / "keep.txt").read_text() == "unrelated"


def test_full_reset_clears_system_config_state_locations(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    system_sqlite_home = tmp_path / "system-state"
    system_log_dir = tmp_path / "system-logs"
    system_config = tmp_path / "etc/codex/config.toml"
    codex_home.mkdir()
    system_config.parent.mkdir(parents=True)
    system_config.write_text(
        f"sqlite_home = {json.dumps(str(system_sqlite_home))}\n"
        f"log_dir = {json.dumps(str(system_log_dir))}\n"
    )
    system_sqlite_home.mkdir()
    (system_sqlite_home / "state_15.sqlite").write_text("system state")
    (system_sqlite_home / "keep.txt").write_text("unrelated")
    system_log_dir.mkdir()
    (system_log_dir / "codex-tui.log").write_text("system prompt")
    (system_log_dir / "keep.txt").write_text("unrelated")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
        system_config_path=system_config,
    )

    assert result.succeeded
    assert (system_sqlite_home / "state_15.sqlite").exists() is False
    assert (system_sqlite_home / "keep.txt").read_text() == "unrelated"
    assert (system_log_dir / "codex-tui.log").exists() is False
    assert (system_log_dir / "keep.txt").read_text() == "unrelated"


def test_full_reset_clears_locations_from_every_codex_profile(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    profile_sqlite_home = tmp_path / "profile-state"
    profile_log_dir = tmp_path / "profile-logs"
    codex_home.mkdir()
    (codex_home / "review.config.toml").write_text(
        f"sqlite_home = {json.dumps(str(profile_sqlite_home))}\n"
        f"log_dir = {json.dumps(str(profile_log_dir))}\n"
    )
    profile_sqlite_home.mkdir()
    (profile_sqlite_home / "state_9.sqlite").write_text("profile state")
    (profile_sqlite_home / "keep.txt").write_text("unrelated")
    profile_log_dir.mkdir()
    (profile_log_dir / "codex-tui.log").write_text("profile log")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    assert result.succeeded
    assert (profile_sqlite_home / "state_9.sqlite").exists() is False
    assert (profile_sqlite_home / "keep.txt").read_text() == "unrelated"
    assert profile_log_dir.exists()
    assert (profile_log_dir / "codex-tui.log").exists() is False


def test_full_reset_clears_trusted_project_config_state_locations(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    project_root = tmp_path / "repo"
    working_directory = project_root / "src/nested"
    working_directory.mkdir(parents=True)
    (project_root / ".git").mkdir()
    (codex_home / "config.toml").write_text(
        f"[projects.{json.dumps(str(project_root))}]\n"
        "trust_level = 'trusted'\n"
    )

    project_sqlite_home = tmp_path / "project-state"
    project_log_dir = tmp_path / "project-logs"
    project_config = project_root / ".codex/config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        f"sqlite_home = {json.dumps(str(project_sqlite_home))}\n"
        f"log_dir = {json.dumps(str(project_log_dir))}\n"
    )
    project_sqlite_home.mkdir()
    (project_sqlite_home / "state_11.sqlite").write_text("project state")
    (project_sqlite_home / "keep.txt").write_text("unrelated")
    project_log_dir.mkdir()
    (project_log_dir / "codex-tui.log").write_text("project prompt")
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
        working_directory=working_directory,
    )

    assert result.succeeded
    assert (project_sqlite_home / "state_11.sqlite").exists() is False
    assert (project_sqlite_home / "keep.txt").read_text() == "unrelated"
    assert project_log_dir.exists()
    assert (project_log_dir / "codex-tui.log").exists() is False


def test_full_reset_ignores_untrusted_project_config_state_locations(
    monkeypatch,
    tmp_path,
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    (codex_home / "config.toml").write_text(
        f"[projects.{json.dumps(str(project_root))}]\n"
        "trust_level = 'untrusted'\n"
    )
    external_logs = tmp_path / "untrusted-logs"
    external_logs.mkdir()
    external_log = external_logs / "codex-tui.log"
    external_log.write_text("not loaded by Codex")
    project_config = project_root / ".codex/config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        f"log_dir = {json.dumps(str(external_logs))}\n"
    )
    monkeypatch.setattr(
        codex_reset_module,
        "running_client_processes",
        lambda *args, **kwargs: (),
    )

    result = codex_reset_module.reset_all_codex_data(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
        working_directory=project_root,
    )

    assert result.succeeded
    assert external_log.read_text() == "not loaded by Codex"
