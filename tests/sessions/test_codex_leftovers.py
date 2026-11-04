"""What deleted Codex sessions leave outside Codex's session stores, found by
the dry run and removed by the cleanup: the ChatGPT app's sidebar catalogs
and its state file, the logs, the shell snapshots and the generated images.
Only ids outside the keep-set count, as for every other item; whatever is not
plainly a deleted session's stays byte for byte."""

import json
import os
import sqlite3

import pytest

from dotsync.sessions import codex_cleanup, codex_keepset, procs, roots
from dotsync.sessions.stop import StopSignal
from tests.sessions.codex_fixtures import add_thread, make_codex_home, write_rollout

# Codex makes thread ids with UUIDv7.
KEPT = "01a00000-0000-7000-8000-00000000000a"
GONE = "01a00000-0000-7000-8000-00000000000b"
GONE2 = "01a00000-0000-7000-8000-00000000000c"
GONE3 = "01a00000-0000-7000-8000-00000000000d"
CHAT = "01a00000-0000-7000-8000-0000000000c7"  # a ChatGPT conversation in the sidebar
DRAFT = "11111111-1111-4111-8111-111111111111"  # a UUID the app uses for something else
LOCAL_HOST = "local"
CHAT_HOST = "chatgpt:workspace:user"
DONE_AT = 1_793_337_644_724  # last_full_reconciled_at, in ms


@pytest.fixture(autouse=True)
def nothing_runs(monkeypatch):
    monkeypatch.setattr(procs, "ps_table", lambda: {})
    monkeypatch.setattr(procs, "lsof_scan", lambda fds=None: {})


@pytest.fixture
def home():
    db_dir = make_codex_home(roots.codex_home())
    add_thread(db_dir, KEPT)
    write_rollout(roots.codex_home(), KEPT)
    lock = roots.codex_home() / "thread-writer-locks" / ".coordination.lock"
    lock.parent.mkdir()
    lock.write_bytes(b"")
    return roots.codex_home()


def found():
    return codex_cleanup.orphans(codex_keepset.keep_set())


def item(item_id):
    payload = codex_cleanup.dry_run_payload()
    assert payload["refusal"] is None
    return {i["id"]: i for i in payload["items"]}[item_id]


def run(*items):
    report = codex_cleanup.run_cleanup(
        list(items), False, stop=StopSignal(), progress=lambda done, total, phase: None
    )
    return {r["id"]: r for r in report["items"]}


# The ChatGPT app's sidebar catalogs (<codex home>/sqlite/codex.db, codex-dev.db)


def catalog(name, rows, *, synced=None, shape="known", hosts=()):
    """A catalog as the app makes it: `rows` are (host, thread id, missing);
    `synced` maps a host to its last_full_reconciled_at (default DONE_AT);
    `hosts` adds (host id, kind) pairs."""
    folder = roots.codex_home() / "sqlite"
    folder.mkdir(exist_ok=True)
    path = folder / name
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = WAL")
    missing = "missing_candidate INTEGER NOT NULL DEFAULT 0," if shape == "known" else ""
    con.executescript(
        f"""
        CREATE TABLE local_thread_catalog (
            host_id TEXT NOT NULL, thread_id TEXT NOT NULL, display_title TEXT NOT NULL,
            observation_sequence INTEGER NOT NULL, {missing}
            PRIMARY KEY (host_id, thread_id));
        CREATE TABLE local_thread_catalog_hosts (host_id TEXT PRIMARY KEY, host_kind TEXT NOT NULL);
        CREATE TABLE local_thread_catalog_sync_state (
            host_id TEXT PRIMARY KEY, watermark_updated_at REAL,
            initial_build_complete INTEGER NOT NULL DEFAULT 0,
            observation_sequence INTEGER NOT NULL DEFAULT 0, last_full_reconciled_at INTEGER);
        INSERT INTO local_thread_catalog_hosts VALUES ('{LOCAL_HOST}', 'local'), ('{CHAT_HOST}', 'chatgpt');
        """
    )
    con.executemany("INSERT INTO local_thread_catalog_hosts VALUES (?, ?)", hosts)
    synced = {LOCAL_HOST: DONE_AT, CHAT_HOST: DONE_AT} if synced is None else synced
    for host, at in synced.items():
        con.execute("INSERT INTO local_thread_catalog_sync_state VALUES (?, 1, 1, 4, ?)", (host, at))
    for host, thread_id, gone in rows:
        if shape == "known":
            con.execute(
                "INSERT INTO local_thread_catalog VALUES (?, ?, 'a title', 1, ?)", (host, thread_id, gone)
            )
        else:
            con.execute("INSERT INTO local_thread_catalog VALUES (?, ?, 'a title', 1)", (host, thread_id))
    con.commit()
    con.close()
    return path


