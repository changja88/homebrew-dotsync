# Task 14 report — adversarial isolation and lifecycle coverage

Date: 2026-08-22

Status: COMPLETE with external native visual/accessibility gates recorded below.
No production code or build/release code is changed by this task.

## Scope delivered

- Added a real, executable fixture Codex CLI workflow covering two managed
  accounts through create, login, refresh, rename, reauthentication, logout,
  deletion, failed deletion, stale cache fallback, and force-local deletion.
- Proved each Codex invocation uses only its account-owned `HOME`, `CODEX_HOME`,
  `TMPDIR`, and probe directory; `config.toml` is mode `0600` and forces the
  top-level `cli_auth_credentials_store = "file"` contract.
- Added real `127.0.0.1` HTTP coverage for capability bootstrap, policy-first
  Claude rejection, correlated concurrent refresh, duplicate/delete/cancel/
  shutdown races, stale sync-plan generations, hostile request framing,
  filesystem-shaped labels, identifier-free menu summaries, and fixed error
  redaction.
- Added six real Python native-host lifecycle orders with a fixture Codex
  provider grandchild. Named pipes, `threading.Barrier`, and macOS `kqueue`
  `NOTE_EXIT` notifications provide deterministic process barriers; there are
  no sleep-based lifecycle oracles.
- Extended Swift coverage for trailing secret bytes, handshake timeout/exit,
  stop/termination-handler and stale-owner identity races, repeated Retry,
  external-origin redirects, nested bridged Objective-C bodies, malformed
  summary display, and polling during Quit.

## Account and secret isolation evidence

- Every Python isolation fixture sets a temporary `HOME`; no test targets the
  operator's real `~/.claude`, `~/.claude.json`, or `~/.codex`.
- The managed-account and native-lifecycle suites seed all three default-profile
  trees and compare file type, mode, `mtime_ns`, and contents before/after.
- The manual fixture review also compared the seeded default-profile timestamps
  before and after; all five entries remained byte-size/timestamp identical.
