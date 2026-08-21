# Unified DotSync App Design

**Date:** 2026-08-21
**Status:** Approved menu-bar architecture; written review gate
**Visual direction:** Concept A · Menu Bar Companion + Management Window

## 1. Goal

Install one unified DotSync product through a single Homebrew command that provides
both:

1. the existing configuration backup/apply capability, now available through a UI; and
2. a multi-account Codex subscription-usage dashboard, with the Claude Code adapter
   retained behind a policy-disabled capability gate until Anthropic explicitly
   permits third-party subscription login.

The account subsystem must never import, switch, overwrite, log out, or otherwise
mutate the user's existing default Claude/Codex profiles. Every enabled managed
account is created by a fresh official-CLI login inside DotSync-owned storage. In
the public version 1 build, this means Codex accounts only; the Claude adapter is
tested with fixtures but cannot be reached through a public account operation.

## 2. Delivery decision

Version 1 is a native macOS menu-bar application backed by the existing local Python
service.

- `DotSync.app` uses SwiftUI `MenuBarExtra` with the window style for the approved
  popover and a single-instance SwiftUI `Window` for the management surface.
- Both surfaces embed the same packaged HTML, CSS, and vanilla JavaScript in
  non-persistent `WKWebView` instances. Swift owns only native lifecycle, windowing,
  process supervision, strict navigation policy, and the menu-bar summary.
- The Python backend remains Python 3.12+, standard-library-only, and binds only to
  `127.0.0.1` on an ephemeral port. The native shell reuses the secured JSON API
  instead of duplicating account, usage, job, or sync rules in Swift.
- The app starts the backend as a child in a dedicated `--native-host` mode. A
  bounded one-line launch handshake travels over an anonymous pipe; the capability
  token never appears in process arguments, environment variables, files, logs, or
  persistent web data.
- The child lifetime is owned by the native app. Quitting the app gracefully closes
  jobs and provider children, then force-kills survivors after the existing bounded
  deadline. Browser mode retains the 30-minute idle policy as a diagnostic fallback.
- Opening the app is explicit. Version 1 does not install a login item or launch
  automatically after Homebrew installation.
- There is no Electron, Node runtime, Tauri runtime, PyObjC, or third-party Swift
  dependency. SwiftUI, AppKit, and WebKit are macOS system frameworks.

The existing `dotsync` Formula remains the source-built CLI and Python backend. A
new `dotsync-app` Cask installs a signed/notarized `DotSync.app` release artifact and
declares the Formula as a dependency, so one documented Cask command installs both.
Formula-only installation remains supported for CLI users. A public Cask release is
blocked until a real universal app archive has passed Developer ID signing,
notarization, Gatekeeper, and checksum verification; those credentials are never
invented or bypassed.

