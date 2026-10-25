"""The Codex cleanup's support check and keep-set. Every later cleanup item
deletes data of ids outside the keep-set, so anything the scan cannot read
must refuse the whole cleanup."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from dotsync.sessions import codex_cleanup, codex_migrations, procs, roots
from tests.sessions.codex_fixtures import (
    DATABASES,
    FIXTURES,
    add_edge,
    add_thread,
    make_codex_home,
    write_rollout,
)

THREAD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ROLLOUT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
META = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BASE = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OTHER = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
CHILD = "ffffffff-ffff-4fff-8fff-ffffffffffff"
STAMP = "2026-01-02T03-04-05"
ZSTD = "/fake/bin/zstd"
# The records Codex skips before the SessionMeta (codex-facts.md).
SKIPPED = (
    "inter_agent_communication_metadata",
    "compacted",
    "turn_context",
    "token_usage_record",
    "world_state",
    "realtime_item",
    "retained_context",
    "security_risk_score",
    "event_msg",
)


@pytest.fixture
def home():
    return make_codex_home(roots.codex_home())


class FakeZstd:
    """Stands in for `procs.stream_lines` running zstd. The `.zst` files in
    these tests hold plain JSON lines, served as `zstd -dc` would serve them.
    Records each call, and how many lines each stream handed out."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.served: dict[Path, int] = {}
        self.closed: list[Path] = []
        self.failure: Exception | None = None

    def __call__(self, argv, *, timeout):
        self.calls.append((argv, timeout))
        return self._stream(Path(argv[-1]))

    def _stream(self, path):
        if self.failure is not None:
            raise self.failure
        self.served[path] = 0
        try:
            for line in path.read_bytes().splitlines(keepends=True):
                self.served[path] += 1
                yield line
        finally:
            self.closed.append(path)


@pytest.fixture
def zstd(monkeypatch):
    fake = FakeZstd()
    monkeypatch.setattr(codex_cleanup, "zstd_binary", lambda: ZSTD)
    monkeypatch.setattr(procs, "stream_lines", fake)
    return fake


def run_sql(db: Path, sql: str) -> None:
    con = sqlite3.connect(db)
    con.execute(sql)
    con.commit()
    con.close()


def remove_db(db: Path) -> None:
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)


def replace_db(db: Path, content: bytes) -> None:
    remove_db(db)
    db.write_bytes(content)


def day_folder(home: Path) -> Path:
    folder = home / "sessions" / "2026" / "01" / "02"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def rollout_name(thread_id: str, rollout_id: str | None = None, zst: bool = False) -> str:
    suffix = f"_{rollout_id}" if rollout_id else ""
    return f"rollout-{STAMP}-{thread_id}{suffix}.jsonl{'.zst' if zst else ''}"


def write_lines(path: Path, records: list) -> Path:
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records))
    return path


# The support check


def test_known_checksums_match_the_fixture_files():
    recomputed = {
        folder: {
            int(sql.name.partition("_")[0]): hashlib.sha384(sql.read_bytes()).hexdigest().upper()
            for sql in sorted((FIXTURES / folder).glob("*.sql"))
        }
        for folder in ("state", "queue", "thread_history")
    }
    assert codex_migrations.KNOWN == recomputed
    assert [len(codex_migrations.KNOWN[f]) for f in ("state", "queue", "thread_history")] == [61, 2, 8]


def test_codex_0164_home_is_supported(home):
    assert codex_cleanup.support_check() == (None, True)


@pytest.mark.parametrize("db_name", list(DATABASES))
def test_unknown_migration_in_any_db_refuses(home, db_name):
    run_sql(
        home / db_name,
        "INSERT INTO _sqlx_migrations (version, description, success, checksum, execution_time)"
        " VALUES (9999, 'next', 1, x'00', 0)",
    )
    refusal, vacuum_available = codex_cleanup.support_check()
    assert refusal.code == "codex_version"
    assert db_name in refusal.message
    assert vacuum_available is False