def catalog_rows(path):
    con = sqlite3.connect(path)
    try:
        return sorted(con.execute("SELECT host_id, thread_id, missing_candidate FROM local_thread_catalog"))
    finally:
        con.close()


def synced(path):
    con = sqlite3.connect(path)
    try:
        return dict(con.execute("SELECT host_id, last_full_reconciled_at FROM local_thread_catalog_sync_state"))
    finally:
        con.close()


def test_the_sidebar_counts_deleted_sessions_the_app_still_shows(home):
    catalog(
        "codex.db",
        [(LOCAL_HOST, KEPT, 0), (LOCAL_HOST, GONE, 0), (LOCAL_HOST, GONE2, 1), (CHAT_HOST, GONE3, 0)],
    )
    assert found().catalog_ids == {GONE}
    row = item("app_list")
    assert (row["label"], row["count"], row["checked"]) == ("ChatGPT 앱 목록에 남은 지운 세션", 1, True)


def test_the_cleanup_has_the_app_check_its_whole_list_again(home):
    """The app marks what is gone itself when it next starts: dotsync only
    clears the local host's last full check, as the app's own migrations do."""
    path = catalog("codex.db", [(LOCAL_HOST, KEPT, 0), (LOCAL_HOST, GONE, 0), (CHAT_HOST, GONE3, 0)])
    rows = catalog_rows(path)
    result = run("app_list")["app_list"]
    assert (result["outcome"], result["count"]) == ("done", 1)
    assert synced(path) == {LOCAL_HOST: None, CHAT_HOST: DONE_AT}
    assert catalog_rows(path) == rows
    assert item("app_list")["count"] == 0  # the check is due: nothing left to do


def test_a_check_already_due_counts_nothing(home):
    catalog("codex.db", [(LOCAL_HOST, GONE, 0)], synced={LOCAL_HOST: None, CHAT_HOST: DONE_AT})
    assert found().catalog_ids == set()


def test_the_old_catalog_loses_the_rows_of_deleted_sessions(home):
    """codex-dev.db is the catalog of the app's non-production builds; the
    app that runs here does not open it, so its rows are deleted outright."""
    path = catalog("codex-dev.db", [(LOCAL_HOST, KEPT, 0), (LOCAL_HOST, GONE, 1), (CHAT_HOST, GONE2, 0)])
    assert found().catalog_ids == {GONE}
    assert run("app_list")["app_list"]["count"] == 1
    assert catalog_rows(path) == [(CHAT_HOST, GONE2, 0), (LOCAL_HOST, KEPT, 0)]
    assert found().catalog_ids == set()


def test_an_id_in_both_catalogs_counts_once(home):
    catalog("codex.db", [(LOCAL_HOST, GONE, 0)])
    catalog("codex-dev.db", [(LOCAL_HOST, GONE, 0), (LOCAL_HOST, GONE2, 0)])
    assert item("app_list")["count"] == 2


def test_a_catalog_of_another_shape_is_left_alone(home):
    path = catalog("codex.db", [(LOCAL_HOST, GONE, 0)], shape="old")
    before = path.read_bytes()
    assert found().catalog_ids == set()
    assert run("app_list")["app_list"]["count"] == 0
    assert path.read_bytes() == before


def test_no_catalog_is_nothing_to_clean(home):
    assert found().catalog_ids == set()
    assert item("app_list")["count"] == 0


# The ChatGPT app's state file (.codex-global-state.json and its .bak)

STATE = ".codex-global-state.json"


