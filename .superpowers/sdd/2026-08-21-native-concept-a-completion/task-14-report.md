# Task 14 report — adversarial isolation and lifecycle coverage

Date: 2026-08-22

Status: COMPLETE after review fix round 1, with the native manual
visual/accessibility gates below still external and not claimed as passed.

## Outcome

All nine Important proof gaps from `task-14-review.md` are closed with real
filesystem, HTTP, process, packaged-JavaScript, or WKWebView outcomes. The only
production edit is the controller-authorized package-internal
`processFactory: @Sendable () -> Process` injection in `BackendProcess`; its
default remains a new Foundation `Process`, and the public initializer and API
are unchanged. No build, release, formula, or packaged-web production code is
changed.

## Review-gap closure

1. **Persistent account cache correlation.** The two-account workflow refreshes
   Personal after Work, then fails Work again. It inspects both distinct
   `usage/<account-id>/snapshot.json` files and their embedded `account_id`,
   loads each cache entry, and proves failed Work receives only Work's stale
   snapshot.
2. **Force-local disclosure.** A Node harness runs the packaged `app.mjs`, real
   reducer/render/dialog code, and the real loopback delete/job API. It proves
   the first official logout failure renders the user-visible Korean warning
   that local deletion is irreversible, and that the confirmed retry sends the
   exact `force_local` intent.
3. **Distinct parent-crash order.** A separate relay process owns the backend
   control pipe. The test kills that relay with `SIGKILL`, so OS pipe EOF—not a
   direct test close—causes backend shutdown, and exact relay/backend/provider
   PIDs receive `kqueue NOTE_EXIT` and disappear.
4. **Published terminal job barrier.** The old eight-request polling helper is
   removed. A test-only `sitecustomize` wrapper signals a FIFO only after the
   real `JobRegistry` terminal result is published; the test makes one GET and
   checks the exact `provider_unavailable` result.
5. **Concurrent Quit/completion reconciliation.** FIFO and `Barrier` gates
   retain the job ID, overlap control EOF with completion publication, record
   the one terminal result, count exactly one real `WebApplication.shutdown`,
   and consume exactly one provider exit record.
6. **Real surface lifecycle.** XCTest starts the real Python native host through
   `BackendProcess`, seeds a cached Codex account, exposes an executable fixture
   provider, creates the packaged popover and manager in real `WKWebView`
   surfaces, waits for both documents, dismantles both hosts, closes the manager
   state, and quits. The cached summary is 58%, the provider launch count stays
   zero before/after dismantle and Quit, and seeded default profiles are
   unchanged.
7. **Capability containment.** The native test parses and removes the one
   protocol-authorized stdout handshake frame, then requires zero token bytes in
   trailing stdout, stderr/diagnostics, backend/provider argv and environment,
   captured test output, and every regular file in the isolated HOME/hook/temp
   root. The web test checks Node stdout and stderr, pytest stdout and stderr,
   and the complete isolated HOME including sync/app-owned data. Query erasure
   and nonpersistent WebKit configuration remain covered.
8. **Numeric PID reuse.** Two distinct forwarding `Process` proxy owners expose
   the same numeric PID while running two real fixture children. A system double
   observes Foundation process state without ever signalling the fake numeric
   PID. Releasing the stale termination callback cannot affect the replacement
   owner.
9. **Production redirect delegate.** A real local server redirects the exact
   launch origin to a second local origin. A production `WebSurface` hosted in
   WKWebView cancels before the external server receives a request and forwards
   no bridge command.

## Test-first and mutation evidence

Each coverage gap was tied to a concrete bad implementation that the previous
suite allowed. Temporary mutations were applied, observed RED, and restored
before final GREEN:

| Gap | Previously allowed bad implementation | Strengthened RED | Final GREEN |
| --- | --- | --- | --- |
| Account cache | `_cache_file` returns one global snapshot | per-account cache file missing | focused `1 passed` |
| Force disclosure | generic warning copy | exact irreversible warning assertion failed | focused `1 passed in 3.20s` |
| Force intent | retry sends `logout_and_delete` | second DELETE payload mismatch | same focused GREEN |
| Parent crash | relay gives backend `DEVNULL` instead of an owned pipe | refresh connection reset after premature EOF | lifecycle GREEN |
| Job publication | FIFO signals before `_finish_locked` | the single job GET timed out on unpublished state | lifecycle GREEN |
| Exactly-once shutdown | `RunningUIServer.close` invokes application shutdown twice | `shutdown_count` was `2`, expected `1` | lifecycle GREEN |
| Capability | native host writes the token to stderr | containment failed on stderr/diagnostics | containment GREEN |
| Surface lifecycle | packaged startup automatically refreshes the first account | real provider launch count was `1`, expected `0` | focused XCTest passed |
| PID reuse | stale-owner check compares numeric PIDs | replacement fixture was no longer running | focused XCTest passed |
| Redirect delegate | navigation-action delegate always returns `.allow` | external server received the redirected request | focused XCTest passed |

The authorized Swift seam followed a product RED: the new PID-reuse test first
failed to compile with `extra argument 'processFactory' in call`; the smallest
internal seam then made it GREEN. Initial attempts also exposed test-only
fixture defects (an abstract `Process` subclass and a provider exit-record race).
The final proxy forwards every used Foundation process contract, and the exit
fixture combines per-thread signal masking with a process-wide lock. The first
full Python run caught the latter race as one duplicate provider exit record;
after the fixture fix, the lifecycle suite was `6 passed` and the full suite was
rerun from scratch to `1319 passed`.