@pytest.mark.parametrize("db_name", list(DATABASES))
@pytest.mark.parametrize(
    "change",
    [
        "UPDATE _sqlx_migrations SET checksum = x'00' WHERE version = 1",
        "UPDATE _sqlx_migrations SET success = 0 WHERE version = 2",
        "DELETE FROM _sqlx_migrations WHERE version = 2",
    ],
    ids=["changed_checksum", "failed_migration", "missing_migration"],
)
def test_any_other_migration_set_refuses(home, db_name, change):
    run_sql(home / db_name, change)
    refusal, _ = codex_cleanup.support_check()
    assert refusal.code == "codex_version"


@pytest.mark.parametrize("db_name", list(DATABASES))
def test_missing_db_refuses_as_an_unknown_version(home, db_name):
    remove_db(home / db_name)
    refusal, _ = codex_cleanup.support_check()
    assert refusal.code == "codex_version"


def test_db_without_applied_migrations_refuses_as_an_unknown_version(home):
    replace_db(home / "queue_1.sqlite", b"")  # an empty file is an empty database
    refusal, _ = codex_cleanup.support_check()
    assert refusal.code == "codex_version"


@pytest.mark.parametrize("db_name", list(DATABASES))
def test_db_that_cannot_be_read_refuses_with_db_failed(home, db_name):
    replace_db(home / db_name, b"not a database at all" * 100)
    refusal, _ = codex_cleanup.support_check()
    assert refusal.code == "codex_db_failed"


def test_dbs_are_read_from_the_sqlite_home(tmp_path, monkeypatch):
    sqlite_home = tmp_path / "elsewhere"
    make_codex_home(roots.codex_home(), sqlite_home)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(sqlite_home))
    assert codex_cleanup.support_check() == (None, True)


def test_pending_rollout_migration_refuses(home):
    folder = home / "rollout-migrations"
    folder.mkdir()
    (folder / f"{THREAD}.pending").touch()
    refusal, _ = codex_cleanup.support_check()
    assert (refusal.code, refusal.message) == ("codex_version", "Codex is migrating rollouts")


def test_staging_file_in_rollout_migrations_refuses(home):
    folder = home / "rollout-migrations"
    folder.mkdir()
    (folder / f".{THREAD}.pending.a1b2c3.tmp").touch()
    refusal, _ = codex_cleanup.support_check()
    assert refusal.code == "codex_version"


def test_other_files_in_rollout_migrations_do_not_refuse(home):
    folder = home / "rollout-migrations"
    folder.mkdir()
    (folder / ".DS_Store").touch()
    assert codex_cleanup.support_check() == (None, True)


def test_auto_vacuum_not_2_only_disables_vacuum(home):
    con = sqlite3.connect(home / "thread_history_1.sqlite")
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA auto_vacuum = 0")
    con.execute("VACUUM")
    assert con.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    con.close()
    assert codex_cleanup.support_check() == (None, False)


# The keep-set


def test_keep_set_takes_rollout_ids_from_file_names(home):
    write_rollout(home, THREAD, rollout_id=ROLLOUT, meta={"id": THREAD})
    write_rollout(home, OTHER)
    keep = codex_cleanup.keep_set()
    assert keep.ids == {THREAD, ROLLOUT, OTHER}
    assert (keep.unreadable, keep.staging) == (0, 0)
    assert codex_cleanup.scan_refusal(keep) is None


def test_keep_set_finds_meta_after_leading_non_meta_lines(home):
    path = day_folder(home) / rollout_name(THREAD)
    write_lines(
        path,
        [
            "",
            "   ",
            "not json {",
            "[1, 2]",
            {"no": "type"},
            {"type": "a_record_codex_cannot_parse", "payload": {}},
            *({"type": kind, "payload": {"id": OTHER}} for kind in SKIPPED),
            {"type": "session_meta", "payload": {"id": META}},
        ],
    )
    keep = codex_cleanup.keep_set()
    assert keep.ids == {THREAD, META}
    assert keep.unreadable == 0


