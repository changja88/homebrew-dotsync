# DotSync macOS app

This Swift package is the native macOS 13+ host for DotSync's Concept A menu-bar
popover and management window. It owns app/window lifecycle, starts the
Formula-installed `dotsync ui --native-host` backend, and embeds the backend's
loopback web surfaces in ephemeral WebKit views. Account, usage, job, and config
sync rules remain in the Python backend.

## Local development build

Install the Formula backend first, then build the universal app from the
repository root:

```bash
brew install changja88/dotsync/dotsync
make test-native
make build-app
open build/DotSync.app
```

`make build-app` compiles separate macOS 13 arm64 and x86_64 release executables,
combines only those executables, and writes `build/DotSync.app`. The result is an
unsigned local development artifact. The script does not create an archive,
sign, notarize, upload, publish, or generate a Cask.

Assembly requires a clean `build/DotSync.app` output path. If any file,
directory, or link already exists there, the build fails immediately and never
deletes or replaces that entry; remove a previous generated app yourself before
building again. A clean build assembles in a private staging directory, removes
that owned staging directory on ordinary failure or SIGINT, SIGTERM, and SIGHUP,
and publishes the verified app with one no-replace rename. Package sources and
the plist template are manifested before copying, copied through pinned
descriptors, and compared with both a second source manifest and the private
snapshot. The complete package and plist snapshot is revalidated immediately
before and after every Swift build and `--show-bin-path` call, so source changes
during copying and tool-side input mutations fail before another architecture
runs.

New build and staging directories begin under 128-bit random private names. The
first filesystem observation after `mkdir` is a no-follow open, followed by an
empty-directory, link, mode, device, binding, and creation-time proof. A failed
proof means no ownership was established: the script does not chmod or delete
that name. As with any same-user filesystem protocol, a peer that learns the
random name and substitutes an indistinguishable empty directory inside that
small create/open interval cannot be identified perfectly from filesystem
metadata alone; the random name and fail-closed pristine proof narrow that
unavoidable local boundary.

Publication rollback is identity-bound too. It atomically swaps the public name
with a no-follow, beneath-resolved private placeholder, then inspects the entry
captured in the private stage. An owned app is deleted only through its held
private descriptor. An unowned captured replacement is swapped back to the
public `build/DotSync.app` name and reported as exact ownership loss, even when
a termination signal is also pending; it is never recursively deleted. If the
same-user peer keeps rebinding names during recovery, the builder fails closed
and can preserve the private stage rather than delete an entry it cannot prove
it owns. It cannot safely find or delete an owned inode after another actor has
already moved that inode away from every builder-known binding.

Every child build tool runs in its own process group. The first SIGINT, SIGTERM,
or SIGHUP is retained as the final `128 + signal` status, forwarded to the full
group, and escalated when descendants do not exit. Later signals cannot raise
through cleanup. A signal during staging cleanup or immediately after the
no-replace publication removes the exact held app and leaves neither staging nor
final output behind. The two tool outputs consumed by the supervisor are drained
concurrently, decoded as strict UTF-8, and limited to 64 KiB. Overflow, read,
decode, or pipe-close failure terminates and quiesces the exact process group
while its leader PID remains reserved, then reaps that exact leader.

The public Cask remains blocked until a real release archive passes Developer ID
signing, notarization, Gatekeeper, and checksum verification. The eventual Cask
will depend on the existing Formula rather than bundling a second Python runtime.

## Diagnostics

Run the state-free backend installation check without creating Application
Support data or launching a provider process:

```bash
dotsync ui --check
```

The app keeps its own account metadata and cached usage under
`~/Library/Application Support/DotSync/`. Managed Codex credentials stay inside
account-owned `CODEX_HOME` directories there. Account and usage operations do not
write `~/.claude`, `~/.claude.json`, or `~/.codex`; the native Swift process does
not inspect those provider homes.
