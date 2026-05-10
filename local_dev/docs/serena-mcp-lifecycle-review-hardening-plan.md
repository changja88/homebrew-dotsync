# Serena MCP Lifecycle Review Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use TDD for each behavior change. Implement in small steps and verify each targeted test before moving on.

**Goal:** 리뷰에서 확인된 Serena MCP lifecycle 위험을 줄여, 같은 scope 프로세스만 더 정확하게 정리하고 PID 재사용으로 인한 오종료 가능성을 낮춘다.

**Architecture:** 기존 registry/lease/watchdog 구조는 유지한다. `ServerRecord`에 managed process identity를 저장하고, 종료 직전 identity를 다시 확인한다. `ps` command text parsing은 공백 포함 path와 truncated command를 보수적으로 처리한다.

**Tech Stack:** Python stdlib, pytest, macOS process table commands.

---

## File Structure

- Modify: `local_dev/serena_mcp_management/serena_mcp/processes.py`
  - `ps` command text에서 `--project` 값이 공백을 포함해도 다음 option 전까지 복원한다.
  - parse된 process에 `process_identity`를 함께 담는다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/termination.py`
  - `expected_identity`가 있으면 SIGTERM/SIGKILL 직전 identity를 확인한다.
  - `killpg`가 process group을 찾지 못해도 PID가 살아 있으면 individual PID kill로 fallback한다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/registry.py`
  - `ServerRecord`에 `server_identity`, `proxy_identity`, `watchdog_identity`를 추가하고 legacy registry는 `None`으로 읽는다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/server.py`
  - 새 server/proxy 시작 시 identity를 기록한다.
  - healthy 판정과 registry/orphan 종료에서 identity를 사용한다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/watchdog.py`
  - watchdog duplicate 판단에 watchdog identity를 사용한다.
  - identity 없는 stale legacy lease는 PID만으로 live로 보존하지 않는다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
  - 필요하면 registered process identity를 진단 정보에 포함한다.
- Modify: `local_dev/docs/serena-mcp-lifecycle-spec.md`
  - registry 없는 proxy orphan은 현재 command만으로 scope를 증명하기 어렵다는 gap을 명시한다.

## Task 1: Safer Process Discovery

- [ ] Add failing tests in `local_dev/tests/test_serena_processes.py`:
  - unquoted `--project /path/with spaces --context codex`
  - unquoted `--project=/path/with spaces --context codex`
  - truncated command without `--context` remains fail-closed
- [ ] Implement option value recovery until the next `--option`.
- [ ] Attach `process_identity(pid)` to `SerenaMcpProcess`.
- [ ] Run `pytest local_dev/tests/test_serena_processes.py -q`.

## Task 2: Identity-Aware Termination

- [ ] Add failing tests in `local_dev/tests/test_serena_termination.py`:
  - `killpg` `ProcessLookupError` falls back to `os.kill` when PID is alive.
  - `expected_identity` prevents SIGKILL if PID identity changes after SIGTERM.
- [ ] Implement `terminate_pid(..., expected_identity=...)`.
- [ ] Run `pytest local_dev/tests/test_serena_termination.py -q`.

## Task 3: Registry and Server Identity

- [ ] Add failing tests for identity persistence and server health identity mismatch.
- [ ] Add identity fields to `ServerRecord`.
- [ ] Record server/proxy identities during `_start_healthy_server`.
- [ ] Pass expected identities when terminating registered records and same-scope orphans.
- [ ] Run `pytest local_dev/tests/test_serena_registry.py local_dev/tests/test_serena_server.py -q`.

## Task 4: Watchdog Identity

- [ ] Add failing tests for watchdog PID reuse and identity-less stale legacy leases.
- [ ] Store `watchdog_identity` after watchdog spawn.
- [ ] Require watchdog identity match before suppressing duplicate watchdog spawn.
- [ ] Treat stale leases with missing `launcher_identity` as expired.
- [ ] Run `pytest local_dev/tests/test_serena_watchdog.py -q`.

## Task 5: Docs and Verification

- [ ] Update the lifecycle spec known gaps for registry-less proxy process cleanup.
- [ ] Run targeted Serena lifecycle tests.
- [ ] Run full `local_dev/tests` suite, escalating only if localhost bind is sandbox-blocked.
- [ ] Run `git diff --check`.
