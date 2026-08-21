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

Assembly happens in a private staging directory. A failed rebuild removes that
staging directory and leaves any pre-existing valid `build/DotSync.app`
unchanged; a verified rebuild replaces the previous app only at the final
publication step.

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
