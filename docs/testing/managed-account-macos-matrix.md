BLOCKED

# Managed-account macOS release evidence

This record may change to `PASS` only after every required row contains
redacted evidence from the exact signed release candidate. Any failed row sets
the status to `FAIL`; any missing row keeps it `BLOCKED`.

Never record a credential, token, raw provider response, email address, account
label, absolute home path, Keychain secret, signing secret, or notary credential
in this file. Record only versions, hashes, mtimes, command exit status, public
certificate metadata, notarization request status, and concise observed results.

## Release candidate

| Field | Redacted evidence | Status |
|---|---|---|
| Release version | Not recorded | BLOCKED |
| Exact commit | Not recorded | BLOCKED |
| macOS version | Not recorded | BLOCKED |
| Hardware architecture | Not recorded | BLOCKED |
| Codex CLI version | Not recorded | BLOCKED |

## Default-profile non-mutation

Record SHA-256 and nanosecond mtime before the first operation and after the
last operation. Use `ABSENT` when a path did not exist at both observations;
do not record its resolved absolute path or any file contents.

| Default profile path | Pre SHA-256 | Pre mtime | Post SHA-256 | Post mtime | Status |
|---|---|---|---|---|---|
| `~/.claude` tree manifest | Not recorded | Not recorded | Not recorded | Not recorded | BLOCKED |
| `~/.claude.json` | Not recorded | Not recorded | Not recorded | Not recorded | BLOCKED |
| `~/.codex` tree manifest | Not recorded | Not recorded | Not recorded | Not recorded | BLOCKED |

## Two isolated Codex profiles

Do not record provider output or identifying profile metadata. “First” and
“second” refer only to test order.

| Operation | First isolated profile | Second isolated profile | Status |
|---|---|---|---|
| Official CLI login completes | Not recorded | Not recorded | BLOCKED |
| Explicit usage refresh remains isolated | Not recorded | Not recorded | BLOCKED |
| Login cancellation leaves recoverable state | Not recorded | Not recorded | BLOCKED |
| Retry after cancellation succeeds | Not recorded | Not recorded | BLOCKED |
| Reauthentication remains profile-scoped | Not recorded | Not recorded | BLOCKED |
| Official CLI logout completes | Not recorded | Not recorded | BLOCKED |
| Confirmed local-profile deletion affects only the selected profile | Not recorded | Not recorded | BLOCKED |

## Native process lifecycle

| Observation | Redacted evidence | Status |
|---|---|---|
| Opening and closing the menu popover starts no provider refresh | Not recorded | BLOCKED |
| Opening and closing the management window starts no provider refresh | Not recorded | BLOCKED |
| Menu popover renders the approved safe summary | Not recorded | BLOCKED |
| Management window completes the approved fixture workflows | Not recorded | BLOCKED |
| Explicit Quit leaves no backend child | Not recorded | BLOCKED |
| Explicit Quit leaves no provider child | Not recorded | BLOCKED |

## Installation and release artifact

| Gate | Redacted evidence | Status |
|---|---|---|
| Formula source installation | Not recorded | BLOCKED |
| Formula `brew test` | Not recorded | BLOCKED |
| Local generated Cask installation and Formula dependency | Not recorded | BLOCKED |
| `codesign --verify --deep --strict --verbose=2` | Not recorded | BLOCKED |
| Developer ID hardened-runtime signature and timestamp | Not recorded | BLOCKED |
| Accepted notarization request and stapler validation | Not recorded | BLOCKED |
| Gatekeeper execute assessment | Not recorded | BLOCKED |
| Uploaded archive SHA-256 equals generated Cask SHA-256 | Not recorded | BLOCKED |
| Installed app launch and Formula-backed handshake | Not recorded | BLOCKED |
| Installed menu, window, and clean Quit | Not recorded | BLOCKED |

## Claude policy gate

| Capability | Required result | Evidence | Status |
|---|---|---|---|
| Public Claude account create/login/refresh/logout/delete | POLICY_DISABLED | No public call may reach the Claude adapter; adapter-call counter or equivalent redacted fixture evidence not recorded | BLOCKED |

## Final decision

| Requirement | Decision | Status |
|---|---|---|
| Every row above is complete and contains no prohibited data | Not established | BLOCKED |
| Exact release archive passed signing, notarization, stapling, Gatekeeper, checksum, install, launch, and lifecycle checks | Not established | BLOCKED |
| Public Cask publication explicitly authorized after review | Not authorized | BLOCKED |