## Automated verification

Final-state commands and results:

| Command | Result |
| --- | --- |
| `.venv/bin/python3 -m pytest tests/integration/test_managed_accounts.py tests/integration/test_native_host_lifecycle.py tests/integration/test_web_workflows.py -q` | PASS — `16 passed in 9.19s` |
| `.venv/bin/python3 -m pytest tests/integration/test_native_host_lifecycle.py -q` after the full-load fixture finding | PASS — `6 passed in 3.81s` |
| `.venv/bin/python3 -m pytest` | PASS — `1319 passed in 98.92s` |
| `node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs` | PASS — `11` tests |
| `swift test` from `macos/DotSyncApp` | PASS — `107` XCTest tests in `27.063s` |
| `PYTHONPATH=lib python3 -m dotsync --help` | PASS |
| `PYTHONPATH=lib python3 bin/dotsync --help` | PASS |
| `PYTHONPATH=lib python3 -m dotsync ui --check` | PASS |
| `python3 -m compileall -q lib tests` | PASS |
| `bash scripts/build_macos_app.sh` | PASS — fresh universal `build/DotSync.app` assembled |
| `git diff --check` | PASS (run again immediately before commit) |

The Swift build still emits the pre-existing macOS 12 `WKProcessPool`
deprecation warnings. There are no compiler errors or test failures.

## Isolation and process evidence

- Every Python/Swift real-backend fixture uses an explicit temporary HOME; none
  resolves or targets the operator's real `.claude`, `.claude.json`, or
  `.codex` paths.
- Managed-account and native-host tests snapshot type/mode/timestamp/content of
  seeded default profiles. The real Swift native-surface fixture also snapshots
  mode, modification date, and contents.
- Public Claude operations remain policy-disabled before account/provider/job
  work.
- Provider process identity is observed from the provider itself, registered
  with one-shot kernel exit notifications while live, and asserted absent after
  shutdown. Lifecycle synchronization uses FIFO, `Condition`, `Barrier`, and
  `kqueue`; no sleep/stress loop decides a process or race result.
- Explicit Quit leaves the real fixture backend stopped and provider launch
  count at zero in the native-surface case; all six Python orders prove exact
  backend/provider teardown for active-provider cases.

## Visual/accessibility/native gates

The prior fixture browser review remains valid evidence for inspectable web
content: screenshots and accessibility snapshots of actual manager/popover and
both immutable HTML references, keyboard Tab/Enter navigation into Accounts,
named headings/controls/regions, long Korean copy, query erasure, and presence
of dark-mode/reduced-motion CSS. That review honestly observed that CSS
`documentElement` zoom at 200% produced horizontal overflow, which is not
equivalent to native macOS accessibility zoom and remains a concern rather than
a claimed product failure.

Fix round 1 added automated native WKWebView creation, navigation, dismantle,
cached-summary, provider-zero-work, redirect, and explicit backend Quit
evidence. It did not use the in-app browser because the relevant boundary was
the native XCTest WebKit surface; the browser control skill was read before any
visual action and caused no further action or pause in this round.

Still unexecuted and not claimed as passed:

- pixel-level comparison inside the unsigned app's native WKWebView;
- full keyboard traversal of AppKit menu-extra/window chrome;
- VoiceOver names and traversal order in macOS Accessibility Inspector;
- native reduced-motion and light/dark screenshots;
- native 200% accessibility zoom and long-Korean layout;
- closing a user-driven real manager window and observing menu-only presence;
- clicking the real menu-extra Quit item.

The tooling still cannot drive or inspect those native menu/VoiceOver surfaces
reliably. Automated tests cover the underlying manager-close/dismantle and Quit
lifecycle but are not presented as manual visual acceptance.

## Files changed

- `tests/integration/test_managed_accounts.py`
- `tests/integration/test_web_workflows.py`
- `tests/integration/test_native_host_lifecycle.py`
- `macos/DotSyncApp/Tests/DotSyncNativeTests/BackendProcessTests.swift`
- `macos/DotSyncApp/Tests/DotSyncNativeTests/WebSurfaceTests.swift`
- `macos/DotSyncApp/Sources/DotSyncNative/BackendProcess.swift` — authorized
  package-internal test seam only
- `.superpowers/sdd/2026-08-21-native-concept-a-completion/task-14-report.md`

## Self-review and concerns

- Final production diff is limited to the explicitly authorized internal
  factory seam. The public initializer, native handshake, local-origin policy,
  fixed `dotsyncNative` bridge receiver, MainActor summary owner, and all build/
  release contracts are unchanged.
- The real surface fixture invokes `dotsync.ui_app.run_native_ui` directly
  through an isolated wrapper so production native-host composition and framing
  are exercised without the CLI's fixed-error catch obscuring test diagnostics.
- Tests assert persistent files, HTTP payloads, real render copy, real process
  PIDs/counts, actual WK navigation, and shutdown records rather than mock
  return values.
- Generated SwiftPM and app-build output is removed before staging; no fixture
  process or temporary HOME is intentionally retained.
- Remaining concerns are only the external native visual/VoiceOver gates and
  existing `WKProcessPool` deprecation warnings. No observed behavior requires
  another production change.

Serena: skipped — this linked worktree has no `.serena/project.yml` opt-in
marker. The one production change was a small, explicitly ruled internal seam.

Graphify: skipped — this linked worktree has no `graphify-out/graph.json` opt-in
marker.
