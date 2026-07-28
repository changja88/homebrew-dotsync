# Codex State-Aware Session Deletion Implementation Plan

> **Superseded:** State-aware cataloging remains for read-only inventory, but
> deletion no longer loops over selected IDs. A confirmed reset removes every
> known Codex session/state store and verifies a zero-session result.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover Codex threads persisted only in `state_<n>.sqlite`, delete every selected owner-local thread through the official CLI, and require both rollout and state metadata absence before reporting success.

**Architecture:** Extend the existing rollout catalog with read-only SQLite thread records and build logical groups from the union of rollout parent links and state spawn edges. Preserve per-session selection, invoke `codex delete --force` descendant-first for every selected ID, retain direct rollout cleanup as a file fallback, and verify selected IDs by re-reading state databases without ever modifying SQLite directly.

**Tech Stack:** Python 3.12+, stdlib `sqlite3`, pathlib, dataclasses, subprocess, pytest.

## Global Constraints

- Runtime dependencies remain stdlib-only.
- Never delete an entire `state_<n>.sqlite` file.
- Never issue direct SQL writes against a real Codex database.
- Treat state `rollout_path` as metadata only; never use it as a file-deletion target.
- Existing state databases are regular, non-symlink `state_<n>.sqlite` files under a known Codex home.
- An unreadable, unsafe, or incompatible existing state database makes the reset catalog unavailable; do not fall back to rollout-only deletion.
- Invoke the official CLI for every selected owner-local ID, deepest descendant first.
- Continue later IDs after one official-delete failure.
- A selected group succeeds only when its independently cataloged rollout paths and state rows are both absent.
- Empty selection remains an exact no-op.
- Keep the existing all-memory/history/log/snapshot reset after a confirmed non-empty selection.
- Do not terminate Codex Desktop, CLI processes, or app-server.
- Preserve unrelated notification-guard worktree changes.
- This is a user-approved dirty `main` checkout containing the preceding reset implementation. Do not create a partial implementation commit that omits those dependencies.

---

### Task 1: Add state-backed catalog regression tests

**Files:**
- Modify: `local_dev/tests/test_codex_reset.py`

**Interfaces:**
- Consumes: `scan_codex_session_catalog(home, codex_home, orca_codex_home)`.
- Produces: test fixtures for `state_<n>.sqlite`, state-only discovery, rollout/state merging, and owner-local descendant-first deletion IDs.

- [ ] **Step 1: Add a minimal SQLite fixture helper**

Add `import sqlite3` and this helper beside the existing rollout/history
helpers:

```python
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
```

- [ ] **Step 2: Add the state-only and merged-group tests**

Add a guardian UUID:

```python
GUARDIAN = "00000000-0000-4000-8000-000000000004"
```

Add:

```python
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
```

Add a fatal catalog test:

```python
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
```

Import `pytest` if it is not already imported.

- [ ] **Step 3: Run the catalog tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_codex_reset.py::test_catalog_merges_state_threads_and_lists_state_only_guardian \
  local_dev/tests/test_codex_reset.py::test_catalog_rejects_incompatible_state_database \
  -q
```

Expected: the state-only guardian test fails because the scanner ignores
SQLite, and the incompatible-database test fails because the scanner silently
ignores `state_5.sqlite`.

---

### Task 2: Implement read-only state discovery and union grouping

**Files:**
- Modify: `local_dev/serena_mcp_management/codex_reset.py`
- Test: `local_dev/tests/test_codex_reset.py`

**Interfaces:**
- Produces: `CodexCatalogThread`, `_read_catalog_threads(...)`, and a
  `CodexSessionCatalog` whose owners contain every local group ID.
- Preserves: existing `CodexCatalogFile`, `CodexSessionSummary.files`, picker
  labels, and public scan function signature.

- [ ] **Step 1: Add state record and database discovery primitives**

Add stdlib SQLite support and a strict filename pattern:

```python
import sqlite3

_STATE_DB_RE = re.compile(r"state_\d+\.sqlite")


@dataclass(frozen=True)
class CodexCatalogThread:
    session_id: str
    parent_id: str | None
    codex_home: Path
    state_db: Path
    cwd: str
    preview: str
    updated_ns: int
    archived: bool