def test_first_session_meta_returns_its_payload(home):
    payload = {"id": META, "cwd": "/work", "history_base": None}
    path = write_lines(
        day_folder(home) / rollout_name(THREAD),
        [{"type": "event_msg", "payload": {}}, {"type": "session_meta", "payload": payload}],
    )
    assert codex_cleanup.first_session_meta(path) == payload


def test_keep_set_takes_history_base_ids(home):
    write_rollout(home, THREAD, meta={"id": THREAD, "history_base": {"thread_id": BASE}})
    write_rollout(home, OTHER, meta={"id": OTHER, "history_base": None})
    keep = codex_cleanup.keep_set()
    assert keep.ids == {THREAD, BASE, OTHER}
    assert keep.unreadable == 0


def test_keep_set_reads_archived_and_zst(home, zstd):
    write_rollout(home, OTHER, archived=True, meta={"id": OTHER})
    compressed = write_rollout(
        home, THREAD, zst=True, meta={"id": META, "history_base": {"thread_id": BASE}}
    )
    with compressed.open("a") as f:
        f.write(json.dumps({"type": "response_item", "payload": {}}) + "\n")  # past the meta
    keep = codex_cleanup.keep_set()
    assert keep.ids == {OTHER, THREAD, META, BASE}
    assert keep.unreadable == 0
    assert zstd.calls == [([ZSTD, "-dc", "--", str(compressed)], 10)]
    assert zstd.served[compressed] == 1  # nothing past the SessionMeta was read
    assert zstd.closed == [compressed]


def test_keep_set_reads_a_rollout_and_its_zst_both(home, zstd):
    write_rollout(home, THREAD, meta={"id": THREAD})
    write_rollout(home, THREAD, zst=True, meta={"id": THREAD, "history_base": {"thread_id": BASE}})
    keep = codex_cleanup.keep_set()
    assert keep.ids == {THREAD, BASE}
    assert len(zstd.calls) == 1


def test_keep_set_takes_state_thread_and_edge_ids(home):
    add_thread(home, THREAD)
    add_edge(home, OTHER, CHILD)
    keep = codex_cleanup.keep_set()
    assert keep.ids == {THREAD, OTHER, CHILD}
    assert (keep.unreadable, keep.staging) == (0, 0)


def test_keep_set_reads_the_state_db_in_the_sqlite_home(tmp_path, monkeypatch):
    sqlite_home = tmp_path / "elsewhere"
    make_codex_home(roots.codex_home(), sqlite_home)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(sqlite_home))
    add_thread(sqlite_home, THREAD)
    assert codex_cleanup.keep_set().ids == {THREAD}