def app_state(*ids):
    """The app's state with an entry of every kind for each of `ids`, and
    some that belong to no session."""
    atoms = {"has-seen-intro": True, "제목": "한글 그대로"}
    for i in ids:
        atoms |= {
            f"thread-reference-capability:{i}": {"canReference": True},
            f"thread-tab-routes-v1:{i}": ["a", "b", "c"],
            f"thread-client-id-v1:local%3A{i}": "client",
            f"codex-writing-block-deleted-thread-v1:{i}": True,
        }
    by_id = {i: {"value": i} for i in ids}
    atoms |= {
        "heartbeat-thread-permissions-by-id": dict(by_id),
        "thread-descriptions-v1": dict(by_id),
        "prompt-history": {**by_id, DRAFT: ["a draft"]},
        "client-thread-bindings-v1": {f"local-chatgpt:{n}": i for n, i in enumerate(ids)},
        "recent-workspace-files-v3": {'["local","/work"]': [f"mentions {ids[0]}" if ids else "x"]},
    }
    return {
        "desktop-first-seen-at-ms": 1,
        "electron-persisted-atom-state": atoms,
        "projectless-thread-ids": list(ids),
        "thread-projectless-output-directories": {i: "/out" for i in ids},
        "thread-workspace-root-hints": {i: "/work" for i in ids},
        "thread-writable-roots": {i: ["/work"] for i in ids},
        "electron-thread-read-state-v1": {
            "version": 1,
            "unreadByIdentity": {"me": {"local:abc": list(ids), "ssh:server": list(ids)}},
            "legacyMigration": {"unreadThreadIdsByHostId": {"local": list(ids), "ssh:server": list(ids)}},
        },
    }


def app_catalog():
    """The active catalog with no thread in it: the state can be judged."""
    if not (roots.codex_home() / "sqlite" / "codex.db").exists():
        catalog("codex.db", [])


def write_state(data, name=STATE):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    (roots.codex_home() / name).write_bytes(raw)
    return raw


def read_state(name=STATE):
    return json.loads((roots.codex_home() / name).read_bytes())


def test_the_state_counts_the_deleted_sessions_it_holds(home):
    catalog("codex.db", [(CHAT_HOST, CHAT, 0)])
    write_state(app_state(KEPT, GONE, CHAT, DRAFT))
    assert found().state_ids == {GONE}
    row = item("app_state")
    assert (row["label"], row["count"]) == ("ChatGPT 앱 상태에 남은 지운 세션", 1)


def test_the_cleanup_drops_every_entry_of_a_deleted_session_from_both_files(home):
    catalog("codex.db", [(CHAT_HOST, CHAT, 0)])
    write_state(app_state(KEPT, GONE, GONE2, CHAT))
    write_state(app_state(GONE), STATE + ".bak")
    result = run("app_state")["app_state"]
    assert (result["outcome"], result["count"]) == ("done", 2)
    want = app_state(KEPT, CHAT)
    # What only mentions an id, and what is keyed by no session, stays as it was.
    want["electron-persisted-atom-state"]["recent-workspace-files-v3"] = {
        '["local","/work"]': [f"mentions {KEPT}"]
    }
    want["electron-persisted-atom-state"]["client-thread-bindings-v1"] = {
        "local-chatgpt:0": KEPT,
        "local-chatgpt:3": CHAT,
    }
    # Unread lists of another host are that host's.
    every = [KEPT, GONE, GONE2, CHAT]
    want["electron-thread-read-state-v1"]["unreadByIdentity"]["me"]["ssh:server"] = every
    want["electron-thread-read-state-v1"]["legacyMigration"]["unreadThreadIdsByHostId"]["ssh:server"] = every
    assert read_state() == want
    bak = app_state()
    bak["electron-persisted-atom-state"]["recent-workspace-files-v3"] = {'["local","/work"]': [f"mentions {GONE}"]}
    bak["electron-thread-read-state-v1"]["unreadByIdentity"]["me"]["ssh:server"] = [GONE]
    bak["electron-thread-read-state-v1"]["legacyMigration"]["unreadThreadIdsByHostId"]["ssh:server"] = [GONE]
    assert read_state(STATE + ".bak") == bak


def test_the_state_keeps_its_own_layout(home):
    app_catalog()
    write_state(app_state(KEPT, GONE))
    run("app_state")
    raw = (roots.codex_home() / STATE).read_bytes()
    assert raw == json.dumps(read_state(), ensure_ascii=False, separators=(",", ":")).encode()
    assert "한글 그대로".encode() in raw


