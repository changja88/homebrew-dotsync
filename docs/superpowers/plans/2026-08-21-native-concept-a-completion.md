# Native Concept A DotSync App Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Finish the approved Concept A product as a native macOS menu-bar app with a full management window, isolated multi-account Codex subscription usage, existing DotSync backup/apply controls, and a release-gated Homebrew Cask.

**Architecture:** The reviewed Python 3.12 loopback API at commit 8e48256 remains the only account, usage, job, and sync domain implementation. A SwiftUI MenuBarExtra and Window supervise the Python child and embed packaged vanilla HTML/CSS/JavaScript in non-persistent WKWebView instances; Swift owns only native lifecycle, navigation, the fixed bridge, and a safe cached summary. The existing Formula remains the CLI/backend, while a signed and notarized dotsync-app Cask depends on that Formula.

**Tech Stack:** Python 3.12 standard library, HTML5, CSS, vanilla ECMAScript modules, Node built-in test runner for development only, Swift 5.9 language mode, SwiftUI, AppKit, WebKit, Foundation, XCTest, pytest, Homebrew Formula/Cask Ruby DSL, macOS codesign/notarytool/stapler.

**Spec:** docs/superpowers/specs/2026-08-21-unified-dotsync-app-design.md

## Plan relationship and execution baseline

- Tasks 1–8 from the original unified-app plan are complete and independently reviewed.
- This plan supersedes only the original Tasks 9–11.
- Begin from commit 5ac1a5d or a descendant containing the approved spec and both immutable Concept A HTML references.
- Before each task, read the approved spec and the task's listed producer interfaces.
- Every behavior change follows RED → GREEN → focused regression → fresh reviewer gate.
- Keep one implementation commit per task. Review fixes use separate commits.

## Global Constraints

- Python runtime remains standard-library-only and Python 3.12+.
- The native app targets macOS 13 or newer.
- The public version 1 build enables multiple Codex ChatGPT subscription accounts only.
- Claude account create, login, refresh, logout, and delete remain policy-disabled before any provider or job call.
- Account and usage operations never write to ~/.claude, ~/.claude.json, or ~/.codex.
- The native shell never reads or writes provider profile paths.
- Application state remains under ~/Library/Application Support/DotSync.
- Codex credentials remain in account-owned CODEX_HOME with cli_auth_credentials_store = "file".
- Provider raw output never crosses the presentation boundary before redaction.
- The app never calls provider-private usage HTTP endpoints.
- Opening or closing either UI surface never invokes a provider CLI.
- Explicit Refresh is the only subscription-usage refresh trigger.
- Existing backup/apply CLI behavior stays compatible; Apply still requires preview, digest revalidation, confirmation, and backup.
- The loopback server binds only to 127.0.0.1 on an ephemeral port and keeps the exact reviewed CSP.
- The capability token exists only in the parent-child handshake and process memory; it never enters argv, environment, disk, logs, persistent web data, or diagnostics.
- WKWebView uses WKWebsiteDataStore.nonPersistent() and rejects every navigation outside the exact launched origin.
- Native runtime dependencies are limited to SwiftUI, AppKit, WebKit, and Foundation. Do not add Electron, Node runtime, Tauri, PyObjC, rumps, or third-party Swift packages.
- The app has no Dock icon, login item, launch-at-login toggle, or automatic post-install launch in version 1.
- A public Cask is not created from an unsigned, ad-hoc-signed, unnotarized, unstapled, or unchecked archive.
- README English and Korean sections change together.
- local_dev remains unrelated and must not enter public docs, root make help, Formula/Cask artifacts, or these commits.

---

## Planned File Structure

Files marked existing already contain the reviewed Tasks 1–8 implementation.

~~~text
lib/dotsync/
├── cli.py                                  # existing; add public ui composition
├── macos_actions.py                        # fixed macOS picker/open actions
├── native_host.py                          # parent-pipe lifetime and launch handshake
├── ui_app.py                               # Python UI composition root
└── web/
    ├── api.py                              # existing; add safe menu summary
    ├── server.py                           # existing; browser/native lifetime modes
    └── static/
        ├── index.html                      # both approved surfaces
        ├── styles.css                      # Concept A tokens and responsive layout
        ├── state.mjs                       # pure state/reducer
        ├── api-client.mjs                  # capability and exact API methods
        ├── render.mjs                      # textContent-only DOM rendering
        └── app.mjs                         # event wiring and bounded job polling

macos/DotSyncApp/
├── Package.swift
├── README.md
├── Sources/
│   ├── DotSyncNative/
│   │   ├── BackendError.swift
│   │   ├── StrictJSON.swift
│   │   ├── BackendExecutableResolver.swift
│   │   ├── BackendProcess.swift
│   │   ├── LaunchHandshake.swift
│   │   ├── LocalOrigin.swift
│   │   ├── AppBridge.swift
│   │   ├── MenuSummary.swift
│   │   └── WebSurface.swift
│   └── DotSyncApp/
│       ├── AppCoordinator.swift
│       └── DotSyncApp.swift
└── Tests/
    └── DotSyncNativeTests/
        ├── StrictJSONTests.swift
        ├── LaunchHandshakeTests.swift
        ├── LocalOriginTests.swift
        ├── BackendExecutableResolverTests.swift
        ├── BackendProcessTests.swift
        ├── AppBridgeTests.swift
        ├── MenuSummaryTests.swift
        └── WebSurfaceTests.swift

packaging/
├── DotSync-Info.plist.in
└── dotsync-app.rb.in

scripts/
├── build_macos_app.sh
├── render_cask.py
└── release_macos_app.sh

tests/
├── test_cli_ui.py
├── test_macos_actions.py
├── test_macos_packaging.py
├── test_macos_release.py
├── web/
│   ├── test_native_host.py
│   ├── test_static_assets.py
│   └── js/
│       ├── api-client.test.mjs
│       └── state.test.mjs
└── integration/
    ├── test_managed_accounts.py
    ├── test_native_host_lifecycle.py
    └── test_web_workflows.py
~~~

The native Swift target imports no Python domain files. The Python UI composition root imports the already-reviewed services; provider homes and default-profile safety stay below that boundary.

### Task 9: Add the native-host transport contract and safe menu summary

**Files:**
- Create: lib/dotsync/native_host.py
- Modify: lib/dotsync/web/api.py
- Modify: lib/dotsync/web/server.py
- Modify: lib/dotsync/web/__init__.py
- Test: tests/web/test_api.py
- Test: tests/web/test_server.py
- Create: tests/web/test_native_host.py

**Interfaces:**
- Consumes: WebApplication, RunningUIServer, ApiController,
  UsageService.cached_usage(), and an in-memory safe sync-attention observation
  recorded by explicit Sync API work.
- Produces: GET /api/menu-summary, RunningUIServer.origin, native idle suppression, NativeHostHandshake, run_native_host(application, control, handshake, poll_interval) -> int.
- Exact menu DTO:

~~~json
{
  "usage": {"state": "fresh", "highest_percent": 72.0},
  "sync": {"state": "fresh", "attention_count": 1},
  "observed_at": "2026-08-21T09:00:00Z"
}
~~~

Each state is exactly fresh, stale, or unknown. highest_percent and attention_count are null when their state is unknown. The DTO contains no provider, account ID, label, identity, path, window label, token, error detail, or job data.

- Exact native handshake:

~~~json
{"schema_version":1,"origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
~~~

The encoded line is compact UTF-8 JSON followed by one LF and is at most 4096 bytes.

- [ ] **Step 1: Write failing menu-summary route tests**

Add deterministic clocks and cached snapshots to the existing API stack:

~~~python
def test_menu_summary_reads_cache_without_invoking_provider_or_exposing_identity(stack):
    account = stack.codex_account(label="Codex Personal")
    stack.cache_usage(account, used_percent=72.0, observed_at="2026-08-21T09:00:00Z")
    stack.set_sync_status(states=("dirty",))
    assert stack.client.request("GET", "/api/sync/status").status == 200
    stack.providers.fail_if_called()

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json() == {
        "usage": {"state": "fresh", "highest_percent": 72.0},
        "sync": {"state": "fresh", "attention_count": 1},
        "observed_at": "2026-08-21T09:00:00Z",
    }
    encoded = response.body.decode()
    assert account.id not in encoded
    assert account.label not in encoded
    assert str(stack.paths.root) not in encoded
    assert stack.providers.calls == []


def test_menu_summary_fails_closed_for_missing_or_invalid_cache(stack):
    stack.codex_account(label="No cache")
    stack.cache.load = lambda account_id: (_ for _ in ()).throw(ValueError("secret"))

    response = stack.client.request("GET", "/api/menu-summary")

    assert response.status == 200
    assert response.json()["usage"] == {
        "state": "unknown",
        "highest_percent": None,
    }
    assert "secret" not in response.body.decode()
~~~

Also cover:

- no managed Codex account → unknown/null;
- a snapshot older than 15 minutes → stale with its validated percentage;
- mixed fresh/missing accounts → stale;
- 0.0 and 100.0 remain valid;
- NaN, infinity, wrong DTO types, cache exceptions, and sync exceptions fail closed;
- sync attention counts every non-clean tracked app from the last explicit
  /api/sync/status result and never includes names;
- the read causes no provider process, job submission, config write, or state write;
- Claude fixture records, if present in a synthetic store, do not contribute.

- [ ] **Step 2: Run the route tests and verify RED**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/web/test_api.py -k menu_summary -v
~~~

Expected: FAIL with 404 because /api/menu-summary is not registered.

- [ ] **Step 3: Implement the exact safe summary**

Add the exact route and an injected UTC clock to ApiController. Keep aggregation in one private function:

~~~python
_SUMMARY_STATES = frozenset({"fresh", "stale", "unknown"})
_SUMMARY_FRESH_FOR_SECONDS = 15 * 60

_ROUTES = (
    _Route(("api", "bootstrap"), {"GET": "_bootstrap"}),
    _Route(("api", "menu-summary"), {"GET": "_menu_summary"}),
    # existing exact routes remain unchanged
)


def _menu_summary(self, request: ApiRequest, params: dict[str, str]) -> HttpResponse:
    _require_no_body(request)
    summary = _build_menu_summary(
        usage=self._usage,
        sync_observation=self._safe_sync_attention_observation(),
        now=self._clock(),
    )
    return json_response(200, summary)
~~~

_build_menu_summary() must:

1. call list_accounts() and consider only provider == "codex";
2. call cached_usage() only, never refresh();
3. validate every returned object as the concrete reviewed dataclass;
4. compute the highest validated percentage across known windows;
5. mark usage stale if any Codex account is missing, errored, or older than 900 seconds;
6. read only the controller's in-memory safe sync-attention observation;
7. never call SyncService.status() from /api/menu-summary;
8. update that observation only after an explicit /api/sync/status result has
   passed generation validation, and invalidate it on app/folder/execute
   transitions;
9. mark a sync observation stale after 900 seconds;
10. catch read/shape failures and return unknown/null without exception text;
11. emit observed_at as the newest validated usage or sync observation, or null.

Do not reuse the account DTO because that would expand the native summary boundary.
Store only (attention_count, observed_at) under the controller's existing sync lock;
do not store app names, paths, status details, or a SyncStatus object.

- [ ] **Step 4: Write failing native-lifetime and handshake tests**

~~~python
def test_native_host_emits_one_bounded_handshake_and_stops_on_control_eof(application):
    read_fd, write_fd = os.pipe()
    control = os.fdopen(read_fd, "rb", buffering=0)
    handshake = io.BytesIO()

    os.close(write_fd)
    result = run_native_host(
        application,
        control=control,
        handshake=handshake,
        poll_interval=0.01,
    )

    assert result == 0
    lines = handshake.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0], object_pairs_hook=_reject_duplicates)
    assert set(payload) == {"schema_version", "origin", "token"}
    assert payload["schema_version"] == 1
    assert payload["origin"].startswith("http://127.0.0.1:")
    assert len(base64.urlsafe_b64decode(payload["token"] + "=")) == 32


