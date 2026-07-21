# Final Review Fix Report

Date: 2026-07-22

Reviewed base: `91e7881` (`feat/agent-startup-cleanup`)

## Scope

Implemented only the three Important findings in
`.superpowers/sdd/final-review-findings.md`:

- strict Claude zero-target validation;
- irreversible intra-session partial-mutation reporting;
- bounded concrete strict-inventory causes on the immediate launcher row.

No real Codex or Claude store was inspected or mutated. All destructive-path
tests use pytest temporary directories, injected inventories, fake Codex
runners, and injected filesystem failures. No deploy or graphify update was
run; those remain controller work.

## Result contract

`CleanupResult.deleted` continues to count only fully completed logical
sessions. `partial_mutations` separately counts successful member/root delete
operations inside the current incomplete logical session, while
`partial_mutation_details` retains at most the first three precise Codex
member/home or Claude root-path identifiers. The launcher derives a remainder
from the count and renders `+N more`.

On failure, the session row says `sessions fully deleted` and, when applicable,
adds `partial mutation: N operation(s) completed (...)` before the concrete
error. It does not imply zero mutation or rollback.

Strict inventory errors retain the original warnings and fold the first three
concrete causes into `error`, followed by `+N more`. Because the explicit
launcher renders `error` before returning `1`, the path/reason is visible even
though no shutdown summary is rendered.

## Finding 1: unsafe Claude zero-target inventory

RED:

```text
pytest local_dev/tests/test_claude_session_cleanup.py -k 'zero_targets' -q
5 failed, 1 passed, 22 deselected
```

The warning, wrong-client, wrong-policy, and missing-config cases all returned
the old successful early no-op.

GREEN:

```text
pytest local_dev/tests/test_claude_session_cleanup.py \
  -k 'zero_targets or inventory_warning' -q
7 passed, 21 deselected
```

Client, policy, warnings, and absolute config invariants now run before the
zero-target no-op. A valid warning-free zero-target inventory still invokes no
active/open snapshot callback.

## Finding 2: intra-session partial mutation

RED:

```text
pytest \
  local_dev/tests/test_session_cleanup.py::test_explicit_codex_cleanup_reports_intra_group_partial_mutation \
  local_dev/tests/test_claude_session_cleanup.py::test_cleanup_claude_reports_intra_bundle_partial_root_mutation \
  local_dev/tests/test_launcher_phases.py::test_explicit_session_cleanup_reports_bounded_partial_mutation -q
3 failed
```

Both cleanup tests failed because `CleanupResult` had no partial-mutation
fields; the launcher test failed because those constructor arguments were
unknown.

GREEN:

```text
same command
3 passed
```

Codex records each successful official `codex delete --force <UUID>` operation
within an incomplete logical group. Claude records each fully completed bounded
root removal within an incomplete bundle. Completing the whole target increments
`deleted` and clears the target-local partial evidence.

## Finding 3: concrete strict-inventory causes

RED:

```text
pytest \
  local_dev/tests/test_session_cleanup.py::test_explicit_codex_cleanup_treats_unsafe_inventory_as_failure \
  local_dev/tests/test_claude_session_cleanup.py::test_cleanup_claude_zero_targets_with_warning_fails_closed \
  local_dev/tests/test_launcher_phases.py::test_v2_main_inventory_failure_renders_bounded_causes_before_exit -q
4 failed
```

The cleanup errors and the real `_main_v2` exit-1 row contained only the generic
inventory error.

GREEN:

```text
same command
4 passed
```

The first three exact synthetic path/reason warnings now appear, the fourth is
not expanded, `+1 more` appears, return code is `1`, and child launch is absent.

## Verification

```text
/Users/hyun/Desktop/homebrew-dotsync/.venv/bin/python3 -m pytest \
  local_dev/tests/test_session_cleanup.py \
  local_dev/tests/test_claude_session_cleanup.py \
  local_dev/tests/test_session_inventory.py \
  local_dev/tests/test_claude_session_inventory.py \
  local_dev/tests/test_launcher_phases.py -q
200 passed in 2.29s
```

The first sandboxed full-suite run produced `506 passed, 5 failed`; every
failure was `PermissionError: [Errno 1] Operation not permitted` while
`test_serena_proxy.py` attempted to bind `127.0.0.1`. Re-running the identical
command outside the socket-restricted sandbox produced:

```text
/Users/hyun/Desktop/homebrew-dotsync/.venv/bin/python3 -m pytest local_dev/tests -q
511 passed in 8.09s
```

The identical final pre-commit rerun also passed: `511 passed in 8.80s`.

Python 3.12 verification:

```text
/opt/homebrew/bin/python3.12 -c '<touched imports>'
python3.12 imports ok

/opt/homebrew/bin/python3.12 -c '<zero-target/detail/result-contract assertions>'
python3.12 targeted contract ok

/opt/homebrew/bin/python3.12 -X pycache_prefix=/tmp/agent-startup-cleanup-py312 \
  -m compileall -q local_dev/serena_mcp_management local_dev/tests
exit 0
```

`/opt/homebrew/bin/python3.12 -m pytest ...` was not available because that
interpreter has no `pytest` module; the import, direct targeted contract, and
full compile checks above succeeded instead.

`git diff --check` exited `0`.

## Self-review

- Claude's zero-target callback-free no-op is after every existing strict
  invariant and before active/open snapshots.
- Codex mutation remains official CLI-only; no direct Codex store deletion was
  introduced.
- Claude deletion keeps the existing descriptor/no-follow, quarantine, active,
  open-file, root, and manifest gates.
- Partial evidence is target-local, counted only after an operation returns
  successfully, bounded to three identifiers, and discarded after full target
  completion.
- Existing defaults, prompts, colors, product scope, and noninteractive flow are
  unchanged.
- Serena: skipped after `initial_instructions` reported the main checkout rather
  than the required linked worktree; graphify plus worktree-local tools were
  used without activating or updating another project.

## Files changed

- `local_dev/serena_mcp_management/session_cleanup.py`
- `local_dev/serena_mcp_management/serena_agent_launcher.py`
- `local_dev/tests/test_session_cleanup.py`
- `local_dev/tests/test_claude_session_cleanup.py`
- `local_dev/tests/test_launcher_phases.py`
- `local_dev/README.md`
- `.superpowers/sdd/final-fix-report.md`