def test_ids_that_are_not_codex_sessions_stay(home):
    """A v4 UUID is not a Codex thread id, and a ChatGPT conversation in the
    sidebar is not a deleted session, even though neither is in the keep-set."""
    catalog("codex.db", [(CHAT_HOST, CHAT, 0)])
    raw = write_state(app_state(KEPT, CHAT, DRAFT))
    assert found().state_ids == set()
    run("app_state")
    assert (roots.codex_home() / STATE).read_bytes() == raw


def test_a_state_with_nothing_to_drop_is_not_rewritten(home):
    write_state(app_state(KEPT))
    before = os.stat(roots.codex_home() / STATE)
    run("app_state")
    after = os.stat(roots.codex_home() / STATE)
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_an_unread_mark_alone_is_counted(home):
    app_catalog()
    data = app_state()
    data["electron-thread-read-state-v1"]["unreadByIdentity"]["me"]["local:abc"] = [GONE]
    write_state(data)
    assert found().state_ids == {GONE}


def test_a_key_of_another_host_is_that_hosts(home):
    """`thread-client-id-v1:local%3A<id>` is a local thread's; with another
    host before the id, that id is the other host's thread, and every entry
    of it stays."""
    app_catalog()
    data = app_state()
    data["electron-persisted-atom-state"][f"thread-client-id-v1:ssh%3Aserver%3A{GONE}"] = "client"
    data["electron-persisted-atom-state"]["thread-descriptions-v1"] = {GONE: "a remote thread"}
    data["projectless-thread-ids"] = [GONE]
    raw = write_state(data)
    assert found().state_ids == set()
    run("app_state")
    assert (roots.codex_home() / STATE).read_bytes() == raw


def test_a_remote_thread_named_in_one_file_is_kept_in_both(home):
    """The host is in one file's key only: the other file's entries of that
    thread stay too, however the two files differ."""
    app_catalog()
    primary = app_state(GONE)
    primary["electron-persisted-atom-state"][f"thread-client-id-v1:ssh%3Aserver%3A{GONE2}"] = "client"
    write_state(primary)
    backup = app_state()
    backup["electron-persisted-atom-state"]["thread-descriptions-v1"] = {GONE2: "a remote thread"}
    backup["projectless-thread-ids"] = [GONE2]
    raw = write_state(backup, STATE + ".bak")
    assert found().state_ids == {GONE}
    run("app_state")
    assert (roots.codex_home() / (STATE + ".bak")).read_bytes() == raw
    assert f"thread-client-id-v1:ssh%3Aserver%3A{GONE2}" in read_state()["electron-persisted-atom-state"]


def test_with_a_remote_host_the_state_is_left_alone(home):
    """A thread of an SSH, WSL or remote-control host is a UUIDv7 too, and
    no local store says it lives: the state cannot be told apart."""
    catalog("codex.db", [], hosts=[("ssh:server", "ssh")])
    raw = write_state(app_state(GONE))
    assert found().state_ids == set()
    run("app_state")
    assert (roots.codex_home() / STATE).read_bytes() == raw


def test_without_the_apps_catalog_the_state_is_left_alone(home):
    """No catalog says whether the app knows remote hosts."""
    raw = write_state(app_state(GONE))
    assert found().state_ids == set()
    run("app_state")
    assert (roots.codex_home() / STATE).read_bytes() == raw


def test_a_symlinked_catalog_folder_is_not_followed(home, tmp_path):
    elsewhere = tmp_path / "other-profile"
    catalog("codex-dev.db", [(LOCAL_HOST, GONE, 0)])
    (home / "sqlite").rename(elsewhere)
    os.symlink(elsewhere, home / "sqlite")
    rows = catalog_rows(elsewhere / "codex-dev.db")
    raw = write_state(app_state(GONE))
    assert (found().catalog_ids, found().state_ids) == (set(), set())
    run("app_list", "app_state")
    assert catalog_rows(elsewhere / "codex-dev.db") == rows
    assert (roots.codex_home() / STATE).read_bytes() == raw