The native primitives are documented by Apple in
[`MenuBarExtra`](https://developer.apple.com/documentation/swiftui/menubarextra),
[`Window`](https://developer.apple.com/documentation/swiftui/window), and
[`WKWebView`](https://developer.apple.com/documentation/webkit/wkwebview). The
Formula/Cask split follows Homebrew's
[`Cask Cookbook`](https://docs.brew.sh/Cask-Cookbook) and
[`Acceptable Formulae`](https://docs.brew.sh/Acceptable-Formulae).

## 3. Scope

### Included

- Concept A menu-bar popover and full management window.
- Account list, add, rename, reauthenticate, refresh, and delete flows.
- Multiple isolated Codex ChatGPT subscription accounts.
- A policy-disabled Claude capability state; the fixture-tested official-CLI adapter
  remains internal until Anthropic explicitly permits third-party subscription login.
- Five-hour and seven-day usage windows when supplied by the provider.
- Reset times, last refresh time, stale state, and actionable errors.
- Existing DotSync tracked-app status, backup preview/execute, and apply
  preview/execute flows.
- Sync-folder selection and persisted UI preference.
- Native backend supervision, strict `WKWebView` navigation, and a cached menu-bar
  summary that never invokes provider refresh implicitly.
- A Homebrew Cask that depends on the existing Formula and installs `DotSync.app`.
- English and Korean README parity.

### Excluded

- API-key billing or API token-cost dashboards.
- Importing `~/.claude`, `~/.codex`, browser cookies, or existing OAuth tokens.
- Direct calls to Anthropic private OAuth endpoints or ChatGPT private HTTP endpoints.
- Account hot-swapping in the user's default Claude/Codex installation.
- Launching normal coding sessions under managed accounts.
- Provider refresh merely because the popover or management window is closed.
- Team/Enterprise administrative analytics.
- Native notifications, launch-at-login, Dock presence, and automatic launch after
  install.
- A second native implementation of account, usage, job, or sync domain rules.
- Electron, Tauri, PyObjC/rumps, bundled Chromium, or a Node runtime.
- Claude status-line installation or ingestion; version 1 refreshes through `/usage`.

## 4. Non-negotiable invariants

1. Usage/account operations never write to `~/.claude`, `~/.claude.json`, or
   `~/.codex`.
2. Usage/account operations never target the default Claude or Codex Keychain item.
3. Claude login credentials are managed only by the official Claude CLI. On macOS,
   the CLI may create a profile-scoped Keychain item; this is the sole storage
   exception outside the DotSync data root.
4. Codex credentials use `cli_auth_credentials_store = "file"` and remain inside
   the account's DotSync-owned `CODEX_HOME`.
5. DotSync never reads, copies, returns, caches, or logs OAuth access/refresh tokens.
6. DotSync never calls provider-private usage HTTP endpoints.
7. Every child process receives an explicit, sanitized environment and an
   account-owned working directory.
8. Provider raw output never crosses the presentation boundary until it has passed
   redaction; neither the browser fallback nor the native web views receive it.
9. An account identifier is an application-generated UUID, never a user-supplied
   path component.
10. Symlinks are rejected anywhere under a managed account root before mutation or
    recursive deletion.
11. Existing DotSync `apply` remains an explicit exception: it may modify local app
    configuration only after preview, confirmation, and backup. The Account screens
    never invoke `apply`.
12. The native shell never reads or writes provider profile paths. It can start and
    stop the DotSync backend, render safe DTOs, and open native windows only.
13. `WKWebView` uses a non-persistent website data store, accepts content only from
    the exact launched loopback origin, and rejects all in-view external navigation.
14. The launch capability is delivered only over the parent-child pipe, held in
    memory, removed from visible navigation state after bootstrap, and never copied
    to diagnostics.
15. Menu-bar percentages and attention counts come from a narrow safe summary DTO.
    Missing, stale, or failed data displays an unknown/stale state, never fabricated
    zero usage or a clean sync state.
16. Keeping the menu-bar app running may keep the local backend alive, but it never
    triggers a provider CLI refresh without an explicit user action.

## 5. Storage model

The executable's installation directory is immutable and upgrade-owned. Persistent
state lives at the standard macOS application-data location:

```text
~/Library/Application Support/DotSync/
├── state.json
├── accounts.json
├── accounts/
│   ├── claude/
│   │   └── <account-uuid>/
│   │       ├── home/          # CLAUDE_CONFIG_DIR
│   │       ├── probe/         # empty cwd for login/usage probes
│   │       └── tmp/           # CLAUDE_CODE_TMPDIR
│   └── codex/
│       └── <account-uuid>/
│           ├── home/          # CODEX_HOME; config.toml + auth.json
│           ├── probe/
│           └── tmp/
└── usage/
    └── <account-uuid>.json
```

The `accounts/claude/` branch reserves the future schema only; the public version 1
build does not create it while the Claude capability gate is disabled.

Both JSON documents carry `schema_version: 1`; an unknown version fails closed and
never triggers a destructive best-effort migration. `state.json` contains the
selected sync-folder path and UI preferences. `accounts.json` contains only
non-secret metadata: UUID, provider, user label,
provider display identity when available, creation time, and lifecycle state.
Usage cache files contain only bucket identifiers/labels, percentages, window
durations, reset timestamps, observation time, and provider version. Directories use
mode `0700`; created files use mode `0600`.
Writes use a sibling temporary file, `fsync`, atomic replacement, and final mode
verification.

No account data or usage cache is stored in the user-selected sync folder because
that folder may be committed to Git or synchronized through cloud storage.

The native shell adds no credential store. macOS may persist ordinary system-owned
window placement, but DotSync application state, account metadata, and cached usage
remain under the same Application Support root. `WKWebView` cookies, cache, history,
and local storage are ephemeral for each app process.

## 6. Components

### 6.1 Application state

`dotsync.app_paths` resolves the data root and creates private directories.
`dotsync.app_state` owns atomic JSON persistence for application settings.
`dotsync.accounts.store` owns account metadata and validates all account paths.

These modules do not know about HTTP, terminal rendering, or provider commands.

### 6.2 Provider adapters

Both adapters implement this conceptual contract:

```python
class UsageProvider(Protocol):
    def login(
        self,
        account: ManagedAccount,
        report: Callable[[LoginProgress], None],
    ) -> ProviderIdentity:
        raise NotImplementedError

    def refresh_usage(self, account: ManagedAccount) -> UsageSnapshot:
        raise NotImplementedError

    def logout(self, account: ManagedAccount) -> None:
        raise NotImplementedError
```

The public result objects contain normalized state only. Provider stdout/stderr and
credentials remain internal to the adapter.

#### Codex

- Set `CODEX_HOME=<account>/home` for every invocation.
- Create `config.toml` with `cli_auth_credentials_store = "file"` before login.
- Use the official `codex app-server` JSON-RPC interface.
- Login uses `account/login/start` and completion notifications.
- Usage uses `account/rateLimits/read`.
- Codex `primary`/`secondary` windows are classified from documented
  `windowDurationMins`; 300 minutes maps to five-hour, 10,080 minutes maps to
  seven-day, and other durations remain visible as labeled `other` buckets.
- Logout uses `account/logout` or, if the app-server cannot start, the official
  `codex logout` scoped to that `CODEX_HOME`.
- The adapter never points an app-server process at `~/.codex`.

#### Claude

This adapter remains an internal, fixture-tested capability in the public version 1
build. No public API route, native bridge message, or visible control may reach its
login, refresh, logout, or deletion operations until explicit Anthropic permission
is recorded and the release gate is reviewed again. If enabled later, it must obey
all of the following constraints:

- Set `CLAUDE_CONFIG_DIR=<account>/home` and
  `CLAUDE_CODE_TMPDIR=<account>/tmp` for every invocation.
- Remove inherited `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, and provider-routing
  variables so subscription OAuth cannot silently become API billing.
- Use the official `claude auth login` command in a controlled PTY.
- Reject Claude Code versions older than `2.1.215`; this is the oldest version in
  the compatibility matrix whose profile-scoped macOS Keychain behavior is accepted
  for release.
- Run the CLI from `<account>/probe`, never from a user project.
- Query fresh usage by opening the official interactive CLI in a controlled PTY,
  entering `/usage`, reconstructing the terminal screen, and parsing the displayed
  plan windows.
- Logout uses `claude auth logout` under the same account environment before the
  account directory is removed.

Claude terminal parsing is versioned. Unknown layouts return `unsupported` with the
installed Claude version; they never silently report zero usage.

### 6.3 Usage service

`dotsync.usage.service` coordinates providers, cache freshness, and concurrency.

- At most one login/logout/refresh operation runs for an account.
- At most two provider refreshes run globally.
- Opening the dashboard displays the last cached snapshot and its age; it does not
  automatically invoke provider CLIs.
- Stale accounts refresh only after an explicit Refresh action in version 1.
- Failed refresh keeps the last successful snapshot, marks it stale, and exposes a
  short safe error code.
- A refresh timeout is 30 seconds for Codex and 45 seconds for Claude.

### 6.4 Existing DotSync service

CLI orchestration currently embedded in `dotsync.cli` is moved behind
`dotsync.sync_service` without changing public CLI behavior.

The service exposes:

- load configuration and tracked-app status;
- preview backup/apply as serializable plan data;
- execute a previously previewed plan;
- select/update the sync folder and tracked applications.

An execution request includes a plan digest. The service recomputes the plan and
rejects execution if files changed after preview. `apply` retains the existing local
backup behavior.

### 6.5 Local application boundary

`dotsync.web.server` uses `ThreadingHTTPServer`. It serves packaged static assets and
a narrow JSON API. Mutations are asynchronous jobs so login and provider probes do
not block request threads.

Primary routes:

```text
GET    /api/bootstrap
GET    /api/health
GET    /api/accounts
POST   /api/accounts
PATCH  /api/accounts/<uuid>
POST   /api/accounts/<uuid>/login
POST   /api/accounts/<uuid>/refresh
POST   /api/accounts/<uuid>/logout
DELETE /api/accounts/<uuid>
GET    /api/jobs/<uuid>
GET    /api/sync/status
PATCH  /api/sync/apps
POST   /api/sync/preview
POST   /api/sync/execute
POST   /api/settings/sync-folder/select
POST   /api/settings/app-data/reveal
POST   /api/heartbeat
```

The server:

- binds only to `127.0.0.1`;
- rejects non-loopback Host headers;
- emits no CORS headers;
- requires a random 256-bit per-process capability token on every API request;
- accepts JSON bodies no larger than 64 KiB;
- uses exact route matching and strict UUID/provider/action allowlists;
- adds `Content-Security-Policy`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, and `Cache-Control: no-store`;
- refuses to serve filesystem paths supplied by HTTP parameters;
- in browser mode, shuts down after 30 minutes without a browser heartbeat and no
  active job;
- in native-host mode, is owned by the parent control pipe and shuts down when the
  native shell exits or closes that pipe;
- on shutdown, signals every provider job, terminates its registered child process,
  waits up to two seconds, then kills any survivor before closing the job registry.

Native hosting adds one narrow read endpoint:

```text
GET /api/menu-summary
```

Its exact DTO contains only the highest known safe usage percentage, sync attention
count, stale/unknown state, and observation time. It never exposes paths, identities,
account labels, provider output, tokens, or job payloads. Reading it may inspect
cached/application state but may not launch provider CLIs or mutate sync state.

### 6.6 Native macOS shell

The native source lives under `macos/DotSyncApp/` and targets macOS 13 or newer. It
uses a small set of explicit roles:

- `BackendProcess` resolves the Formula-installed `dotsync` launcher, starts
  `dotsync ui --native-host`, validates its launch handshake, observes exit, and owns
  graceful/forced shutdown. Resolution accepts an injected test path and the two
  standard Homebrew prefixes; an unsupported custom prefix produces a recoverable
  setup error instead of executing an untrusted path.
- `LocalOrigin` validates exactly `http://127.0.0.1:<ephemeral-port>` plus the
  high-entropy launch capability. It rejects user info, fragments in the origin,
  non-loopback hosts, non-HTTP schemes, invalid ports, extra keys, and oversized
  handshake data.
- `WebSurface` wraps `WKWebView` with `WKWebsiteDataStore.nonPersistent()`, a shared
  ephemeral process pool, and an exact navigation allowlist. It loads either the
  popover or management surface from the validated origin. Provider verification
  URLs continue through the backend-owned safe external-open seam; web content
  cannot navigate the embedded view away from DotSync.
- `AppBridge` accepts only fixed native messages such as `open_manager` with an
  allowlisted destination. It never accepts filesystem paths, commands, URLs,
  provider names, account identifiers, or arbitrary JSON actions from JavaScript.
- `MenuSummaryModel` reads only `/api/menu-summary`, presents unknown/stale states
  conservatively, and updates after explicit UI work or a bounded cached-state poll.
  It does not own provider refresh policy.

The backend emits exactly one schema-versioned JSON handshake line no larger than
4 KiB within five seconds, then reserves stdout from further application output.
The shell rejects malformed, duplicate-key, extra-field, non-loopback, or mismatched
version handshakes and shows a native recovery panel. Backend stderr is bounded and
redacted before any generic diagnostic is displayed; raw output is never rendered.

The launch URL is loaded only into ephemeral web views. Packaged JavaScript captures
the token in memory, removes it from visible history immediately, and sends it in the
existing `X-DotSync-Token` header. The server remains silent for request logging and
emits `Referrer-Policy: no-referrer` and `Cache-Control: no-store` as before.

## 7. Concept A information architecture

The immutable visual references live under `docs/ui/concept-a/`:

- `original-concepts.html` preserves the recovered exploration; its first option,
  **A · Menu Bar Companion**, is the selected original.
- `menu-bar-plus-management.html` is the approved extension and is authoritative for
  how the popover and full management window work together.

Production assets may simplify fixture content but must preserve the original
pink-purple glass visual language, popover proportions, and two-speed interaction
model. A sidebar-only browser dashboard is not Concept A.

### Menu-bar item and popover

- The status item always provides an accessible DotSync title/icon. When safe cached
  data is known it may also show the highest usage percentage and sync-attention
  count; unavailable data shows an unknown marker.
- Clicking it opens the compact Concept A popover without activating provider CLIs.
- The popover shows last update time, the policy-disabled Claude state, compact
  Codex account usage, sync attention, Backup/Apply preview entry points, and
  `Open management window`.
- Backup and Apply buttons navigate to a concrete preview in the management window;
  they never execute directly from the first click in the popover.

### Management window

The full window uses these destinations:

- Overview
- Accounts
- Config Sync
- Settings

#### Overview

- Header with last-refresh time and global Refresh button.
- Claude policy state and a Codex section containing account cards.
- Each account card shows label, provider identity/plan when known, lifecycle state,
  five-hour bar, seven-day bar, reset times, and per-account actions.
- Compact DotSync status card showing selected sync folder, clean/dirty/error counts,
  and links to Backup or Apply preview.

#### Accounts

- `Add Codex account` is enabled. Claude actions are visibly policy-disabled with a
  stable explanation and make no provider/job call.
- Login progress appears as a job panel with only safe official-CLI instructions.
- Account menus provide Rename, Reauthenticate, Log out, and Delete.
- Delete confirmation names the exact account and states that the existing default
  Claude/Codex installation will not be touched.

#### Config Sync

- Tracked-app status list reuses existing clean/dirty/missing/unknown semantics.
- Backup and Apply always open a concrete preview.
- Apply uses a stronger destructive confirmation and displays the backup location.
- Account usage controls never appear inside a Sync execution confirmation.

#### Settings

- Sync-folder selector.
- App-data path and `Open in Finder` action.
- Privacy summary and external CLI versions.
- Fixed 15-minute freshness policy and the age of each cached snapshot.

Closing the management window returns the app to its menu-bar-only presence; it does
not quit the app or refresh providers. Quitting is an explicit menu action. Version 1
has no launch-at-login toggle.

## 8. Error semantics

Provider errors normalize to these stable codes:

- `cli_missing`
- `not_logged_in`
- `reauth_required`
- `login_cancelled`
- `refresh_timeout`
- `unsupported_cli_version`
- `unsupported_usage_layout`
- `provider_unavailable`
- `logout_failed`
- `unsafe_account_path`

Native-shell failures use a separate safe set:

- `backend_not_found`
- `backend_start_failed`
- `backend_protocol_error`
- `backend_exited`

UI messages state the affected account and the next safe action. Debug logs may
include executable version, exit code, timing, and redacted parser state; they may
not contain raw auth output, OAuth strings, browser callback URLs, or full terminal
buffers.

Native startup messages never include the rejected launch line, capability token,
child environment, raw stderr, or executable search paths. They offer only Retry,
Open installation help, or Quit. An unexpected backend exit does not loop-restart;
the user chooses Retry after active jobs have been reconciled or failed closed.

## 9. Account deletion

Deletion is two-phase:

1. Run the official provider logout scoped to the managed account.
2. Only after logout succeeds, validate the account path and remove the account
   directory, cache, and metadata.

If logout fails, the default action preserves the account folder and reports the
failure. A separate `Remove local profile anyway` confirmation may remove the
directory but must disclose that a provider-scoped Keychain entry could remain.
DotSync never enumerates or deletes the default Claude Keychain item.

## 10. Testing strategy

- TDD for every state, process, parser, service, and route behavior.
- Unit tests run with a temporary data root and blocked subprocesses.
- Provider tests use recorded, redacted JSON-RPC and PTY fixtures.
- Contract tests assert no provider adapter receives a default-home path.
- Filesystem tests cover symlinks, path traversal, permissions, atomic-write failure,
  and interrupted deletion.
- Web tests cover loopback binding, Host validation, capability token, body limits,
  CSP, method allowlists, job isolation, and redaction.
- Swift unit tests cover strict handshake decoding, executable resolution, process
  ownership, bounded shutdown, navigation rejection, the fixed bridge allowlist,
  safe menu-summary decoding, and unknown/stale rendering.
- Fixture UI tests open both the popover and management window against a temporary
  backend, navigate every approved destination, and prove account and Apply
  confirmations cannot execute without the expected digest/job contract.
- Native lifecycle tests terminate the window, backend, and provider-child layers in
  each order and verify no orphan remains.
- Visual review compares both surfaces against the two checked-in Concept A HTML
  references at representative popover and management-window sizes.
- Existing CLI tests remain unchanged where behavior is intentionally preserved.
- A manual macOS matrix covers the current stable Codex CLI, two Codex accounts,
  cancel/reauth/delete, and verification that default profiles remain byte-for-byte
  unchanged. Claude remains fixture-only and does not use real subscription accounts
  in the release matrix until the policy gate is explicitly lifted.

## 11. Documentation and release

- Update English and Korean README sections together.
- Amend `AGENTS.md` so the new application-data directory and scoped Claude Keychain
  exception are explicit while the sync-folder rules remain intact.
- Document that the app invokes official installed CLIs and is not affiliated with
  Anthropic or OpenAI.
- The public version 1 build keeps Claude account operations disabled under the
  current policy ruling. Before any later release enables them, review Anthropic's
  then-current authentication terms and record explicit permission. A failed or
  ambiguous review keeps the capability disabled; it must never be replaced by
  private OAuth or cookie scraping.
- Homebrew validation keeps the Formula source build and `brew test dotsync`, then
  installs the `dotsync-app` Cask artifact, launches `DotSync.app` without a provider
  request, and verifies the Formula-backed native-host handshake.
- Public release CI builds the universal app archive, signs it with a real Developer
  ID Application identity, notarizes and staples it, verifies Gatekeeper acceptance,
  computes the real archive SHA-256, and only then updates the Cask. Missing signing
  or notarization credentials block public app release; they do not trigger an
  unsigned or ad-hoc fallback.
- The documented one-command application install is
  `brew install --cask changja88/dotsync/dotsync-app`; the Cask installs
  `DotSync.app` and its Formula dependency. Existing CLI-only users may keep
  `brew install changja88/dotsync/dotsync`.

## 12. Acceptance criteria

1. Two Codex accounts can be added and independently refreshed. Public Claude
   account create/login/refresh/logout/delete remains policy-disabled before any
   provider or job call until explicit Anthropic permission is recorded.
2. Existing `~/.claude`, `~/.claude.json`, and `~/.codex` content remains unchanged
   throughout every account/usage operation.
3. Codex usage is read through official app-server RPC only.
4. The fixture-tested Claude adapter reads usage through official CLI surfaces only,
   and no public Claude operation can reach that adapter while the policy gate is
   disabled.
5. Killing the UI or a provider process cannot corrupt account metadata.
6. A parser failure never becomes a fabricated 0% or 100% value.
7. Deleting one managed account cannot affect another managed or default account.
8. Backup/apply CLI behavior and full existing test coverage remain green.
9. The Concept A menu-bar item opens the approved popover, and the same app opens a
   full management window supporting Codex add, refresh, rename, reauth, logout,
   delete, Sync preview, and explicit Apply confirmation without terminal-only steps
   other than the official provider login interaction.
10. Neither opening nor closing either UI surface invokes a provider CLI. Explicit
    refresh remains the only subscription-usage refresh trigger.
11. The native shell accepts only an exact loopback launch handshake, never persists
    the capability token, and never allows embedded navigation outside the launched
    DotSync origin.
12. Quitting `DotSync.app`, killing the backend, or cancelling a provider job leaves
    no orphan backend/provider process and cannot corrupt account, cache, job, or sync
    state.
13. The checked-in original and extended Concept A artifacts remain the visual
    regression baseline; a sidebar-only browser dashboard does not satisfy approval.
14. A public Cask is shipped only after the exact release archive passes signing,
    notarization, Gatekeeper, checksum, Formula dependency, and fixture-only launch
    checks.