def test_native_mode_never_uses_browser_idle_shutdown(native_application, clock):
    clock.advance(IDLE_TIMEOUT_SECONDS * 2)
    assert native_application.should_idle_shutdown() is False
~~~

Also cover control bytes other than EOF returning a fixed protocol failure, handshake write failure closing the server/jobs, server death before EOF returning failure, exact LF framing, no token in repr/error/stderr, and browser mode retaining the reviewed 30-minute idle behavior.

- [ ] **Step 5: Run the native-host tests and verify RED**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/web/test_native_host.py tests/web/test_server.py -k "native or idle or launch_url" -v
~~~

Expected: FAIL because native_host.py and native lifetime selection do not exist.

- [ ] **Step 6: Implement the native-host runner**

Add an explicit idle_shutdown_enabled constructor parameter to WebApplication, defaulting to True so existing browser behavior does not change. RunningUIServer exposes:

~~~python
@property
def origin(self) -> str:
    host, port = self.server_address
    return f"http://{host}:{port}"


def launch_url_for(
    self,
    *,
    surface: Literal["popover", "manager"] = "manager",
    destination: Literal["overview", "accounts", "sync", "settings"] = "overview",
) -> str:
    query = urlencode(
        {
            "token": self._server.application.token,
            "surface": surface,
            "destination": destination,
        }
    )
    return f"{self.origin}/?{query}"
~~~

Preserve the existing launch_url property and make it delegate to
launch_url_for(surface="manager", destination="overview"). New callers use the
method so browser fallback and native surfaces select their destination explicitly.

native_host.py owns no service composition:

~~~python
@dataclass(frozen=True)
class NativeHostHandshake:
    schema_version: int
    origin: str
    token: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not _valid_native_origin(self.origin)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", self.token) is None
        ):
            raise NativeHostProtocolError("native handshake is invalid")

    def encode_line(self) -> bytes:
        data = json.dumps(
            asdict(self),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
        if len(data) > 4096:
            raise NativeHostProtocolError("native handshake exceeds 4096 bytes")
        return data


def _valid_native_origin(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port is not None
        and 1 <= port <= 65_535
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def run_native_host(
    application: WebApplication,
    *,
    control: BinaryIO,
    handshake: BinaryIO,
    poll_interval: float = 0.1,
) -> int:
    if application.idle_shutdown_enabled:
        raise NativeHostProtocolError("native host requires parent-owned lifetime")
    with run_ui_server(application, poll_interval=poll_interval) as server:
        line = NativeHostHandshake(
            schema_version=1,
            origin=server.origin,
            token=application.token,
        ).encode_line()
        handshake.write(line)
        handshake.flush()
        selector = selectors.DefaultSelector()
        try:
            selector.register(control, selectors.EVENT_READ)
            while True:
                if server.wait(timeout=poll_interval):
                    return 1
                for key, _ in selector.select(timeout=0):
                    value = key.fileobj.read(1)
                    return 0 if value == b"" else 2
        finally:
            selector.close()
~~~

Catch only at the CLI boundary in Task 10. This runner must never print the handshake, token, raw control byte, exception repr, or server origin to stderr.

- [ ] **Step 7: Run focused and complete web regressions**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/web/test_native_host.py tests/web/test_server.py tests/web/test_api.py -v
.venv/bin/python3 -m pytest tests/accounts tests/usage tests/test_jobs.py tests/test_sync_service.py -v
~~~

Expected: PASS with no changes to existing account, job, or sync DTOs.

- [ ] **Step 8: Commit Task 9**

~~~bash
git add lib/dotsync/native_host.py lib/dotsync/web tests/web/test_native_host.py tests/web/test_server.py tests/web/test_api.py
git commit -m "feat: add native DotSync host contract"
~~~

### Task 10: Build the production Concept A web surfaces and Python UI composition

**Files:**
- Create: lib/dotsync/web/static/index.html
- Create: lib/dotsync/web/static/styles.css
- Create: lib/dotsync/web/static/state.mjs
- Create: lib/dotsync/web/static/api-client.mjs
- Create: lib/dotsync/web/static/render.mjs
- Create: lib/dotsync/web/static/app.mjs
- Create: lib/dotsync/macos_actions.py
- Create: lib/dotsync/ui_app.py
- Modify: lib/dotsync/web/api.py
- Modify: lib/dotsync/web/server.py
- Modify: lib/dotsync/cli.py
- Modify: pyproject.toml
- Create: tests/web/test_static_assets.py
- Create: tests/web/js/state.test.mjs
- Create: tests/web/js/api-client.test.mjs
- Create: tests/test_macos_actions.py
- Create: tests/test_cli_ui.py
- Modify: tests/test_release_script.py

**Interfaces:**
- Consumes: all reviewed Task 8 routes, Task 9 menu summary/native runner, both immutable Concept A references.
- Produces: packaged static surfaces, build_web_application(), run_browser_ui(), check_ui_installation(), public dotsync ui, internal dotsync ui --native-host.
- JavaScript launch context:

~~~javascript
{
  token: "base64url capability",
  surface: "popover" | "manager",
  destination: "overview" | "accounts" | "sync" | "settings"
}
~~~

- Native bridge messages are exactly:

~~~json
{"action":"open_manager","destination":"overview"}
{"action":"refresh_summary"}
{"action":"quit_app"}
~~~

No other key or value is accepted by Swift in Task 12.

- [ ] **Step 1: Write failing packaged-asset and visual-contract tests**

~~~python
def test_packaged_ui_has_both_concept_a_surfaces(package_assets):
    html = package_assets["index.html"]
    assert 'data-surface="popover"' in html
    assert 'data-surface="manager"' in html
    for destination in ("overview", "accounts", "sync", "settings"):
        assert f'data-destination="{destination}"' in html


def test_assets_have_no_external_or_inline_runtime_code(package_assets):
    html = package_assets["index.html"]
    joined = "\n".join(package_assets.values())
    assert "https://" not in joined
    assert "http://" not in joined
    assert "<script>" not in html
    assert " style=" not in html
    assert "innerHTML" not in joined


def test_public_claude_controls_are_non_actionable(package_assets):
    joined = "\n".join(package_assets.values())
    assert 'data-provider", "claude"' in joined
    assert 'data-policy-state", "disabled"' in joined
    assert "add-claude" not in joined
    assert 'provider: "claude"' not in package_assets["api-client.mjs"]
~~~

Also assert semantic headings, accessible names for icon-only controls, dialog headings/cancel buttons, progress max=100, reduced-motion CSS, visible focus, a 320-pixel popover rule, no default-focused Apply/Delete button, local-only module imports, and exact fixed static route names.

- [ ] **Step 2: Run static tests and verify RED**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/web/test_static_assets.py -v
~~~

Expected: FAIL because lib/dotsync/web/static does not exist.

- [ ] **Step 3: Create semantic HTML and Concept A CSS**

index.html contains both surfaces so the same asset set serves browser fallback and native web views:

~~~html
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>DotSync</title>
    <link rel="stylesheet" href="/styles.css">
    <script type="module" src="/app.mjs"></script>
  </head>
  <body>
    <section data-surface="popover" hidden aria-label="DotSync menu">
      <header class="popover-header">
        <span class="brand-mark" aria-hidden="true">◆</span>
        <div>
          <h1>DotSync</h1>
          <p id="popover-updated">아직 조회하지 않음</p>
        </div>
      </header>
      <main id="popover-content" aria-live="polite"></main>
      <footer>
        <button type="button" data-native-action="open_manager" data-destination="overview">
          관리 창 열기
        </button>
        <button type="button" data-native-action="quit_app">종료</button>
      </footer>
    </section>

    <section data-surface="manager" hidden aria-label="DotSync management">
      <nav aria-label="주요 화면">
        <button type="button" data-destination="overview">Overview</button>
        <button type="button" data-destination="accounts">Accounts</button>
        <button type="button" data-destination="sync">Config Sync</button>
        <button type="button" data-destination="settings">Settings</button>
      </nav>
      <main id="manager-content" tabindex="-1" aria-live="polite"></main>
    </section>

    <dialog id="confirmation-dialog" aria-labelledby="confirmation-title">
      <h2 id="confirmation-title"></h2>
      <p id="confirmation-copy"></p>
      <form method="dialog">
        <button value="cancel" autofocus>취소</button>
        <button id="confirmation-submit" value="confirm"></button>
      </form>
    </dialog>
  </body>
</html>
~~~

styles.css defines one token set at :root for the approved pink-purple glass language. Preserve the original popover proportions and the approved management-window hierarchy. A management navigation rail is allowed, but a browser dashboard without the menu-bar popover is not Concept A. Use system fonts and CSS/local glyphs only.

~~~css
:root {
  color-scheme: light dark;
  --ink: #17151d;
  --muted: #74717e;
  --line: rgba(35, 29, 48, 0.11);
  --pink: #f780e2;
  --mid: #c069f0;
  --purple: #7571f9;
  --green: #2db47d;
  --amber: #e3a32b;
  --red: #de5b65;
  --blue: #5f82f2;
  --claude: #c87850;
  --codex: #171717;
  --surface: rgba(255, 255, 255, 0.72);
  --surface-strong: rgba(255, 255, 255, 0.88);
  --shadow: 0 26px 70px rgba(37, 27, 58, 0.14);
  --brand-gradient: linear-gradient(145deg, var(--pink), var(--mid) 46%, var(--purple));
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
    "SF Pro Text", system-ui, sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --ink: #f7f3fb;
    --muted: #b7b0c2;
    --line: rgba(255, 255, 255, 0.13);
    --surface: rgba(32, 27, 42, 0.76);
    --surface-strong: rgba(42, 35, 54, 0.9);
    --shadow: 0 26px 70px rgba(0, 0, 0, 0.34);
  }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto;
    transition-duration: 0.001ms;
    animation-duration: 0.001ms;
    animation-iteration-count: 1;
  }
}
~~~

- [ ] **Step 4: Write failing reducer and capability-bootstrap JavaScript tests**

~~~javascript
import test from "node:test";
import assert from "node:assert/strict";
import { initialState, reduce } from "../../../lib/dotsync/web/static/state.mjs";

