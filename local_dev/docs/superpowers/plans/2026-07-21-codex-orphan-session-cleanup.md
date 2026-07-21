# Codex Orphan Session Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent five-day Codex retention from deleting only a parent session and leaving orphaned subagent transcripts, then safely remove existing old and closed orphan sessions.

**Architecture:** Inventory will treat a missing external parent UUID as a synthetic logical root instead of an invalid graph. Each owner plan will carry every present session ID in descendant-first order, because the official `codex delete` command removes only the named session and does not cascade to children. Cleanup retains the existing path-set, fingerprint, and open-file gates before invoking the official CLI.

**Tech Stack:** Python 3.12+, pytest, Codex CLI, macOS `lsof`, graphify.

## Global Constraints

- Do not touch any currently open Codex rollout file.
- Apply the existing strict five-day retention threshold to orphan groups.
- Use only the official `codex delete --force <UUID>` command for persistent session deletion.
- Preserve source-before-Orca owner ordering and fail-closed behavior.
- Do not modify the user's existing `AGENTS.md` change or `.superpowers/` files.
- Do not commit unless the user explicitly requests a commit.

---

### Task 1: Define descendant-first owner deletion plans

**Files:**
- Modify: `local_dev/tests/test_session_inventory.py`
- Modify: `local_dev/serena_mcp_management/session_inventory.py`

**Interfaces:**
- Consumes: `group_ids`, `parents`, and per-home `CodexSessionFile` records.
- Produces: `OwnerDeletePlan.local_delete_ids: tuple[str, ...]`, containing every present local group member in descendant-first order.

- [x] **Step 1: Write failing inventory tests**

Add assertions proving that a root-child-grandchild group produces `(grandchild, child, root)` and that a missing parent becomes a synthetic root whose old descendants are eligible for cleanup.

- [x] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py -q
```

Expected: failures because missing parents are currently marked invalid and owner plans contain only local roots.

- [x] **Step 3: Implement the minimal inventory change**

Rename `local_root_ids` to `local_delete_ids`, order all local IDs by descending ancestry depth with a deterministic UUID tie-breaker, and resolve absent parents as synthetic roots rather than warnings.

- [x] **Step 4: Run inventory tests and verify GREEN**

Run the Task 1 pytest command and expect all tests to pass.

### Task 2: Delete every group member through the official CLI

**Files:**
- Modify: `local_dev/tests/test_session_cleanup.py`
- Modify: `local_dev/serena_mcp_management/session_cleanup.py`

**Interfaces:**
- Consumes: `OwnerDeletePlan.local_delete_ids` from Task 1.
- Produces: one `codex delete --force <UUID>` invocation per present session, children before parents, while preserving all existing safety gates.

- [x] **Step 1: Write a failing cleanup regression test**

Create a parent-child inventory and assert the runner receives child deletion before parent deletion in each owner home.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_session_cleanup.py -q
```

Expected: failure because cleanup currently invokes only `local_root_ids`.

- [x] **Step 3: Implement minimal cleanup iteration**

Iterate over `owner.local_delete_ids`; preserve source failure handling, Orca-copy preservation, timeout handling, path-set checks, fingerprint checks, and the second open-file snapshot.

- [x] **Step 4: Run cleanup tests and verify GREEN**

Run the Task 2 pytest command and expect all tests to pass.

### Task 3: Verify, deploy, and clean existing orphans

**Files:**
- Update generated graph files under `graphify-out/` with `graphify update .`.
- Mirror `local_dev/` to `~/Desktop/dotsync_config/agent_launcher/` using `make -C local_dev install-shim`.

**Interfaces:**
- Consumes: corrected inventory and cleanup behavior.
- Produces: deployed launcher plus zero old, closed orphan subagent sessions.

- [x] **Step 1: Run regression and full local_dev tests**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py local_dev/tests/test_session_cleanup.py -q
.venv/bin/python3 -m pytest local_dev/tests -q
```

- [x] **Step 2: Refresh the graph and deploy the runtime copy**

```bash
graphify update .
make -C local_dev install-shim
```

- [x] **Step 3: Re-scan immediately before deletion**

Build a fresh inventory, require all selected orphan records to be older than five days, verify their fingerprints, and require their inode identities to be absent from a fresh `lsof` snapshot.

- [x] **Step 4: Delete only selected orphan groups**

Pass an inventory restricted to missing-parent targets into `cleanup_codex_inventory`; stop without deletion on any path-set, fingerprint, or active-file mismatch.

- [x] **Step 5: Verify final state**

Re-scan and report remaining missing-parent groups, currently open rollouts, runtime/source file equality, test results, and any warnings. Do not claim completion unless all checks have fresh successful output.