def test_a_state_put_in_place_counts_even_if_its_folder_sync_fails(home, monkeypatch):
    app_catalog()
    write_state(app_state(GONE))
    syncs = []

    def full_fsync(fd):
        syncs.append(fd)
        if len(syncs) == 2:  # the folder's, after the rename
            raise OSError("folder sync failed")

    monkeypatch.setattr(codex_cleanup, "_full_fsync", full_fsync)
    result = run("app_state")["app_state"]
    assert (result["outcome"], result["count"]) == ("failed", 1)
    assert found().state_ids == set()


def test_without_a_readable_catalog_the_state_is_left_alone(home):
    """The catalogs say which ids are ChatGPT conversations: one that cannot
    be read leaves the state as it is, and the rest of the cleanup runs."""
    folder = home / "sqlite"
    folder.mkdir()
    (folder / "codex.db").write_bytes(b"not a database")
    raw = write_state(app_state(GONE))
    assert codex_cleanup.dry_run_payload()["refusal"] is None
    assert found().state_ids == set()
    run("app_state")
    assert (roots.codex_home() / STATE).read_bytes() == raw


@pytest.mark.parametrize(
    "data",
    [
        f"not json {GONE}",
        json.dumps([GONE]),
        json.dumps({"electron-persisted-atom-state": [GONE], "projectless-thread-ids": [GONE]}),
    ],
)
def test_a_state_of_another_shape_is_left_alone(home, data):
    path = roots.codex_home() / STATE
    path.write_text(data)
    before = path.read_bytes()
    assert found().state_ids == set()
    run("app_state")
    assert path.read_bytes() == before


def test_a_symlinked_catalog_is_not_followed(home, tmp_path):
    """A catalog that is a link may be another profile's: its rows are not
    this keep-set's to judge."""
    other = catalog("codex-dev.db", [(LOCAL_HOST, GONE, 0)])
    target = tmp_path / "other-profile.db"
    other.rename(target)
    os.symlink(target, other)
    rows = catalog_rows(target)
    assert found().catalog_ids == set()
    run("app_list")
    assert catalog_rows(target) == rows


def test_a_state_file_that_is_a_link_is_left_alone_and_blocks_nothing(home, tmp_path):
    from tests.sessions.codex_fixtures import add_history

    add_history(roots.codex_home(), GONE2)
    app_catalog()
    target = tmp_path / "elsewhere.json"
    target.write_bytes(json.dumps(app_state(GONE)).encode())
    os.symlink(target, roots.codex_home() / (STATE + ".bak"))
    write_state(app_state(GONE))
    assert codex_cleanup.dry_run_payload()["refusal"] is None
    results = run("history_rows", "app_state")
    assert (results["history_rows"]["count"], results["app_state"]["count"]) == (1, 1)
    assert json.loads(target.read_bytes()) == app_state(GONE)


def test_a_catalog_that_fails_midway_reports_what_it_changed(home, monkeypatch):
    active = catalog("codex.db", [(LOCAL_HOST, GONE, 0)])
    catalog("codex-dev.db", [(LOCAL_HOST, GONE2, 0)])
    real = codex_cleanup._writing

    def writing(db):
        if db.name == "codex-dev.db":
            raise sqlite3.OperationalError("disk I/O error")
        return real(db)

    monkeypatch.setattr(codex_cleanup, "_writing", writing)
    result = run("app_list")["app_list"]
    assert (result["outcome"], result["count"]) == ("failed", 1)
    assert synced(active)[LOCAL_HOST] is None


# The logs (logs_2.sqlite)


def logs(rows):
    con = sqlite3.connect(roots.codex_home() / "logs_2.sqlite")
    con.execute(
        "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,"
        " ts_nanos INTEGER NOT NULL, level TEXT NOT NULL, target TEXT NOT NULL,"
        " feedback_log_body TEXT, thread_id TEXT, process_uuid TEXT,"
        " estimated_bytes INTEGER NOT NULL DEFAULT 0)"
    )
    con.executemany(
        "INSERT INTO logs (ts, ts_nanos, level, target, thread_id) VALUES (0, 0, 'INFO', 't', ?)",
        [(r,) for r in rows],
    )
    con.commit()
    con.close()