test("one account job update cannot replace another account", () => {
  const start = {
    ...initialState,
    accounts: [
      { id: "a", label: "Personal" },
      { id: "b", label: "Work" },
    ],
    jobs: {},
  };
  const next = reduce(start, {
    type: "JOB_UPDATED",
    job: { id: "j", account_id: "a", state: "running" },
  });
  assert.deepEqual(next.accounts, start.accounts);
  assert.equal(next.jobs.j.account_id, "a");
});
~~~

api-client.test.mjs supplies fake location/history/fetch objects and proves:

- token, surface, and destination are parsed once;
- history.replaceState removes the complete query before any API call;
- missing/duplicate/extra launch parameters fail closed;
- every request sends X-DotSync-Token and cache: "no-store";
- request methods, paths, and JSON keys come from fixed exported methods;
- neither token nor provider payload enters thrown Error.message;
- 202 responses return only canonical job IDs to the poller.

- [ ] **Step 5: Run Node tests and verify RED**

Run:

~~~bash
node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs
~~~

Expected: FAIL with module-not-found for state.mjs and api-client.mjs.

- [ ] **Step 6: Implement pure state, exact API client, rendering, and event wiring**

state.mjs exports a frozen initial state and one reducer:

~~~javascript
export const initialState = Object.freeze({
  surface: "manager",
  destination: "overview",
  providers: {},
  accounts: [],
  sync: null,
  jobs: {},
  modal: null,
  error: null,
});

export function reduce(state, event) {
  switch (event.type) {
    case "BOOTSTRAP_LOADED":
      return { ...state, providers: event.providers, error: null };
    case "ACCOUNTS_LOADED":
      return { ...state, accounts: [...event.accounts], error: null };
    case "SYNC_LOADED":
      return { ...state, sync: event.sync, error: null };
    case "JOB_UPDATED":
      return {
        ...state,
        jobs: { ...state.jobs, [event.job.id]: event.job },
      };
    case "NAVIGATED":
      return { ...state, destination: event.destination, modal: null };
    case "ERROR_RAISED":
      return { ...state, error: event.error };
    default:
      return state;
  }
}
~~~

api-client.mjs exports only fixed methods:

~~~javascript
const SURFACES = new Set(["popover", "manager"]);
const DESTINATIONS = new Set(["overview", "accounts", "sync", "settings"]);

export function readLaunchContext(location, history) {
  const values = new URLSearchParams(location.search);
  const keys = [...values.keys()];
  const allowed = new Set(["token", "surface", "destination"]);
  if (keys.some((key) => !allowed.has(key))) throw new Error("invalid_launch");
  for (const key of allowed) {
    if (values.getAll(key).length !== 1) throw new Error("invalid_launch");
  }
  const token = values.get("token");
  const surface = values.get("surface");
  const destination = values.get("destination");
  if (!/^[A-Za-z0-9_-]{43}$/.test(token ?? "") ||
      !SURFACES.has(surface) ||
      !DESTINATIONS.has(destination)) {
    throw new Error("invalid_launch");
  }
  history.replaceState(null, "", location.pathname || "/");
  return Object.freeze({ token, surface, destination });
}


export function createApiClient(token, fetchImpl = globalThis.fetch) {
  const request = async (method, path, body) => {
    const response = await fetchImpl(path, {
      method,
      cache: "no-store",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        "X-DotSync-Token": token,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw safeApiError(response.status, payload);
    return payload;
  };
  return Object.freeze({
    bootstrap: () => request("GET", "/api/bootstrap"),
    menuSummary: () => request("GET", "/api/menu-summary"),
    accounts: () => request("GET", "/api/accounts"),
    createCodex: (label) => request("POST", "/api/accounts", { provider: "codex", label }),
    rename: (id, label) => request("PATCH", "/api/accounts/" + id, { label }),
    login: (id) => request("POST", "/api/accounts/" + id + "/login", { provider: "codex" }),
    refresh: (id) => request("POST", "/api/accounts/" + id + "/refresh", { provider: "codex" }),
    logout: (id) => request("POST", "/api/accounts/" + id + "/logout", { provider: "codex" }),
    remove: (id, action) => request("DELETE", "/api/accounts/" + id, { provider: "codex", action }),
    job: (id) => request("GET", "/api/jobs/" + id),
    syncStatus: () => request("GET", "/api/sync/status"),
    syncApps: (apps) => request("PATCH", "/api/sync/apps", { apps }),
    syncPreview: (direction, apps) => request("POST", "/api/sync/preview", { direction, apps }),
    syncExecute: (digest) => request("POST", "/api/sync/execute", { digest }),
    selectSyncFolder: () => request("POST", "/api/settings/sync-folder/select", {}),
    revealAppData: () => request("POST", "/api/settings/app-data/reveal", {}),
    heartbeat: () => request("POST", "/api/heartbeat", {}),
  });
}
~~~

render.mjs creates every node with createElement(), setAttribute(), and textContent. It never accepts HTML strings. Keep provider/error-to-copy mapping in one frozen table and use safe generic copy for unknown codes.

app.mjs must:

1. parse and erase launch context synchronously;
2. reveal exactly one surface;
3. load bootstrap, accounts, and menu-summary for the popover; load bootstrap,
   accounts, and Sync status for the manager; never call Sync status merely because
   the popover opens;
4. allow Add Codex only;
5. show Claude as policy-disabled with stable explanatory text;
6. poll only active jobs from 500 ms up to 2 seconds, stopping on terminal state, unload, or 30 seconds hidden;
7. refresh accounts explicitly and independently;
8. route Backup/Apply from the popover to a concrete management preview;
9. require confirmation before sync execute, logout, delete, and force-local delete;
10. send only the three exact native bridge messages, using refresh_summary after
    terminal account jobs and explicit Sync status/work;
11. treat absence of window.webkit as browser fallback, never as permission to execute a native action.

render.mjs and app.mjs implement every approved destination:

- Popover: last cached update, Claude policy state, compact Codex usage, safe sync
  attention, Backup/Apply preview entry points, management-window entry, Quit.
- Overview: cached account cards, five-hour/seven-day bars, reset times, stale/error
  text, explicit global/per-account Refresh, and safe configured/attention Sync state.
- Accounts: Add Codex, Rename, Reauthenticate, Log out, Delete, and force-local
  deletion after a second disclosure. Buttons disable only for the affected job.
- Config Sync: explicit status load, tracked-app selection, Backup/Apply preview,
  digest-bound execute, stronger Apply confirmation, and the safe message
  “selected sync folder/.backups” after Apply rather than an absolute path.
- Settings: folder picker, generic ~/Library/Application Support/DotSync location,
  Open in Finder, privacy boundary, cache age, and last observed CLI versions from
  validated cached snapshots. Settings never probes either CLI merely to discover a
  version.

Missing Sync configuration is an actionable Settings state, not a fatal dashboard
error. A failure in one account does not hide other accounts or Sync state.

- [ ] **Step 7: Write failing macOS-action, composition, CLI, and package tests**

~~~python
def test_ui_parser_exposes_browser_ui_but_hides_native_host():
    help_text = _build_parser().format_help()
    assert "ui" in help_text
    assert "--native-host" not in help_text


def test_ui_check_does_not_create_application_support(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["ui", "--check"]) == 0
    assert not (tmp_path / "Library/Application Support/DotSync").exists()


def test_native_host_uses_stdio_pipes_and_never_opens_browser(monkeypatch):
    calls = []
    monkeypatch.setattr("dotsync.cli.run_native_ui", lambda: calls.append("native") or 0)
    monkeypatch.setattr("webbrowser.open", lambda value: (_ for _ in ()).throw(AssertionError(value)))
    assert main(["ui", "--native-host"]) == 0
    assert calls == ["native"]
~~~

macos_actions tests assert fixed executable paths, shell=False, exact argv, cancellation mapping, HTTPS-only provider URL opening, and no HTTP-supplied path. tests/test_release_script.py asserts Formula packaging includes every static asset.

- [ ] **Step 8: Implement macOS actions and the explicit UI composition root**

macos_actions.py exposes only:

~~~python
def choose_sync_folder() -> Path | None:
    result = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            'POSIX path of (choose folder with prompt "DotSync 동기화 폴더 선택")',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
    )
    if result.returncode == 1 and "(-128)" in result.stderr:
        return None
    if result.returncode != 0:
        raise RuntimeError("The sync-folder picker could not be opened.")
    if "\0" in result.stdout or len(result.stdout.encode("utf-8")) > 32_768:
        raise RuntimeError("The sync-folder picker returned an invalid result.")
    return Path(result.stdout.rstrip("\n"))


def reveal_in_finder(path: Path) -> None:
    _run_fixed_open(path)


def open_provider_login_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("The provider login URL is invalid.")
    _run_fixed_open(value)
~~~

_run_fixed_open uses /usr/bin/open as argv zero, shell=False, a sanitized environment, bounded timeout, and fixed generic failures. It never logs the path or URL.

ui_app.py constructs AppPaths.for_home(Path.home()), AppStateStore, AccountStore, UsageCache, CodexUsageProvider, the fixture-only ClaudeUsageProvider, UsageService, JobRegistry through WebApplication, and a SyncService only when the saved sync directory can be safely loaded. Claude remains present only as an internal provider mapping; the API policy guard remains authoritative.

Expose these exact functions:

~~~python
def build_web_application(*, idle_shutdown_enabled: bool) -> WebApplication:
    paths = AppPaths.for_home(Path.home())
    state_store = AppStateStore(paths)
    accounts = AccountStore(paths)
    cache = UsageCache(paths)
    usage = UsageService(
        paths=paths,
        accounts=accounts,
        cache=cache,
        providers={
            "codex": CodexUsageProvider(paths),
            "claude": ClaudeUsageProvider(paths),
        },
    )
    return WebApplication(
        paths=paths,
        state_store=state_store,
        account_store=accounts,
        usage_service=usage,
        sync_service=_load_saved_sync_service(state_store),
        folder_picker=choose_sync_folder,
        sync_folder_initializer=_empty_sync_service,
        reveal_app_data=reveal_in_finder,
        open_provider_url=open_provider_login_url,
        idle_shutdown_enabled=idle_shutdown_enabled,
    )


def _load_saved_sync_service(state_store: AppStateStore) -> SyncService | None:
    try:
        return load_persisted_sync_service(
            state_store=state_store,
            factory=_empty_sync_service,
        )
    except (AppStateError, ConfigError, OSError, TypeError, ValueError):
        return None


def _empty_sync_service() -> SyncService:
    return SyncService(Config(dir=Path("/dev/null"), apps=[]))


def run_browser_ui(*, open_browser: bool) -> int:
    application = build_web_application(idle_shutdown_enabled=True)
    with run_ui_server(application) as server:
        if open_browser:
            webbrowser.open(server.launch_url_for(surface="manager", destination="overview"))
        server.wait()
    return 0


def run_native_ui() -> int:
    application = build_web_application(idle_shutdown_enabled=False)
    return run_native_host(
        application,
        control=sys.stdin.buffer,
        handshake=sys.stdout.buffer,
    )


def check_ui_installation() -> None:
    verify_packaged_assets(
        ("index.html", "styles.css", "state.mjs", "api-client.mjs", "render.mjs", "app.mjs")
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
~~~

Add this module-level API helper beside the existing no-follow folder helpers so
startup uses the same identity checks as folder selection:

~~~python
def load_persisted_sync_service(
    *,
    state_store: AppStateStore,
    factory: Callable[[], SyncService],
) -> SyncService | None:
    state = state_store.load()
    if state.sync_dir is None:
        return None
    canonical = _canonical_safe_directory(Path(state.sync_dir))
    directory_fd = _open_directory_no_follow(canonical)
    try:
        _verify_config_file_no_follow(directory_fd, allow_missing=False)
        initial_identity = _directory_identity(directory_fd)
        revalidated_fd = _open_revalidated_sync_directory(
            canonical,
            initial_identity,
        )
        os.close(revalidated_fd)
        candidate = _build_sync_directory_candidate(factory, canonical)
        revalidated_fd = _open_revalidated_sync_directory(
            canonical,
            initial_identity,
        )
        os.close(revalidated_fd)
        return candidate
    finally:
        os.close(directory_fd)
~~~

Add deterministic tests for a saved symlink, replaced directory identity, final
config symlink, missing config, malformed config, and a valid existing folder.
Failure returns no published SyncService and performs no initialization or write.

check_ui_installation() must not instantiate AppPaths, stores, services, or providers.

Add CLI syntax:

~~~text
dotsync ui                 # management browser fallback
dotsync ui --no-open       # browser server without opening a browser
dotsync ui --native-host   # hidden native child mode
dotsync ui --check         # hidden state-free package diagnostic
~~~

--native-host and --check are mutually exclusive and use argparse.SUPPRESS. KeyboardInterrupt in browser mode returns 130 after bounded shutdown. Native failures return a fixed nonzero code and one static stderr line without origin/token/raw exception.

Add package data:

~~~toml
[tool.setuptools.package-data]
"dotsync.web" = ["static/*.html", "static/*.css", "static/*.mjs"]
~~~

Update the server's fixed static route dictionary for exactly the six resource names. Never convert a request path to a filesystem path.

- [ ] **Step 9: Run frontend, CLI, and existing web tests**

Run:

~~~bash
node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs
.venv/bin/python3 -m pytest tests/web tests/test_macos_actions.py tests/test_cli_ui.py tests/test_release_script.py -v
PYTHONPATH=lib python3 -m dotsync ui --check
PYTHONPATH=lib python3 -m dotsync ui --help
~~~

Expected: all commands exit 0. ui --check creates no Application Support directory and performs no provider command.

- [ ] **Step 10: Perform the Concept A fixture visual review**

Serve the production assets against fixture API data and compare:

- 360 × 560 popover against docs/ui/concept-a/original-concepts.html;
- 1180 × 760 manager against docs/ui/concept-a/menu-bar-plus-management.html;
- Overview, Accounts, Config Sync, Settings;
- empty, stale, missing seven-day, error, active-job, Apply confirmation, and long Korean-label states;
- light/dark appearance, reduced motion, keyboard-only navigation, and 200% zoom.

Record screenshots under a temporary test-output directory only. Do not replace either approved HTML reference. Any material visual change requires user review before the task commit.

- [ ] **Step 11: Commit Task 10**

~~~bash
git add lib/dotsync/cli.py lib/dotsync/macos_actions.py lib/dotsync/ui_app.py lib/dotsync/web pyproject.toml tests/test_cli_ui.py tests/test_macos_actions.py tests/test_release_script.py tests/web
git commit -m "feat: add production Concept A surfaces"
~~~

### Task 11: Build the strict Swift backend-process boundary

**Files:**
- Create: macos/DotSyncApp/Package.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/BackendError.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/StrictJSON.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/LaunchHandshake.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/LocalOrigin.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/BackendExecutableResolver.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/BackendProcess.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/LaunchHandshakeTests.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/StrictJSONTests.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/LocalOriginTests.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/BackendExecutableResolverTests.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/BackendProcessTests.swift

**Interfaces:**
- Consumes: Task 9 exact native handshake and parent-control behavior.
- Produces: BackendError, StrictJSONDocument, LaunchHandshake.decode(_:),
  LocalOrigin, BackendExecutableResolver.resolve(testOverride:),
  BackendProcess.start(), BackendProcess.stop().

Package.swift initially exposes a DotSyncNative library and DotSyncNativeTests. It has no external package dependency and uses swift-tools-version 5.9 with macOS 13.

- [ ] **Step 1: Create the Swift package and write failing strict-handshake tests**

~~~swift
// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DotSyncApp",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "DotSyncNative", targets: ["DotSyncNative"]),
    ],
    dependencies: [],
    targets: [
        .target(name: "DotSyncNative"),
        .testTarget(
            name: "DotSyncNativeTests",
            dependencies: ["DotSyncNative"]
        ),
    ]
)
~~~