- Provider fixtures receive no `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or
  `ANTHROPIC_AUTH_TOKEN`.
- The native capability remains an in-memory test value. The real handshake
  token is not written to argv, environment, disk, diagnostics, or test logs.
  The loopback JavaScript boundary proves query erasure before state startup.
- Opening/bootstrap/menu-summary paths assert zero provider calls. Public
  Claude operations return the fixed `provider_policy_disabled` response before
  account, provider, or job work.

## Test-first and non-vacuity evidence

The new coverage is characterization coverage against already-correct Task
8–13 behavior, so no artificial product RED was manufactured.

Fixture/oracle REDs observed and corrected before GREEN:

1. The executable fixture initially failed with `env: python3: No such file or
   directory`; the cause was its intentionally narrowed fixture `PATH`. The
   fixture was corrected to include `os.defpath`.
2. The failed-logout oracle initially expected `logout_failed`; inspection of
   the fixed JSON-RPC normalization contract showed `-32001` correctly maps to
   `provider_unavailable`, and the test was corrected.
3. The web workflow initially read `job.result.snapshot`; the public API
   contract is `job.result.usage`, and the test oracle was corrected.
4. The provider-crash lifecycle oracle initially expected a failed JobRegistry
   record. The fixed API contract safely succeeds the job with
   `result.error_code = provider_unavailable`; the oracle now proves that shape.
5. Swift compilation and one launch-count assertion exposed test-fixture-only
   issues; both were corrected without production changes.

Temporary mutation proofs (each mutation produced the expected RED and was
immediately restored; final verification confirmed no production diff):

- Changed Codex `CODEX_HOME` from the account `home` to the account root:
  `test_managed_accounts.py` failed `2` tests because both snapshots became
  `provider_unavailable`.
- Disabled `_enforce_provider_policy` for Claude:
  the policy workflow failed with `[201, 404, 404, 404, 404]` instead of five
  `403` responses.
- Reversed native control EOF/control-byte exit codes:
  the native lifecycle test failed because the real backend returned `2`
  instead of `0` after EOF.
- Allowed WebKit top-level action/response origins unconditionally:
  the redirect test failed because the external response was allowed instead
  of cancelled.

## Automated verification

All prescribed commands were run once after the final behavior was in place:

| Command | Result |
| --- | --- |
| `.venv/bin/python3 -m pytest` | PASS — `1318 passed in 94.41s` |
| `node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs` | PASS — `11` tests |
| `swift test --package-path macos/DotSyncApp` | PASS — `105` XCTest tests |
| `PYTHONPATH=lib python3 -m dotsync --help` | PASS |
| `PYTHONPATH=lib python3 bin/dotsync --help` | PASS |
| `PYTHONPATH=lib python3 -m dotsync ui --check` | PASS |
| `python3 -m compileall -q lib tests` | PASS |
| `bash scripts/build_macos_app.sh` | PASS — assembled `build/DotSync.app` |
| `git diff --check` | PASS |

Focused iteration results:

- Managed accounts: `2 passed`.
- Real loopback workflows: `7 passed`.
- Native host lifecycle orders: `6 passed`.
- Combined new Python integration coverage: `15 passed`.
- Swift BackendProcess + WebSurface focus: `38 passed`.

Swift emitted the existing macOS 12 `WKProcessPool` deprecation warnings; no
new compiler error or test failure remains.

## Fixture visual/accessibility/native review

The `browser:control-in-app-browser` skill was used for the inspectable web
surface and caused the review to stay within browser accessibility snapshots
rather than claim inspection of an unattached native `WKWebView`.

Executed evidence:

- Started the production loopback application under an isolated fixture HOME
  with a fixed non-secret test capability.
- Captured actual manager and popover screenshots and accessibility snapshots.
- Opened and captured both immutable references:
  `docs/ui/concept-a/menu-bar-plus-management.html` and
  `docs/ui/concept-a/original-concepts.html`.
- Confirmed the actual surface retains the selected Concept A structure:
  menu-only 360×560 popover, separate management window, Overview/Accounts/
  Config Sync/Settings navigation, cached summaries, policy-disabled Claude
  copy, original-profile protection copy, and explicit preview/manager/Quit
  controls.
- Tab/Enter navigation moved from the manager chrome into Accounts without a
  pointer and retained named buttons/regions/headings in the accessibility tree.
- Long Korean copy was present and exposed through named accessibility nodes.
- Query erasure left both surfaces at the query-free root.
- CSS contains explicit dark-appearance and reduced-motion media rules.
- The fixture server and unsigned native process were stopped; the final
  process check found no fixture/native process, and default-profile timestamps
  remained unchanged.
- The existing fixture-backed native XCTest opened real AppKit-hosted popover
  and manager `WKWebView` roots, walked all manager destinations, delivered an
  Apply handoff, and awaited Quit; it passed in the full Swift suite.

Observed concern:

- Browser simulation at a 360×560 viewport with `documentElement` zoomed to
  200% reported `scrollWidth = 720` and horizontal overflow. Browser CSS zoom
  is not equivalent to native macOS accessibility zoom, so this is not claimed
  as a native failure; it remains a native visual verification concern.

Unexecuted external gates (not claimed as passed):

- Pixel-level comparison inside the unsigned app's native `WKWebView`.
- Full keyboard traversal of the AppKit menu extra/window chrome.
- VoiceOver names and order from macOS Accessibility Inspector.
- Native reduced-motion and light/dark appearance screenshots.
- Native 200% accessibility zoom and long-Korean layout.
- Closing the real manager window and observing menu-only presence.
- Clicking the real menu-extra Quit item and observing backend/provider teardown.

Reason: the unsigned AppKit process launched under the fixture HOME, but its
fixed executable resolver found no fixture backend child and System Events
returned no inspectable native window/menu accessibility tree. The process was
terminated and reaped rather than claiming an interaction that could not be
performed.

## Files changed

- `tests/integration/test_managed_accounts.py`
- `tests/integration/test_web_workflows.py`
- `tests/integration/test_native_host_lifecycle.py`
- `macos/DotSyncApp/Tests/DotSyncNativeTests/BackendProcessTests.swift`
- `macos/DotSyncApp/Tests/DotSyncNativeTests/WebSurfaceTests.swift`
- `.superpowers/sdd/2026-08-21-native-concept-a-completion/task-14-report.md`

## Self-review

- Diff is test/report-only; production, build, formula, and release files have
  no final changes.
- Tests assert public or real filesystem/process outcomes, not mock call return
  values. Provider call counts are used only to prove forbidden work did not
  begin.
- Race oracles use Event/Condition/Barrier/FIFO/kqueue primitives. The only
  bounded HTTP repetition is polling the documented asynchronous job endpoint;
  it does not determine process timing.
- Provider PIDs are checked while live, registered with one-shot kernel exit
  notifications, and checked absent after backend `Popen.wait`.
- Temporary build/cache and visual fixture directories were removed after
  verification.
- No concern requires a production change within Task 14. The native visual
  gates and browser-only 200% overflow observation remain for external review.

Serena: skipped — this linked worktree has no `.serena/project.yml`, and Task 14
is test/report-only coverage with no production symbol contract changes.

Graphify: skipped — this linked worktree has no `graphify-out/graph.json`.