def log_threads():
    con = sqlite3.connect(roots.codex_home() / "logs_2.sqlite")
    try:
        return sorted((t or "", n) for t, n in con.execute("SELECT thread_id, COUNT(*) FROM logs GROUP BY 1"))
    finally:
        con.close()


def test_the_logs_of_deleted_sessions_are_counted_and_deleted(home):
    logs([GONE] * 3 + [GONE2, KEPT, None, None, "not-an-id"])
    assert found().log_rows == {GONE: 3, GONE2: 1}
    row = item("logs")
    assert (row["label"], row["count"], row["lines"]) == ("로그에 남은 지운 세션", 2, 4)
    result = run("logs")["logs"]
    assert (result["outcome"], result["count"], result["lines"]) == ("done", 2, 4)
    assert log_threads() == [("", 2), (KEPT, 1), ("not-an-id", 1)]


def test_no_logs_is_nothing_to_clean(home):
    assert found().log_rows == {}


def test_symlinked_logs_are_not_followed(home, tmp_path):
    logs([GONE])
    target = tmp_path / "other-logs.sqlite"
    (home / "logs_2.sqlite").rename(target)
    os.symlink(target, home / "logs_2.sqlite")
    assert found().log_rows == {}
    run("logs")
    con = sqlite3.connect(target)
    assert con.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 1
    con.close()


# The shell snapshots (shell_snapshots/<thread id>.<stamp>.sh)


def test_the_shell_snapshots_of_deleted_sessions_go(home):
    folder = home / "shell_snapshots"
    folder.mkdir()
    for name in (f"{GONE}.1.sh", f"{GONE}.2.sh", f"{KEPT}.1.sh", "zsh.sh", f"{GONE2}"):
        (folder / name).write_text("snapshot")
    os.symlink(folder / f"{KEPT}.1.sh", folder / f"{GONE3}.1.sh")
    assert found().snapshot_names == [f"{GONE}.1.sh", f"{GONE}.2.sh", GONE2]
    row = item("shell_snapshots")
    assert (row["label"], row["count"]) == ("지운 세션의 셸 스냅숏", 3)
    assert run("shell_snapshots")["shell_snapshots"]["count"] == 3
    assert sorted(os.listdir(folder)) == sorted([f"{KEPT}.1.sh", "zsh.sh", f"{GONE3}.1.sh"])


# The generated images (generated_images/<thread id>/)


def test_the_images_of_deleted_sessions_go(home, tmp_path):
    images = home / "generated_images"
    (images / GONE / "sub").mkdir(parents=True)
    (images / GONE / "a.png").write_bytes(b"123")
    (images / GONE / "sub" / "b.png").write_bytes(b"12345")
    (images / KEPT).mkdir()
    (images / KEPT / "c.png").write_bytes(b"1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "d.png").write_bytes(b"1")
    os.symlink(outside, images / GONE2)
    (images / GONE3).write_bytes(b"a file, not a folder")
    assert found().image_bytes == {GONE: 8}
    row = item("images")
    assert (row["label"], row["count"], row["bytes"]) == ("지운 세션의 생성 이미지", 1, 8)
    result = run("images")["images"]
    assert (result["outcome"], result["count"]) == ("done", 1)
    assert sorted(os.listdir(images)) == sorted([KEPT, GONE2, GONE3])
    assert (outside / "d.png").exists()


# Together


def test_the_items_come_in_this_order(home):
    payload = codex_cleanup.dry_run_payload()
    assert [i["id"] for i in payload["items"]] == [
        "history_rows",
        "history_lines",
        "session_index",
        "queue",
        "app_list",
        "app_state",
        "logs",
        "shell_snapshots",
        "images",
    ]


def test_a_kept_id_is_never_a_leftover(home):
    catalog("codex.db", [(LOCAL_HOST, KEPT, 0)])
    write_state(app_state(KEPT))
    logs([KEPT])
    (home / "shell_snapshots").mkdir()
    (home / "shell_snapshots" / f"{KEPT}.1.sh").write_text("")
    (home / "generated_images" / KEPT).mkdir(parents=True)
    leftovers = found()
    assert (
        leftovers.catalog_ids,
        leftovers.state_ids,
        leftovers.log_rows,
        leftovers.snapshot_names,
        leftovers.image_bytes,
    ) == (set(), set(), {}, [], {})