~~~swift
func testValidHandshakeDecodesExactOriginAndCapability() throws {
    let line = Data(
        #"{"schema_version":1,"origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#.utf8
    )
    let value = try LaunchHandshake.decode(line)
    XCTAssertEqual(value.schemaVersion, 1)
    XCTAssertEqual(value.origin.baseURL.absoluteString, "http://127.0.0.1:49152")
}

func testDuplicateOrExtraFieldsAreRejected() {
    assertProtocolError(
        #"{"schema_version":1,"origin":"http://127.0.0.1:49152","origin":"http://127.0.0.1:49153","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#
    )
    assertProtocolError(
        #"{"schema_version":1,"origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","path":"/tmp"}"#
    )
}
~~~

Also reject empty/oversized/multiple lines, invalid UTF-8, BOM, comments, trailing bytes, floats for schema_version, wrong version, escaped/alternate field names, user info, localhost/IPv6/0.0.0.0, HTTPS, missing/zero/out-of-range port, path other than empty or slash, query, fragment, and a token that does not decode to exactly 32 bytes.

- [ ] **Step 2: Run Swift tests and verify RED**

Run:

~~~bash
swift test --package-path macos/DotSyncApp --filter LaunchHandshakeTests
~~~

Expected: FAIL because LaunchHandshake and LocalOrigin do not exist.

- [ ] **Step 3: Implement the bounded flat JSON decoder and LocalOrigin**

Do not use JSONDecoder or JSONSerialization alone because neither proves duplicate-key
rejection. StrictJSONDocument is a focused RFC 8259 parser shared by the handshake
and menu-summary boundary. It:

- accepts UTF-8 object, array, string, finite number, true, false, and null;
- rejects duplicate object keys before materializing a dictionary;
- rejects invalid escapes, lone surrogates, non-finite numbers, comments, BOM,
  trailing tokens, and input beyond the caller's byte/depth limits;
- returns a StrictJSONValue enum with exactObject(keys:), exactArray(),
  exactString(), exactInteger(), exactDouble(), and exactNull() accessors;
- uses a maximum depth of 4 for the handshake and 8 for menu summary;
- never includes source bytes or parsed string values in thrown errors.

LaunchHandshake.decode enforces a byte count in the closed range 1 through 4096,
no LF/CR, one root object, integer schema version 1, origin/token strings, and
exactly three unique keys.

The public values are:

~~~swift
public struct LaunchHandshake: Equatable, Sendable {
    public let schemaVersion: Int
    public let origin: LocalOrigin

    public static func decode(_ line: Data) throws -> LaunchHandshake {
        let document = try StrictJSONDocument.decode(
            line,
            maximumBytes: 4096,
            maximumDepth: 4
        )
        let fields = try document.root.exactObject(
            keys: ["schema_version", "origin", "token"]
        )
        let version = try fields["schema_version"]?.exactInteger()
        let origin = try fields["origin"]?.exactString()
        let token = try fields["token"]?.exactString()
        guard version == 1, let origin, let token
        else { throw BackendError.backendProtocolError }
        return LaunchHandshake(
            schemaVersion: 1,
            origin: try LocalOrigin(origin: origin, token: token)
        )
    }
}

public struct LocalOrigin: Equatable, Sendable {
    public enum Surface: String, CaseIterable, Sendable { case popover, manager }
    public enum Destination: String, CaseIterable, Sendable {
        case overview, accounts, sync, settings
    }

    public let baseURL: URL
    private let token: String

    public init(origin: String, token: String) throws {
        let allowed = CharacterSet(
            charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        )
        guard token.utf8.count == 43,
              token.unicodeScalars.allSatisfy({ allowed.contains($0) })
        else { throw BackendError.backendProtocolError }
        let padded = token
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/") + "="
        guard Data(base64Encoded: padded)?.count == 32,
              let components = URLComponents(string: origin),
              components.scheme == "http",
              components.host == "127.0.0.1",
              let port = components.port,
              (1...65_535).contains(port),
              components.user == nil,
              components.password == nil,
              components.path.isEmpty || components.path == "/",
              components.query == nil,
              components.fragment == nil,
              let url = components.url
        else { throw BackendError.backendProtocolError }
        self.baseURL = url
        self.token = token
    }

    public func launchURL(
        surface: Surface,
        destination: Destination = .overview
    ) throws -> URL {
        guard var components = URLComponents(
            url: baseURL,
            resolvingAgainstBaseURL: false
        ) else { throw BackendError.backendProtocolError }
        components.path = "/"
        components.queryItems = [
            URLQueryItem(name: "token", value: token),
            URLQueryItem(name: "surface", value: surface.rawValue),
            URLQueryItem(name: "destination", value: destination.rawValue),
        ]
        guard let result = components.url
        else { throw BackendError.backendProtocolError }
        return result
    }

    public func accepts(_ url: URL) -> Bool {
        guard let candidate = URLComponents(
            url: url,
            resolvingAgainstBaseURL: false
        ),
        let expected = URLComponents(
            url: baseURL,
            resolvingAgainstBaseURL: false
        ),
        candidate.scheme == "http",
        candidate.host == "127.0.0.1",
        candidate.port == expected.port,
        candidate.user == nil,
        candidate.password == nil,
        candidate.fragment == nil,
        candidate.path.isEmpty || candidate.path == "/"
        else { return false }
        if candidate.queryItems == nil { return true }
        return candidate.queryItems == [
            URLQueryItem(name: "token", value: token),
            URLQueryItem(name: "surface", value: "popover"),
            URLQueryItem(name: "destination", value: "overview"),
        ] || Surface.allCases.contains { surface in
            Destination.allCases.contains { destination in
                candidate.queryItems == [
                    URLQueryItem(name: "token", value: token),
                    URLQueryItem(name: "surface", value: surface.rawValue),
                    URLQueryItem(name: "destination", value: destination.rawValue),
                ]
            }
        }
    }

    public func authorize(_ request: inout URLRequest) {
        request.setValue(token, forHTTPHeaderField: "X-DotSync-Token")
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    }
}
~~~