def test_keep_set_reports_progress_per_rollout(home):
    for thread_id in (THREAD, OTHER, CHILD):
        write_rollout(home, thread_id)
    calls = []
    codex_cleanup.keep_set(lambda done, total: calls.append((done, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


def _meta_first(kind):
    def build(home):
        write_lines(
            day_folder(home) / rollout_name(CHILD),
            [{"type": kind, "payload": {}}, {"type": "session_meta", "payload": {"id": CHILD}}],
        )

    return build


def _meta(payload):
    def build(home):
        write_lines(
            day_folder(home) / rollout_name(CHILD),
            [{"type": "session_meta", "payload": payload}],
        )

    return build


def _named(name):
    def build(home):
        write_lines(day_folder(home) / name, [{"type": "session_meta", "payload": {"id": CHILD}}])

    return build


def _no_meta_for(home, thread_id):
    write_lines(day_folder(home) / rollout_name(thread_id), [{"type": "event_msg", "payload": {}}])


def _no_meta(home):
    _no_meta_for(home, CHILD)


def _empty(home):
    (day_folder(home) / rollout_name(CHILD)).touch()


def _symlink(home):
    target = write_rollout(home.parent / "outside", CHILD)
    (day_folder(home) / rollout_name(CHILD)).symlink_to(target)


def _linked_folder(home):
    outside = home.parent / "outside-folder"
    write_rollout(outside, CHILD)
    (home / "sessions" / "2026" / "02").symlink_to(outside / "sessions" / "2026" / "01")


def _fifo(home):
    os.mkfifo(day_folder(home) / rollout_name(CHILD))


def _folder_named_like_a_rollout(home):
    (day_folder(home) / rollout_name(CHILD)).mkdir()


def _sessions_is_a_file(home):
    (home / "archived_sessions").write_text("")


UNREADABLE = {
    "response_item_first": _meta_first("response_item"),
    "inter_agent_communication_first": _meta_first("inter_agent_communication"),
    "no_session_meta": _no_meta,
    "empty_file": _empty,
    "meta_payload_not_an_object": _meta(["not", "an", "object"]),
    "meta_id_missing": _meta({"cwd": "/work"}),
    "meta_id_not_canonical": _meta({"id": CHILD.upper()}),
    "meta_id_not_a_string": _meta({"id": 7}),
    "history_base_not_an_object": _meta({"id": CHILD, "history_base": BASE}),
    "history_base_without_thread_id": _meta({"id": CHILD, "history_base": {}}),
    "history_base_id_not_canonical": _meta({"id": CHILD, "history_base": {"thread_id": BASE.upper()}}),
    "name_fails_the_regex": _named("rollout-garbage.jsonl"),
    "zst_name_fails_the_regex": _named("rollout-2026-01-02.jsonl.zst"),
    "name_thread_id_not_canonical": _named(rollout_name(CHILD.upper())),
    "name_rollout_id_not_canonical": _named(rollout_name(CHILD, BASE.upper())),
    "symlink": _symlink,
    "symlinked_folder": _linked_folder,
    "fifo": _fifo,
    "folder_named_like_a_rollout": _folder_named_like_a_rollout,
    "rollout_tree_is_a_file": _sessions_is_a_file,
}


@pytest.mark.parametrize("build", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_one_unreadable_rollout_refuses_everything(home, build):
    write_rollout(home, THREAD, rollout_id=ROLLOUT, meta={"id": META})
    build(home)
    keep = codex_cleanup.keep_set()
    assert keep.unreadable == 1
    assert {THREAD, ROLLOUT, META} <= keep.ids
    refusal = codex_cleanup.scan_refusal(keep)
    assert refusal.code == "incomplete_scan"
    assert refusal.message == "could not read 1 of the rollout files"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_rollout_that_cannot_be_opened_is_unreadable(home):
    path = write_rollout(home, CHILD)
    path.chmod(0)
    try:
        keep = codex_cleanup.keep_set()
    finally:
        path.chmod(0o644)
    assert keep.unreadable == 1
    assert codex_cleanup.scan_refusal(keep).code == "incomplete_scan"


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists any folder")
def test_folder_that_cannot_be_listed_is_unreadable(home):
    write_rollout(home, CHILD)
    folder = home / "sessions" / "2026" / "01"
    folder.chmod(0)
    try:
        keep = codex_cleanup.keep_set()
    finally:
        folder.chmod(0o755)
    assert keep.unreadable == 1
    assert codex_cleanup.scan_refusal(keep).code == "incomplete_scan"


def test_zstd_that_fails_makes_the_rollout_unreadable(home, zstd):
    write_rollout(home, THREAD, zst=True)
    zstd.failure = procs.ToolFailure("timeout", "zstd timed out")
    keep = codex_cleanup.keep_set()
    assert keep.unreadable == 1
    assert THREAD in keep.ids
    assert codex_cleanup.scan_refusal(keep).code == "incomplete_scan"


def test_unreadable_rollouts_are_counted(home):
    for thread_id in (THREAD, OTHER, CHILD):
        _no_meta_for(home, thread_id)
    keep = codex_cleanup.keep_set()
    assert codex_cleanup.scan_refusal(keep).message == "could not read 3 of the rollout files"


def test_missing_zstd_refuses(home, monkeypatch):
    def no_child(argv, *, timeout):
        raise AssertionError(f"nothing should run without zstd: {argv!r}")

    monkeypatch.setattr(codex_cleanup, "zstd_binary", lambda: None)
    monkeypatch.setattr(procs, "stream_lines", no_child)
    write_rollout(home, OTHER)
    assert codex_cleanup.scan_refusal(codex_cleanup.keep_set()) is None  # no .zst, no need

    write_rollout(home, THREAD, zst=True)
    keep = codex_cleanup.keep_set()
    assert keep.zstd_missing is True
    assert keep.unreadable == 1
    refusal = codex_cleanup.scan_refusal(keep)
    assert refusal.code == "incomplete_scan"
    assert "zstd" in refusal.message


def test_staging_file_refuses(home):
    path = write_rollout(home, THREAD)
    (path.parent / f".{path.name}.a1b2c3.tmp").write_text("half")
    keep = codex_cleanup.keep_set()
    assert (keep.staging, keep.unreadable) == (1, 0)
    assert THREAD in keep.ids
    assert codex_cleanup.scan_refusal(keep).code == "codex_version"


@pytest.mark.parametrize(
    "bad",
    [
        lambda home: add_thread(home, THREAD.upper()),
        lambda home: add_edge(home, "not-an-id", CHILD),
        lambda home: add_edge(home, OTHER, CHILD.upper()),
    ],
    ids=["thread_id", "edge_parent", "edge_child"],
)
def test_db_id_that_is_not_canonical_refuses_as_an_unknown_version(home, bad):
    bad(home)
    with pytest.raises(codex_cleanup.Refusal) as info:
        codex_cleanup.keep_set()
    assert info.value.code == "codex_version"


def test_keep_set_without_a_state_db_refuses_as_an_unknown_version(home):
    remove_db(home / "state_5.sqlite")
    with pytest.raises(codex_cleanup.Refusal) as info:
        codex_cleanup.keep_set()
    assert info.value.code == "codex_version"


def test_keep_set_with_an_unreadable_state_db_refuses_with_db_failed(home):
    replace_db(home / "state_5.sqlite", b"not a database at all" * 100)
    with pytest.raises(codex_cleanup.Refusal) as info:
        codex_cleanup.keep_set()
    assert info.value.code == "codex_db_failed"


def test_refusal_is_a_json_command_error():
    from dotsync.cli_json import JSONCommandError

    refusal = codex_cleanup.Refusal("codex_version", "why")
    assert isinstance(refusal, JSONCommandError)
    assert (refusal.code, refusal.message, str(refusal)) == ("codex_version", "why", "why")


def test_zstd_binary_prefers_homebrew(monkeypatch):
    monkeypatch.setattr(codex_cleanup.os, "access", lambda path, mode: path == "/opt/homebrew/bin/zstd")
    monkeypatch.setattr(codex_cleanup.shutil, "which", lambda name: "/usr/local/bin/zstd")
    assert codex_cleanup.zstd_binary() == "/opt/homebrew/bin/zstd"
    monkeypatch.setattr(codex_cleanup.os, "access", lambda path, mode: False)
    assert codex_cleanup.zstd_binary() == "/usr/local/bin/zstd"
    monkeypatch.setattr(codex_cleanup.shutil, "which", lambda name: None)
    assert codex_cleanup.zstd_binary() is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches any folder")
def test_rollout_tree_that_cannot_be_checked_is_unreadable(tmp_path, monkeypatch):
    sqlite_home = tmp_path / "elsewhere"
    codex_home = roots.codex_home()
    make_codex_home(codex_home, sqlite_home)
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(sqlite_home))
    write_rollout(codex_home, THREAD)
    codex_home.chmod(0o600)  # no search: whether sessions/ is there cannot be told
    try:
        keep = codex_cleanup.keep_set()
    finally:
        codex_home.chmod(0o755)
    assert keep.unreadable == 2  # both trees
    assert codex_cleanup.scan_refusal(keep).code == "incomplete_scan"