```

Implement read-only discovery:

```python
def _read_catalog_threads(
    homes: tuple[Path, ...],
) -> list[CodexCatalogThread]:
    threads: list[CodexCatalogThread] = []
    for codex_home in homes:
        try:
            candidates = tuple(
                path
                for path in codex_home.iterdir()
                if _STATE_DB_RE.fullmatch(path.name)
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(
                f"cannot enumerate Codex state databases in {codex_home}: {exc}"
            ) from exc

        for state_db in sorted(candidates):
            try:
                state_stat = state_db.lstat()
            except OSError as exc:
                raise RuntimeError(
                    f"cannot inspect Codex state database {state_db}: {exc}"
                ) from exc
            if stat.S_ISLNK(state_stat.st_mode) or not stat.S_ISREG(
                state_stat.st_mode
            ):
                raise RuntimeError(
                    f"unsafe Codex state database: {state_db}"
                )
            try:
                with sqlite3.connect(
                    f"{state_db.as_uri()}?mode=ro",
                    uri=True,
                    timeout=1,
                ) as connection:
                    rows = connection.execute(
                        """
                        SELECT
                            id, cwd, title, preview, first_user_message,
                            updated_at, updated_at_ms, archived
                        FROM threads
                        """
                    ).fetchall()
                    edge_rows = connection.execute(
                        """
                        SELECT parent_thread_id, child_thread_id
                        FROM thread_spawn_edges
                        """
                    ).fetchall()
            except sqlite3.Error as exc:
                raise RuntimeError(
                    f"cannot read Codex state database {state_db}: {exc}"
                ) from exc

            parents: dict[str, str] = {}
            for parent_value, child_value in edge_rows:
                parent = _normalized_uuid(parent_value)
                child = _normalized_uuid(child_value)
                if parent is None or child is None:
                    raise RuntimeError(
                        f"invalid spawn edge in Codex state database {state_db}"
                    )
                previous = parents.setdefault(child, parent)
                if previous != parent:
                    raise RuntimeError(
                        f"conflicting spawn edge for Codex thread {child}"
                    )

            for row in rows:
                session_id = _normalized_uuid(row[0])
                if session_id is None:
                    raise RuntimeError(
                        f"invalid thread UUID in Codex state database {state_db}"
                    )
                updated_ms = row[6] or row[5] * 1_000
                preview = row[3] or row[2] or row[4]
                threads.append(
                    CodexCatalogThread(
                        session_id=session_id,
                        parent_id=parents.get(session_id),
                        codex_home=codex_home,
                        state_db=state_db,
                        cwd=_safe_label(row[1], limit=160),
                        preview=_safe_label(preview, limit=72),
                        updated_ns=int(updated_ms) * 1_000_000,
                        archived=bool(row[7]),
                    )
                )
    return threads
```

- [ ] **Step 2: Generalize grouping over rollout and state IDs**

Replace `_group_catalog_files` with a union grouping helper that:

```python
all_ids = {
    *(item.session_id for item in files),
    *(item.session_id for item in threads),
}
parent_candidates: dict[str, set[str | None]] = {
    session_id: set() for session_id in all_ids
}
for item in (*files, *threads):
    parent_candidates[item.session_id].add(item.parent_id)
```

For each ID, discard `None` when at least one concrete parent exists. Allow one
effective concrete parent; if rollout and state records name two different
concrete parents, raise `RuntimeError` instead of guessing. Reuse the existing
cycle detection and synthetic-missing-parent behavior over `all_ids`.

In `scan_codex_session_catalog`:

```python
threads = _read_catalog_threads(homes)
groups, parents = _group_catalog_entries(files, threads)
```

Choose display metadata from the newest root rollout, then newest root state
record, then the newest member record. Use history preview first and state
preview second.

Build each owner from the union of local rollout and state IDs. Set
`delete_ids` to every local ID ordered by:

```python
key=lambda session_id: (
    -_depth(session_id, parents=parents, group_ids=group_id_set),
    session_id,
)
```

This yields descendants before roots and includes unlinked state-only rows.

- [ ] **Step 3: Run the focused and existing catalog tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_codex_reset.py::test_catalog_lists_active_and_archived_roots_and_groups_descendants \
  local_dev/tests/test_codex_reset.py::test_catalog_merges_state_threads_and_lists_state_only_guardian \
  local_dev/tests/test_codex_reset.py::test_catalog_rejects_incompatible_state_database \
  -q
```

Expected: `3 passed`.

---

### Task 3: Add deletion and verification regression tests

**Files:**
- Modify: `local_dev/tests/test_codex_reset.py`

**Interfaces:**
- Consumes: state-aware catalog owners and
  `delete_selected_codex_sessions(...)`.
- Produces: failure coverage for residual state rows and success coverage for
  official per-ID state removal.

- [ ] **Step 1: Add a helper that emulates official state deletion**

```python
def _delete_state_row(state_db: Path, session_id: str) -> None:
    with sqlite3.connect(state_db) as connection:
        connection.execute(
            """
            DELETE FROM thread_spawn_edges
            WHERE parent_thread_id = ? OR child_thread_id = ?
            """,
            (session_id, session_id),
        )
        connection.execute(
            "DELETE FROM threads WHERE id = ?",
            (session_id,),
        )
```

This helper is test-only and models the already-verified side effect of the
official CLI on a temporary fixture.

- [ ] **Step 2: Add a residual-state failure test**

```python
def test_reset_fails_when_rollout_is_gone_but_state_row_remains(tmp_path):
    codex_home = tmp_path / ".codex"
    rollout = codex_home / "sessions/root.jsonl"
    _write_rollout(rollout, ROOT_A)
    _write_state_db(
        codex_home,
        [(ROOT_A, "/repo", "root", "preview", "", 100, None, 0)],
    )
    catalog = scan_codex_session_catalog(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )

    result = delete_selected_codex_sessions(
        catalog=catalog,
        selected_root_ids=(ROOT_A,),
        codex_binary="/fake/codex",
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "session is loaded"
        ),
    )

    assert rollout.exists() is False
    assert result.succeeded is False
    assert result.deleted_sessions == 0
    assert "state metadata" in (result.error or "")
```

- [ ] **Step 3: Add per-ID continuation and successful verification**

```python
def test_reset_calls_every_id_and_verifies_state_removal(tmp_path):
    codex_home = tmp_path / ".codex"
    state_db = _write_state_db(
        codex_home,
        [
            (ROOT_A, "/repo", "root", "root", "", 100, None, 0),
            (CHILD_A, "/repo", "child", "child", "", 200, None, 0),
            (GUARDIAN, "/old", "guardian", "guardian", "", 50, None, 0),
        ],
        edges=((ROOT_A, CHILD_A, "open"),),
    )
    catalog = scan_codex_session_catalog(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )
    calls = []

    def successful_cli(command, **kwargs):
        session_id = command[-1]
        calls.append(session_id)
        _delete_state_row(state_db, session_id)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = delete_selected_codex_sessions(
        catalog=catalog,
        selected_root_ids=(ROOT_A, GUARDIAN),
        codex_binary="/fake/codex",
        runner=successful_cli,
    )

    assert result.succeeded
    assert result.deleted_sessions == 2
    assert calls == [CHILD_A, ROOT_A, GUARDIAN]
    with sqlite3.connect(state_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM threads"
        ).fetchone() == (0,)
```

Add:

```python
def test_reset_continues_after_one_official_delete_failure(tmp_path):
    codex_home = tmp_path / ".codex"
    state_db = _write_state_db(
        codex_home,
        [
            (ROOT_A, "/repo", "root", "root", "", 100, None, 0),
            (CHILD_A, "/repo", "child", "child", "", 200, None, 0),
        ],
        edges=((ROOT_A, CHILD_A, "open"),),
    )
    catalog = scan_codex_session_catalog(
        home=tmp_path,
        codex_home=codex_home,
        orca_codex_home=tmp_path / "orca",
    )
    calls = []

    def mixed_cli(command, **kwargs):
        session_id = command[-1]
        calls.append(session_id)
        if session_id == CHILD_A:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "session is loaded",
            )
        _delete_state_row(state_db, session_id)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = delete_selected_codex_sessions(
        catalog=catalog,
        selected_root_ids=(ROOT_A,),
        codex_binary="/fake/codex",
        runner=mixed_cli,
    )

    assert calls == [CHILD_A, ROOT_A]
    assert result.succeeded is False
    assert result.deleted_sessions == 0
    assert "state metadata" in (result.error or "")
    with sqlite3.connect(state_db) as connection:
        assert connection.execute(
            "SELECT id FROM threads ORDER BY id"
        ).fetchall() == [(CHILD_A,)]
```

- [ ] **Step 4: Run the new deletion tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_codex_reset.py::test_reset_fails_when_rollout_is_gone_but_state_row_remains \
  local_dev/tests/test_codex_reset.py::test_reset_calls_every_id_and_verifies_state_removal \
  local_dev/tests/test_codex_reset.py::test_reset_continues_after_one_official_delete_failure \
  -q
```

Expected: failures show that deletion currently trusts missing rollout files,
does not verify state rows, and suppresses later IDs after a home-level
failure.

---

### Task 4: Implement per-ID deletion and state residual verification

**Files:**
- Modify: `local_dev/serena_mcp_management/codex_reset.py`
- Test: `local_dev/tests/test_codex_reset.py`

**Interfaces:**
- Consumes: `CodexSessionSummary.owners[*].delete_ids`.
- Produces: `CodexResetResult` whose success and `deleted_sessions` include
  state-row absence.

- [ ] **Step 1: Attempt every owner-local ID**

Remove `failed_official_homes`. Track failures by `(codex_home, session_id)`:

```python
official_failures: dict[tuple[Path, str], str] = {}
for session in selected:
    for owner in session.owners:
        for delete_id in owner.delete_ids:
            detail = _run_official_delete(
                codex_binary=codex_binary,
                codex_home=owner.codex_home,
                session_id=delete_id,
                runner=runner,
            )
            if detail is not None:
                official_failures[(owner.codex_home, delete_id)] = detail
                warnings.append(
                    f"official delete failed for {delete_id}: {detail}; "
                    "persisted rollout fallback will still run"
                )
```

Keep direct removal limited to `session.files`.

- [ ] **Step 2: Re-read state and detect residual selected IDs**

After file and trace cleanup:

```python
try:
    remaining_threads = _read_catalog_threads(catalog.homes)
except RuntimeError as exc:
    remaining_threads = []
    errors.append(f"cannot verify Codex state metadata: {exc}")

remaining_state_keys = {
    (thread.codex_home, thread.session_id)
    for thread in remaining_threads
}
selected_state_keys = {
    (owner.codex_home, delete_id)
    for session in selected
    for owner in session.owners
    for delete_id in owner.delete_ids
}
residual_state_keys = selected_state_keys & remaining_state_keys
if residual_state_keys:
    errors.append(
        "Codex reset left state metadata for: "
        + ", ".join(
            session_id
            for _, session_id in sorted(
                residual_state_keys,
                key=lambda item: (str(item[0]), item[1]),
            )[:5]
        )
    )
```

Do not treat a verification exception as an empty state set; the appended error
must force `succeeded` false.

- [ ] **Step 3: Include state absence in logical deletion counts**

Replace `deleted_sessions` with:

```python
deleted_sessions = sum(
    all(not _path_exists(item.path) for item in session.files)
    and all(
        (owner.codex_home, delete_id) not in remaining_state_keys
        for owner in session.owners
        for delete_id in owner.delete_ids
    )
    for session in selected
)
```

When state verification failed, force `deleted_sessions = 0` because absence
was not proven.

- [ ] **Step 4: Update the existing command expectation**

In `test_selected_reset_deletes_selected_group_and_global_codex_traces`, the
default-home group now attempts child then root, and the Orca copy attempts
root:

```python
assert [command[-1] for command, _ in commands] == [
    CHILD_A,
    ROOT_A,
    ROOT_A,
]
```

- [ ] **Step 5: Run all Codex reset tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_codex_reset.py -q
```

Expected: all tests pass.

---

### Task 5: Update launcher documentation and run regressions

**Files:**
- Modify: `local_dev/README.md`
- Verify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Verify: `local_dev/tests/test_launcher_phases.py`

**Interfaces:**
- Preserves: the approved keep/reset entry gate and `CodexResetSelection`
  orchestration.
- Documents: state-backed discovery, per-ID official deletion, and dual
  residual verification.

- [ ] **Step 1: Update the internal README**

In the Codex startup section, state:

- the catalog is the union of rollout JSONL and state-database thread rows;
- state-only rows with missing rollout files remain selectable;
- official deletion is attempted for every selected owner-local ID;
- the launcher never edits or deletes the state database directly; and
- success requires both rollout and state metadata absence.

Update the mechanism table with the same contract. Leave the public root
README untouched.

- [ ] **Step 2: Run focused launcher and reset suites**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_codex_reset.py \
  local_dev/tests/test_launcher_phases.py \
  local_dev/tests/test_ui_prompts.py \
  -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 4: Request a read-only adversarial review**

Review the implementation against:

`local_dev/docs/superpowers/specs/2026-07-28-codex-state-aware-session-deletion-design.md`

Require the reviewer to inspect SQLite safety, per-home ownership, descendant
ordering, continuation after failure, state residual verification, and
rollout-path trust boundaries. Fix every Critical or Important finding and
repeat the focused and full verification.

---

### Task 6: Promote and verify the installed launcher

**Files:**
- Runtime copy:
  `/Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/`
- Managed shell block: `/Users/hyun/.zshrc`

**Interfaces:**
- Consumes: fully verified source tree.
- Produces: installed runtime byte-identical to source.

- [ ] **Step 1: Install the shim**

Run:

```bash
make -C local_dev install-shim
```

- [ ] **Step 2: Verify source/runtime parity**

Run:

```bash
shasum -a 256 \
  local_dev/serena_mcp_management/codex_reset.py \
  /Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/codex_reset.py
shasum -a 256 \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  /Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py
```

Expected: each source/runtime pair has matching hashes.

- [ ] **Step 3: Run an installed-runtime read-only catalog smoke test**

Import the installed package with its runtime directory on `PYTHONPATH`, scan
the real catalog without selecting or deleting anything, and print only:

- logical session count;
- total owner-local deletion-ID count; and
- count of state-only logical sessions.

Expected on the currently observed machine: one rollout-backed current group
plus the previously hidden state-only guardian groups. Do not invoke
`delete_selected_codex_sessions` against the real home during this smoke test.