Surface and Destination conform to CaseIterable for the exact launch-query check.
launchURL constructs query items itself. It never accepts a caller-provided path,
query, URL, token, provider, or account ID. accepts() is used for top-level WebKit
navigation and permits only the fixed root with either no query or one exact
DotSync-generated launch query. Static modules and API fetches remain same-origin
subresources and are not promoted to top-level navigation.

- [ ] **Step 4: Write failing executable-resolution tests**

~~~swift
func testResolverUsesOnlyFixedHomebrewCandidates() throws {
    let fs = FakeExecutableFileSystem(
        entries: [
            "/opt/homebrew/bin/dotsync":
                .symlink("/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync"),
            "/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync":
                .regularExecutable,
        ]
    )
    let result = try BackendExecutableResolver(fileSystem: fs).resolve()
    XCTAssertEqual(result.path, "/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync")
    XCTAssertEqual(fs.lookups, ["/opt/homebrew/bin/dotsync"])
}

func testResolverNeverSearchesPathOrAcceptsOutsideCellarSymlink() {
    let fs = FakeExecutableFileSystem(
        entries: [
            "/opt/homebrew/bin/dotsync": .symlink("/tmp/evil"),
            "/tmp/evil": .regularExecutable,
        ]
    )
    XCTAssertThrowsError(
        try BackendExecutableResolver(fileSystem: fs).resolve()
    ) { error in
        XCTAssertEqual(error as? BackendError, .backendNotFound)
    }
    XCTAssertEqual(
        fs.lookups,
        ["/opt/homebrew/bin/dotsync", "/usr/local/bin/dotsync"]
    )
}
~~~

The only production candidates are /opt/homebrew/bin/dotsync and /usr/local/bin/dotsync. A resolved production target must be a regular executable below the matching Cellar/dotsync directory. testOverride is accepted only by tests/injected composition and is never read from argv, environment, defaults, or web content.

- [ ] **Step 5: Implement the resolver and normalized native errors**

~~~swift
public enum BackendError: String, Error, Equatable, Sendable {
    case backendNotFound = "backend_not_found"
    case backendStartFailed = "backend_start_failed"
    case backendProtocolError = "backend_protocol_error"
    case backendExited = "backend_exited"
}

public struct BackendExecutableResolver {
    public func resolve(testOverride: URL? = nil) throws -> URL {
        if let testOverride {
            return try validateInjectedTestExecutable(testOverride)
        }
        for candidate in fixedCandidates {
            if let value = try validateHomebrewCandidate(candidate) {
                return value
            }
        }
        throw BackendError.backendNotFound
    }
}
~~~

Diagnostics expose only BackendError.rawValue and an optional exit status. They never include searched paths, resolved path, argv, environment, handshake bytes, token, stderr, or underlying Error.localizedDescription.

- [ ] **Step 6: Write failing process ownership and timeout tests**

A generated temporary executable fixture understands ui --native-host and supports deterministic modes selected by its own test file content, not by production environment variables.

~~~swift
func testStartUsesStdoutHandshakeAndStdinLifetimePipe() throws {
    let fixture = try NativeHostFixture.valid()
    let backend = BackendProcess(
        resolver: BackendExecutableResolver(fileSystem: fixture.fileSystem),
        testOverride: fixture.url,
        handshakeTimeout: .seconds(1)
    )

    let session = try backend.start()
    XCTAssertEqual(session.origin.baseURL.host, "127.0.0.1")
    XCTAssertTrue(fixture.observedArguments(["ui", "--native-host"]))

    backend.stop()
    XCTAssertTrue(fixture.waitedForControlEOF)
    XCTAssertFalse(fixture.isRunning)
}
~~~

Also cover:

- no handshake for five seconds → backend_protocol_error and killed child;
- >4096 bytes before LF → protocol error;
- EOF before LF → protocol error;
- a second stdout byte after handshake → protocol error and shutdown;
- child exit before handshake → backend_exited;
- child exit after start updates state once;
- stop closes stdin, waits three seconds, sends SIGTERM, waits one second, then SIGKILL;
- stop is idempotent and concurrent stop/start is serialized;
- parent deinit/explicit shutdown leaves no fixture child;
- stderr is drained into a fixed-size discard buffer and never rendered;
- no token appears in Process.arguments, Process.environment, errors, or test logs.

- [ ] **Step 7: Implement BackendProcess**

~~~swift
public struct BackendSession: Equatable, Sendable {
    public let origin: LocalOrigin
}

public final class BackendProcess: @unchecked Sendable {
    private let lock = NSLock()
    private let resolver: BackendExecutableResolver
    private let testOverride: URL?
    private let handshakeTimeout: Duration
    private var process: Process?
    private var controlWriter: FileHandle?
    private var exitSignal: DispatchSemaphore?
    private let onUnexpectedExit: @Sendable (BackendError) -> Void

    public init(
        resolver: BackendExecutableResolver = .init(),
        testOverride: URL? = nil,
        handshakeTimeout: Duration = .seconds(5),
        onUnexpectedExit: @escaping @Sendable (BackendError) -> Void = { _ in }
    ) {
        self.resolver = resolver
        self.testOverride = testOverride
        self.handshakeTimeout = handshakeTimeout
        self.onUnexpectedExit = onUnexpectedExit
    }

    public func start() throws -> BackendSession {
        lock.lock()
        defer { lock.unlock() }
        guard process == nil else {
            throw BackendError.backendStartFailed
        }

        let executable = try resolver.resolve(testOverride: testOverride)
        let control = Pipe()
        let handshake = Pipe()
        let child = Process()
        let exited = DispatchSemaphore(value: 0)
        child.executableURL = executable
        child.arguments = ["ui", "--native-host"]
        child.environment = sanitizedEnvironment()
        child.currentDirectoryURL = URL(fileURLWithPath: "/", isDirectory: true)
        child.standardInput = control
        child.standardOutput = handshake
        child.standardError = FileHandle.nullDevice
        child.terminationHandler = { [weak self] terminated in
            exited.signal()
            self?.handleTermination(of: terminated)
        }

        do {
            try child.run()
            control.fileHandleForReading.closeFile()
            handshake.fileHandleForWriting.closeFile()
            let line = try readHandshakeLine(
                handshake.fileHandleForReading,
                maximumBytes: 4096,
                timeout: handshakeTimeout
            )
            let decoded = try LaunchHandshake.decode(line)
            guard child.isRunning else {
                throw BackendError.backendExited
            }
            process = child
            controlWriter = control.fileHandleForWriting
            exitSignal = exited
            monitorProtocolSilence(
                handshake.fileHandleForReading,
                ownedProcess: child
            )
            return BackendSession(origin: decoded.origin)
        } catch {
            control.fileHandleForWriting.closeFile()
            terminateOwnedProcess(child, exitSignal: exited)
            throw normalizeStartFailure(error, process: child)
        }
    }

    public func stop() {
        lock.lock()
        let ownedProcess = process
        let ownedControl = controlWriter
        let ownedExitSignal = exitSignal
        process = nil
        controlWriter = nil
        exitSignal = nil
        lock.unlock()

        guard let ownedProcess else { return }
        ownedControl?.closeFile()
        if waitForExit(ownedProcess, signal: ownedExitSignal, seconds: 3) {
            return
        }
        ownedProcess.terminate()
        if waitForExit(ownedProcess, signal: ownedExitSignal, seconds: 1) {
            return
        }
        kill(ownedProcess.processIdentifier, SIGKILL)
    }
}
~~~

The private helper contracts are fixed: readHandshakeLine returns bytes before one
LF and rejects EOF/timeout/overflow; monitorProtocolSilence treats any later stdout
byte as backend_protocol_error and terminates only the owned Process; waitForExit
uses the termination semaphore and monotonic deadlines without blocking the main
actor; normalizeStartFailure returns only one of the four BackendError cases.
handleTermination and the protocol monitor detach only the exact currently owned
Process before calling onUnexpectedExit once. stop() detaches first, so expected
shutdown never calls that callback. Raw stderr is sent directly to
FileHandle.nullDevice and never retained. Every pipe end is closed exactly once on
every path.

Production Process.environment is a documented allowlist containing HOME, TMPDIR, locale, and macOS runtime variables required by the Formula launcher. It explicitly removes provider API keys, provider auth tokens, provider home overrides, custom base URLs, PYTHONPATH, and shell function variables. The Formula launcher supplies its own fixed PYTHONPATH.

- [ ] **Step 8: Run Swift boundary tests**

Run:

~~~bash
swift test --package-path macos/DotSyncApp
~~~

Expected: PASS with fixture processes gone after the test process exits.

- [ ] **Step 9: Commit Task 11**

~~~bash
git add macos/DotSyncApp
git commit -m "feat: add native backend process boundary"
~~~

### Task 12: Implement MenuBarExtra, management Window, WebKit policy, and menu summary

**Files:**
- Modify: macos/DotSyncApp/Package.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/AppBridge.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/MenuSummary.swift
- Create: macos/DotSyncApp/Sources/DotSyncNative/WebSurface.swift
- Create: macos/DotSyncApp/Sources/DotSyncApp/AppCoordinator.swift
- Create: macos/DotSyncApp/Sources/DotSyncApp/DotSyncApp.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/AppBridgeTests.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/MenuSummaryTests.swift
- Create: macos/DotSyncApp/Tests/DotSyncNativeTests/WebSurfaceTests.swift

**Interfaces:**
- Consumes: LocalOrigin, BackendProcess, GET /api/menu-summary, Task 10 bridge messages.
- Produces: DotSync executable product, AppCoordinator, AppBridge, MenuSummaryClient, WebSurface, MenuBarExtra and one management Window.

- [ ] **Step 1: Write failing bridge and navigation-policy tests**

~~~swift
func testBridgeAcceptsOnlyFixedOpenManagerMessage() throws {
    let command = try AppBridge.decode([
        "action": "open_manager",
        "destination": "sync",
    ])
    XCTAssertEqual(command, .openManager(.sync))
}

func testBridgeRejectsPathsURLsProvidersAndAccountIDs() {
    for body in [
        ["action": "open_manager", "destination": "sync", "path": "/tmp"],
        ["action": "refresh", "account_id": UUID().uuidString],
        ["action": "open_url", "url": "https://example.test"],
        ["action": "open_manager", "destination": "claude"],
    ] {
        XCTAssertThrowsError(try AppBridge.decode(body))
    }
}
~~~

WebSurface policy tests assert:

- the exact generated root launch URL and the query-free exact root are allowed;
- asset/API paths are never allowed as top-level navigation, while normal
  same-origin subresource loads remain available to WebKit;
- localhost alias, another loopback port, IPv6, HTTPS, file, data, javascript, blob, custom schemes, user info, and external hosts rejected;
- target=_blank and window.open create no new WKWebView;
- downloads and authentication challenges outside the origin are cancelled;
- back/forward history cannot leave the origin;
- non-persistent websiteDataStore is used;
- bridge registration is exactly dotsyncNative.

- [ ] **Step 2: Run bridge/WebKit tests and verify RED**

Run:

~~~bash
swift test --package-path macos/DotSyncApp --filter AppBridgeTests
swift test --package-path macos/DotSyncApp --filter WebSurfaceTests
~~~

Expected: FAIL because AppBridge and WebSurface do not exist.

- [ ] **Step 3: Implement the fixed bridge and WebSurface**

~~~swift
public enum NativeCommand: Equatable, Sendable {
    case openManager(LocalOrigin.Destination)
    case refreshSummary
    case quitApp
}

public enum AppBridge {
    public static func decode(_ body: Any) throws -> NativeCommand {
        guard let object = body as? [String: Any],
              let action = object["action"] as? String
        else { throw BackendError.backendProtocolError }
        switch action {
        case "open_manager":
            guard Set(object.keys) == Set(["action", "destination"]),
                  let raw = object["destination"] as? String,
                  let destination = LocalOrigin.Destination(rawValue: raw)
            else { throw BackendError.backendProtocolError }
            return .openManager(destination)
        case "quit_app":
            guard Set(object.keys) == Set(["action"])
            else { throw BackendError.backendProtocolError }
            return .quitApp
        case "refresh_summary":
            guard Set(object.keys) == Set(["action"])
            else { throw BackendError.backendProtocolError }
            return .refreshSummary
        default:
            throw BackendError.backendProtocolError
        }
    }
}
~~~

WebSurface is an NSViewRepresentable around WKWebView. Its configuration is constructed in one factory:

~~~swift
@MainActor
func makeConfiguration(
    processPool: WKProcessPool,
    bridge: WKScriptMessageHandler
) -> WKWebViewConfiguration {
    let configuration = WKWebViewConfiguration()
    configuration.websiteDataStore = .nonPersistent()
    configuration.processPool = processPool
    configuration.defaultWebpagePreferences.allowsContentJavaScript = true
    configuration.userContentController.add(bridge, name: "dotsyncNative")
    return configuration
}
~~~

The navigation delegate calls LocalOrigin.accepts() for every provisional, response, redirect, and new-window decision. External URLs are cancelled, not passed to NSWorkspace. Official provider verification URLs continue to open through the reviewed Python callback.

- [ ] **Step 4: Write failing menu-summary model/client tests**

~~~swift
func testSummaryDecodesExactSafeDTO() throws {
    let data = Data(
        #"{"usage":{"state":"stale","highest_percent":72.0},"sync":{"state":"fresh","attention_count":1},"observed_at":"2026-08-21T09:00:00Z"}"#.utf8
    )
    let summary = try MenuSummary.decode(data)
    XCTAssertEqual(summary.usage.state, .stale)
    XCTAssertEqual(summary.usage.highestPercent, 72.0)
    XCTAssertEqual(summary.sync.attentionCount, 1)
}

func testUnknownOrMalformedSummaryNeverDisplaysZero() {
    let model = MenuSummaryModel()
    model.acceptMalformedResponse()
    XCTAssertEqual(model.menuTitle, "DotSync · —")
}
~~~

Also reject extra keys, duplicate keys, invalid state/value pairing, negative/over-100/non-finite percentages, negative/huge counts, non-RFC3339 observation, paths, identities, labels, and account IDs. Verify requests target only /api/menu-summary, include the capability header, use an ephemeral URLSession, and never call an account refresh route.

- [ ] **Step 5: Implement safe summary decoding and cached polling**

~~~swift
public struct MenuSummary: Equatable, Sendable {
    public enum State: String, Sendable { case fresh, stale, unknown }
    public struct Usage: Equatable, Sendable {
        public let state: State
        public let highestPercent: Double?
    }
    public struct Sync: Equatable, Sendable {
        public let state: State
        public let attentionCount: Int?
    }
    public let usage: Usage
    public let sync: Sync
    public let observedAt: Date?

    public static let unknown = MenuSummary(
        usage: Usage(state: .unknown, highestPercent: nil),
        sync: Sync(state: .unknown, attentionCount: nil),
        observedAt: nil
    )

    public static func decode(_ data: Data) throws -> MenuSummary {
        let document = try StrictJSONDocument.decode(
            data,
            maximumBytes: 16_384,
            maximumDepth: 8
        )
        let root = try document.root.exactObject(
            keys: ["usage", "sync", "observed_at"]
        )
        let usageObject = try root["usage"]?.exactObject(
            keys: ["state", "highest_percent"]
        )
        let syncObject = try root["sync"]?.exactObject(
            keys: ["state", "attention_count"]
        )
        guard let usageObject, let syncObject,
              let usageStateRaw = try usageObject["state"]?.exactString(),
              let usageState = State(rawValue: usageStateRaw),
              let syncStateRaw = try syncObject["state"]?.exactString(),
              let syncState = State(rawValue: syncStateRaw)
        else { throw BackendError.backendProtocolError }

        let percent = try optionalPercentage(
            usageObject["highest_percent"],
            state: usageState
        )
        let count = try optionalAttentionCount(
            syncObject["attention_count"],
            state: syncState,
            maximum: 10_000
        )
        let observedAt = try optionalRFC3339(root["observed_at"])
        if (usageState != .unknown || syncState != .unknown) && observedAt == nil {
            throw BackendError.backendProtocolError
        }
        return MenuSummary(
            usage: Usage(state: usageState, highestPercent: percent),
            sync: Sync(state: syncState, attentionCount: count),
            observedAt: observedAt
        )
    }
}

@MainActor
public final class MenuSummaryModel: ObservableObject {
    @Published public private(set) var summary = MenuSummary.unknown

    public var menuTitle: String {
        guard let value = summary.usage.highestPercent else { return "DotSync · —" }
        let suffix = summary.usage.state == .stale ? " stale" : ""
        return "DotSync · \(Int(value.rounded()))%\(suffix)"
    }
}

public struct MenuSummaryClient: Sendable {
    private let origin: LocalOrigin
    private let session: URLSession

    public init(origin: LocalOrigin) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        self.origin = origin
        self.session = URLSession(configuration: configuration)
    }

    public func fetch() async throws -> MenuSummary {
        let url = origin.baseURL.appendingPathComponent("api/menu-summary")
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        origin.authorize(&request)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse,
              http.statusCode == 200,
              data.count <= 16_384
        else { throw BackendError.backendProtocolError }
        return try MenuSummary.decode(data)
    }
}
~~~

optionalPercentage requires null for unknown and a finite 0...100 number for
fresh/stale. optionalAttentionCount requires null for unknown and an integer in
0...10,000 for fresh/stale. optionalRFC3339 accepts null or one canonical UTC/offset
RFC 3339 string and rejects calendar-normalized or trailing input. All three helpers
throw backend_protocol_error without retaining the rejected value.

MenuSummaryClient polls cached summary no more often than once per 60 seconds while the app is active. It also reloads after explicit UI job completion or sync action. Polling does not call any provider route and pauses when the app becomes inactive.

- [ ] **Step 6: Write failing coordinator lifecycle tests**

~~~swift
@MainActor
func testCoordinatorStartsOneBackendForBothSurfaces() async throws {
    let backend = FakeBackendProcess()
    let coordinator = AppCoordinator(backend: backend)

    await coordinator.start()
    await coordinator.start()
    coordinator.openManager(.accounts)

    XCTAssertEqual(backend.startCount, 1)
    XCTAssertEqual(coordinator.managerDestination, .accounts)
}

@MainActor
func testQuitStopsBackendBeforeTerminatingApplication() async {
    let events = EventRecorder()
    let coordinator = AppCoordinator(
        backend: FakeBackendProcess(events: events),
        terminator: { events.append("terminate") }
    )
    await coordinator.quit()
    XCTAssertEqual(events.values, ["backend-stop", "terminate"])
}
~~~

Also cover backend failure → fixed recovery panel, Retry starts only after old ownership closes, unexpected exit does not loop-restart, closing manager leaves backend/menu extra running, and opening either surface causes no provider API call.

- [ ] **Step 7: Implement AppCoordinator and SwiftUI scenes**

Package.swift adds:

~~~swift
.executable(name: "DotSync", targets: ["DotSyncApp"])

.executableTarget(
    name: "DotSyncApp",
    dependencies: ["DotSyncNative"]
)
~~~

DotSyncApp.swift uses exactly:

~~~swift
@main
struct DotSyncMenuApp: App {
    @StateObject private var coordinator = AppCoordinator.production()

    var body: some Scene {
        MenuBarExtra {
            PopoverRoot(coordinator: coordinator)
                .frame(width: 360, height: 560)
        } label: {
            Label(
                coordinator.summary.menuTitle,
                systemImage: "arrow.triangle.2.circlepath.circle.fill"
            )
        }
        .menuBarExtraStyle(.window)

        Window("DotSync", id: "manager") {
            ManagerRoot(coordinator: coordinator)
                .frame(minWidth: 920, minHeight: 620)
        }
        .defaultSize(width: 1180, height: 760)
    }
}
~~~

AppCoordinator:

- starts BackendProcess once from an idempotent Task;
- owns one shared WKProcessPool and LocalOrigin;
- creates popover and manager launch URLs from fixed enums;
- converts AppBridge.openManager into openWindow(id: "manager") plus destination;
- converts refreshSummary into one cached /api/menu-summary read and never a provider
  request;
- converts quitApp into awaited backend stop followed by NSApplication.terminate;
- shows only Retry, Open installation help, and Quit for normalized backend errors;
- never exposes the token or raw child data to SwiftUI strings;
- refreshes MenuSummaryModel only from the cached summary endpoint.

Closing Window does not terminate the app. Info.plist in Task 13 supplies LSUIElement so no Dock scene appears.

- [ ] **Step 8: Run all Swift tests and a fixture-backed local scene smoke**

Run:

~~~bash
swift test --package-path macos/DotSyncApp
~~~

Then use an XCTest NSHostingView harness with an injected FakeBackendProcess and a
temporary loopback fixture server. No production resolver or provider executable is
invoked. Verify:

- Concept A popover;
- Open management window reuses the same backend;
- each destination opens;
- Quit removes both backend and fixture child.

The smoke uses fixture data only and writes no provider/default profile.

- [ ] **Step 9: Commit Task 12**

~~~bash
git add macos/DotSyncApp
git commit -m "feat: add DotSync menu bar application"
~~~

### Task 13: Assemble a local macOS app bundle and update package/docs contracts

**Files:**
- Create: packaging/DotSync-Info.plist.in
- Create: scripts/build_macos_app.sh
- Create: macos/DotSyncApp/README.md
- Modify: Formula/dotsync.rb
- Modify: Makefile
- Modify: README.md
- Modify: AGENTS.md
- Create: tests/test_macos_packaging.py
- Modify: tests/test_release_script.py

**Interfaces:**
- Consumes: DotSyncApp Swift executable, Formula-installed dotsync backend, packaged web assets.
- Produces: unsigned local universal DotSync.app for development, state-free Formula UI check, English/Korean user docs, durable repository rules.
- Does not produce a public Cask or public archive.

- [ ] **Step 1: Write failing plist/build/package contract tests**

~~~python
def test_info_plist_template_defines_menu_bar_only_macos13_app():
    plist = plistlib.loads(render_info_plist(version="0.3.0"))
    assert plist["CFBundleIdentifier"] == "dev.changja88.dotsync"
    assert plist["CFBundleExecutable"] == "DotSync"
    assert plist["LSMinimumSystemVersion"] == "13.0"
    assert plist["LSUIElement"] is True


def test_public_cask_is_not_created_by_local_build():
    script = Path("scripts/build_macos_app.sh").read_text()
    assert "Casks/dotsync-app.rb" not in script
    assert "notarytool" not in script
    assert "codesign --sign -" not in script
~~~

Also assert the build:

- derives an exact semantic version from pyproject.toml;
- builds arm64-apple-macosx13.0 and x86_64-apple-macosx13.0 separately;
- combines only those executables with lipo;
- creates DotSync.app/Contents/MacOS/DotSync and Contents/Info.plist;
- leaves no token, provider home, or absolute developer checkout path in the bundle;
- marks no unsigned/local archive as Cask-ready;
- Formula test runs dotsync ui --check;
- root make help contains no local_dev target.

- [ ] **Step 2: Run packaging tests and verify RED**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/test_macos_packaging.py tests/test_release_script.py -v
~~~

Expected: FAIL because the plist template and build script do not exist.

- [ ] **Step 3: Create the exact Info.plist template**

packaging/DotSync-Info.plist.in contains:

~~~xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>ko</string>
  <key>CFBundleDisplayName</key><string>DotSync</string>
  <key>CFBundleExecutable</key><string>DotSync</string>
  <key>CFBundleIdentifier</key><string>dev.changja88.dotsync</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>DotSync</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>__DOTSYNC_VERSION__</string>
  <key>CFBundleVersion</key><string>__DOTSYNC_BUILD__</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
~~~

The build script replaces both sentinels in the copied plist and fails if either remains.

- [ ] **Step 4: Implement deterministic unsigned local bundle assembly**

scripts/build_macos_app.sh:

1. requires macOS, xcrun, swift, lipo, plutil, and a clean output directory under build/;
2. parses the exact current version from pyproject.toml;
3. obtains the macOS SDK with xcrun --sdk macosx --show-sdk-path;
4. runs SwiftPM once per exact target triple with independent scratch paths;
5. obtains each executable through swift build --show-bin-path using the same arguments;
6. verifies each file is a regular executable;
7. creates build/DotSync.app with mode 0755 directories and executable;
8. runs lipo -create and lipo -verify_arch arm64 x86_64;
9. renders and validates Info.plist with plutil -lint;
10. scans the bundle for the checkout path, capability field values, and provider-home strings;
11. prints only the final local app path.

Core commands:

~~~bash
swift build --package-path macos/DotSyncApp --configuration release \
  --triple arm64-apple-macosx13.0 --sdk "$SDK" \
  --scratch-path build/swift-arm64
swift build --package-path macos/DotSyncApp --configuration release \
  --triple x86_64-apple-macosx13.0 --sdk "$SDK" \
  --scratch-path build/swift-x86_64
lipo -create "$ARM_BINARY" "$X86_BINARY" \
  -output build/DotSync.app/Contents/MacOS/DotSync
lipo -verify_arch arm64 x86_64 build/DotSync.app/Contents/MacOS/DotSync
~~~

The local build is intentionally unsigned and is never uploaded or used to render a Cask.

- [ ] **Step 5: Update Formula and developer targets**

Formula/dotsync.rb keeps its current Python installation model and adds:

~~~ruby
test do
  assert_match "dotsync #{version}", shell_output("#{bin}/dotsync --version")
  system bin/"dotsync", "ui", "--check"
end
~~~

Makefile adds:

~~~make
.PHONY: test test-ui test-native build-app release

test-ui:
	@$(PYTHON) -m pytest tests/web tests/test_cli_ui.py tests/test_macos_actions.py
	@node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs

test-native:
	@swift test --package-path macos/DotSyncApp

build-app:
	@bash scripts/build_macos_app.sh
~~~

Root make help lists these public DotSync targets only. It does not mention local_dev.

- [ ] **Step 6: Update durable AGENTS.md boundaries**

Keep the sync-folder rule for the original CLI, and add exact UI exceptions:

- account metadata/cache may live only under ~/Library/Application Support/DotSync;
- Codex auth file may live only under account-owned CODEX_HOME there;
- future Claude scoped Keychain behavior remains internal and public Claude actions are policy-disabled;
- native Swift code must not inspect provider homes;
- UI operations never write default provider profiles;
- local unsigned app builds are development artifacts only;
- public Cask changes require a real signed/notarized archive and checksum;
- local_dev stays excluded from public packaging/docs.

- [ ] **Step 7: Update README English and Korean in parity**

Both language sections document:

- one final Cask install command and the still-supported Formula-only command;
- current release status if the signed Cask has not yet been published;
- menu-bar popover and management window;
- two or more isolated Codex accounts;
- explicit login/refresh behavior and official Codex app-server use;
- Claude policy-disabled state and no private fallback;
- Application Support data location;
- no writes to ~/.claude, ~/.claude.json, or ~/.codex for account/usage operations;
- existing config-sync exceptions, preview, backup, and Apply warning;
- explicit app Quit, no launch-at-login, and no automatic provider refresh;
- ui --check troubleshooting;
- app requires macOS 13+ and installed Formula dependency.

Do not advertise a Cask as available until Task 15's real artifact exists.

- [ ] **Step 8: Run local package/build/document verification**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/test_macos_packaging.py tests/test_release_script.py tests/test_cli_ui.py -v
PYTHONPATH=lib python3 -m dotsync ui --check
swift test --package-path macos/DotSyncApp
bash scripts/build_macos_app.sh
plutil -lint build/DotSync.app/Contents/Info.plist
lipo -verify_arch arm64 x86_64 build/DotSync.app/Contents/MacOS/DotSync
git diff --check
~~~

Expected: PASS. This verifies a local unsigned development app only and makes no public-release claim.

- [ ] **Step 9: Commit Task 13**

~~~bash
git add packaging/DotSync-Info.plist.in scripts/build_macos_app.sh macos/DotSyncApp/README.md Formula/dotsync.rb Makefile README.md AGENTS.md tests/test_macos_packaging.py tests/test_release_script.py
git commit -m "build: assemble local DotSync macOS app"
~~~

### Task 14: Add adversarial end-to-end isolation and lifecycle coverage

**Files:**
- Create: tests/integration/test_managed_accounts.py
- Create: tests/integration/test_web_workflows.py
- Create: tests/integration/test_native_host_lifecycle.py
- Modify: macos/DotSyncApp/Tests/DotSyncNativeTests/BackendProcessTests.swift
- Modify: macos/DotSyncApp/Tests/DotSyncNativeTests/WebSurfaceTests.swift

**Interfaces:**
- Consumes: complete Python UI, static assets, native host, Swift shell, fixture provider CLIs.
- Produces: automated proof that multiple accounts, sync, loopback API, WebKit, and process shutdown preserve default profiles and cross-account isolation.

- [ ] **Step 1: Write a full fixture-backed two-Codex-account workflow**

~~~python
def test_two_codex_accounts_are_independent_and_defaults_are_unchanged(
    app, fake_codex_cli, fake_home
):
    before = snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"])

    personal = app.create_and_login_codex("Personal")
    work = app.create_and_login_codex("Work")
    personal_usage = app.refresh(personal.id)
    work_usage = app.refresh(work.id)

    assert personal_usage.snapshot is not None
    assert work_usage.snapshot is not None
    assert personal_usage.snapshot.account_id == personal.id
    assert work_usage.snapshot.account_id == work.id
    assert personal_usage.snapshot.account_id != work_usage.snapshot.account_id
    assert fake_codex_cli.homes == {
        app.paths.account_home("codex", personal.id),
        app.paths.account_home("codex", work.id),
    }
    assert snapshot_tree(fake_home, [".claude", ".claude.json", ".codex"]) == before
~~~

Cover create/login/refresh/rename/reauth/logout/delete for both accounts, one failed account while the other remains usable, stale cache preservation, force-local deletion disclosure, and exact CODEX_HOME/file-credential configuration.

- [ ] **Step 2: Write real-loopback web workflow and race tests**

Drive the actual server over 127.0.0.1 with its capability:

- browser bootstrap strips token in the JS test boundary;
- public Claude operations return policy_disabled before provider/job work;
- simultaneous Codex refreshes remain account-correlated;
- duplicate refresh, delete-during-refresh, cancellation, and shutdown reconcile safely;
- stale Apply digest, folder transition, and app-selection races retain Task 8 guarantees;
- hostile Host, missing/duplicate token, oversize body, unsupported methods, query confusion, and filesystem-shaped labels fail closed;
- menu summary never refreshes providers and never leaks account/sync identifiers;
- one provider exception containing path/OAuth/loopback sentinels never reaches HTTP or native summary;
- opening/closing popover/manager fixture states never creates a provider process.

- [ ] **Step 3: Write parent/child/grandchild lifecycle tests**

tests/integration/test_native_host_lifecycle.py launches a fixture provider grandchild under the real Python native host and tests these orders:

1. native control EOF → backend closes jobs → provider exits;
2. backend SIGTERM → provider bounded cleanup;
3. provider crash → job safe failure → backend remains;
4. native parent crash simulation → OS closes pipe → backend/provider exit;
5. handshake reader disappears → backend closes without orphan;
6. concurrent Quit and job completion → one shutdown.

Use Event/Condition/pipe barriers, not sleeps or stress-loop timing. Assert process disappearance by PID plus waitpid/Process termination, not log text.

- [ ] **Step 4: Extend Swift adversarial tests**

Add fixture cases for:

- valid line followed by secret bytes;
- handshake timeout racing child exit;
- stop racing terminationHandler;
- PID reuse-safe process ownership;
- repeated Retry after protocol failure;
- WebKit redirect from exact origin to external origin;
- script message dictionaries with nested/bridged Objective-C values;
- malformed menu summary retaining unknown, never 0%;
- summary polling during Quit stops before backend ownership is released.

- [ ] **Step 5: Run every automated verification layer**

Run:

~~~bash
.venv/bin/python3 -m pytest
node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs
swift test --package-path macos/DotSyncApp
PYTHONPATH=lib python3 -m dotsync --help
PYTHONPATH=lib python3 bin/dotsync --help
PYTHONPATH=lib python3 -m dotsync ui --check
python3 -m compileall -q lib tests
bash scripts/build_macos_app.sh
git diff --check
~~~

Expected: every command exits 0. Record exact test counts in the task report.

- [ ] **Step 6: Perform fixture visual/accessibility/native lifecycle review**

With the local unsigned app and fixture backend:

- compare popover/manager against both immutable approved HTML files;
- navigate every action by keyboard;
- inspect VoiceOver names/order;
- verify reduced motion and light/dark appearance;
- verify long Korean text and 200% zoom;
- verify manager close returns to menu-only presence;
- verify explicit Quit removes backend/provider fixture processes;
- verify no ~/.claude, ~/.claude.json, or ~/.codex timestamp/content change.

This step may require GUI execution approval. If unavailable, record it as an unexecuted external verification gate and do not claim native visual acceptance.

- [ ] **Step 7: Commit Task 14**

~~~bash
git add tests/integration macos/DotSyncApp/Tests
git commit -m "test: prove native DotSync isolation"
~~~

### Task 15: Add release tooling that refuses unsafe Cask publication

**Files:**
- Create: packaging/dotsync-app.rb.in
- Create: scripts/render_cask.py
- Create: scripts/release_macos_app.sh
- Create: tests/test_macos_release.py
- Modify: tests/test_release_script.py
- Create: docs/testing/managed-account-macos-matrix.md
- Create only at an authorized real release: Casks/dotsync-app.rb

**Interfaces:**
- Consumes: exact tagged source, local build script, real Developer ID Application identity, stored notarytool Keychain profile, existing GitHub release.
- Produces: signed/notarized/stapled universal archive, real SHA-256, validated Cask depending on changja88/dotsync/dotsync.
- Never produces Casks/dotsync-app.rb in dry-run, fixture, unsigned, ad-hoc, failed notarization, failed Gatekeeper, or missing-credential modes.

- [ ] **Step 1: Write failing renderer and release-gate tests**

~~~python
VALID_URL = (
    "https://github.com/changja88/homebrew-dotsync/releases/download/"
    "v0.3.0/DotSync-0.3.0-macOS.zip"
)


def test_renderer_requires_real_version_sha_and_release_asset(tmp_path):
    output = tmp_path / "Casks" / "dotsync-app.rb"
    output.parent.mkdir()
    render_cask(
        version="0.3.0",
        sha256="a" * 64,
        url="https://github.com/changja88/homebrew-dotsync/releases/download/v0.3.0/DotSync-0.3.0-macOS.zip",
        output=output,
        repository_root=tmp_path,
    )
    text = output.read_text()
    assert 'cask "dotsync-app"' in text
    assert 'depends_on formula: "changja88/dotsync/dotsync"' in text
    assert 'depends_on macos: ">= :ventura"' in text
    assert 'app "DotSync.app"' in text
    assert "a" * 64 in text


@pytest.mark.parametrize(
    "version,sha,url",
    [
        ("0.0.0", "a" * 64, VALID_URL),
        ("0.3.0", "0" * 64, VALID_URL),
        ("0.3.0", "not-a-sha", VALID_URL),
        ("0.3.0", "a" * 64, "https://example.test/app.zip"),
    ],
)
def test_renderer_rejects_non_release_inputs(version, sha, url, tmp_path):
    output = tmp_path / "Casks" / "dotsync-app.rb"
    output.parent.mkdir()
    with pytest.raises(ValueError):
        render_cask(
            version=version,
            sha256=sha,
            url=url,
            output=output,
            repository_root=tmp_path,
        )
~~~

Shell contract tests execute release_macos_app.sh with fake command shims and prove every missing/failing gate exits before gh upload or Cask output:

- clean primary main checkout and exact vVERSION tag;
- full Python, Node, Swift, and local bundle verification;
- DEVELOPER_ID_APPLICATION is non-empty and codesign can resolve it;
- NOTARYTOOL_PROFILE is non-empty and stored credentials are usable;
- universal architectures;
- hardened runtime signing and timestamp;
- codesign strict verification;
- successful notarytool --wait;
- successful stapler validate;
- successful spctl --assess --type execute;
- final post-staple archive SHA from actual bytes;
- existing matching GitHub release;
- successful asset upload before rendering Cask;
- brew audit of the generated Cask before the script stops.

- [ ] **Step 2: Run release tests and verify RED**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/test_macos_release.py tests/test_release_script.py -v
~~~

Expected: FAIL because renderer/template/release script do not exist.

- [ ] **Step 3: Implement the exact Cask template and strict renderer**

packaging/dotsync-app.rb.in:

~~~ruby
cask "dotsync-app" do
  version "__DOTSYNC_VERSION__"
  sha256 "__DOTSYNC_SHA256__"

  url "__DOTSYNC_URL__"
  name "DotSync"
  desc "Menu bar companion for DotSync config sync and Codex subscription usage"
  homepage "https://github.com/changja88/homebrew-dotsync"

  depends_on macos: ">= :ventura"
  depends_on formula: "changja88/dotsync/dotsync"

  app "DotSync.app"
end
~~~

render_cask.py:

- accepts only X.Y.Z where X, Y, Z are canonical non-negative integers and the value is not 0.0.0;
- accepts exactly 64 lowercase hexadecimal characters excluding all-zero;
- accepts exactly the GitHub release URL whose vVERSION and archive name match VERSION;
- requires output named Casks/dotsync-app.rb below an explicit repository root;
- refuses replacement unless the CLI caller supplies --replace-existing;
- renders UTF-8 with no remaining sentinel;
- writes atomically and mode 0644;
- never fetches a URL or guesses a checksum.

- [ ] **Step 4: Implement the fail-closed signed release script**

scripts/release_macos_app.sh takes one explicit VERSION and performs:

~~~text
preflight exact tag/clean primary main
→ export tag to mktemp directory
→ run all automated tests from tagged source
→ build unsigned universal app from tagged source
→ codesign with Developer ID + hardened runtime + timestamp
→ codesign strict verification
→ zip for notarization
→ notarytool submit with the named Keychain profile and --wait
→ stapler staple + validate
→ spctl assess
→ recreate final zip containing the stapled app
→ compute SHA-256 from final zip
→ upload exact asset to existing GitHub release
→ render Casks/dotsync-app.rb from exact version/url/hash
→ brew audit the generated Cask
→ stop for explicit publication confirmation
~~~

Required signing commands:

~~~bash
codesign --force --options runtime --timestamp \
  --sign "$DEVELOPER_ID_APPLICATION" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
ditto -c -k --keepParent "$APP" "$NOTARY_ZIP"
xcrun notarytool submit "$NOTARY_ZIP" \
  --keychain-profile "$NOTARYTOOL_PROFILE" --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=4 "$APP"
ditto -c -k --keepParent "$APP" "$FINAL_ZIP"
shasum -a 256 "$FINAL_ZIP"
~~~

There is no --skip-sign, --skip-notary, ad-hoc, unsigned, no-check, guessed-hash, or no-assess path. The script uses mktemp -d and an EXIT trap, never a broad remove target. It does not push main or the Cask automatically.

- [ ] **Step 5: Create the exact manual evidence document**

docs/testing/managed-account-macos-matrix.md contains:

- release version, commit, macOS version, architecture, Codex CLI version;
- status field exactly BLOCKED, PASS, or FAIL;
- pre/post hashes and mtimes for ~/.claude, ~/.claude.json, ~/.codex;
- two Codex account login, refresh, cancel/retry, reauth, logout/delete results;
- menu popover/window/quit process-lifecycle results;
- Formula install, Cask install, Gatekeeper, codesign, notarization, and checksum evidence;
- Claude row fixed to POLICY_DISABLED with proof that no public call reached the adapter;
- no credential, token, raw provider output, email, account label, home path, or Keychain secret.

The document starts BLOCKED and can change to PASS only after every required evidence row is recorded without secrets.

- [ ] **Step 6: Run release-tooling tests and dry-run command shims**

Run:

~~~bash
.venv/bin/python3 -m pytest tests/test_macos_release.py tests/test_release_script.py -v
bash -n scripts/release_macos_app.sh
python3 scripts/render_cask.py --help
git diff --check
~~~

Expected: PASS. Casks/dotsync-app.rb must still be absent unless a real authorized release has completed every gate.

- [ ] **Step 7: Commit release tooling without publishing**

~~~bash
git add packaging/dotsync-app.rb.in scripts/render_cask.py scripts/release_macos_app.sh tests/test_macos_release.py tests/test_release_script.py docs/testing/managed-account-macos-matrix.md
git commit -m "build: gate signed DotSync app releases"
~~~

## External release gate

These actions change real accounts, Keychain/Homebrew state, GitHub assets, and the public tap. Stop and request explicit user authorization before running them.

- [ ] Run the two-real-Codex-account macOS matrix on a disposable macOS user.
- [ ] Confirm the Claude public capability is still policy-disabled.
- [ ] Run brew install --build-from-source ./Formula/dotsync.rb and brew test dotsync.
- [ ] Store notarytool credentials in Keychain outside repository files.
- [ ] Run scripts/release_macos_app.sh with the real Developer ID identity/profile.
- [ ] Compare the uploaded asset checksum with the generated Cask.
- [ ] Run brew audit --cask --strict Casks/dotsync-app.rb.
- [ ] Install the local generated Cask and verify Formula dependency, launch, Gatekeeper, menu/window, and clean Quit.
- [ ] Commit the real Casks/dotsync-app.rb only after the evidence document says PASS.
- [ ] Obtain explicit authorization before pushing the Cask commit or changing the public release.

The documented public command becomes valid only after that final push:

~~~bash
brew install --cask changja88/dotsync/dotsync-app
~~~

## Final Verification

- [ ] Run .venv/bin/python3 -m pytest and record the exact passing count.
- [ ] Run node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs.
- [ ] Run swift test --package-path macos/DotSyncApp.
- [ ] Run PYTHONPATH=lib python3 -m dotsync ui --check.
- [ ] Run bash scripts/build_macos_app.sh and verify both architectures.
- [ ] Run python3 -m compileall -q lib tests.
- [ ] Run git diff --check and inspect git diff plus git diff --cached.
- [ ] Search tracked product code for private provider endpoints, token logging, default provider-home mutation, external runtime assets, shell=True, and unbounded native stderr.
- [ ] Verify README English/Korean parity and AGENTS.md boundary changes.
- [ ] Verify docs/ui/concept-a/original-concepts.html and menu-bar-plus-management.html are unchanged from commit 5ac1a5d.
- [ ] Verify opening/closing popover and manager performs no provider refresh.
- [ ] Verify explicit Quit leaves no backend or provider child.
- [ ] Request an independent adversarial review before any real Cask publication.

## Spec Coverage Map

| Spec area | Implemented by |
|---|---|
| Native-host pipe/token/lifetime | Tasks 9 and 11 |
| Safe menu summary/no implicit refresh | Tasks 9 and 12 |
| Concept A popover + manager | Tasks 10 and 12 |
| Account/Sync actions and confirmations | Task 10 |
| Strict WKWebView origin/bridge | Task 12 |
| MenuBarExtra, Window, no Dock/login item | Tasks 12 and 13 |
| Formula backend and package assets | Tasks 10 and 13 |
| Multiple isolated Codex accounts | Existing Tasks 1–8 plus Task 14 |
| Claude policy-disabled public surface | Existing Task 8 plus Tasks 10, 14, and 15 |
| Default-profile non-mutation | Existing Tasks 1–8 plus Task 14 |
| Universal local app build | Task 13 |
| Signing/notarization/Gatekeeper/checksum | Task 15 and External release gate |
| Formula-dependent Homebrew Cask | Task 15 and External release gate |
| English/Korean docs and durable rules | Task 13 |
