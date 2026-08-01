# Herdr config sync design

## Goal

Add Herdr to dotsync as a normal file-based app so `dotsync backup` and
`dotsync apply` include Herdr when it is selected. Synchronize only Herdr's
user-authored `config.toml`.

## Scope

- Local source and destination: `~/.config/herdr/config.toml`
- Sync-folder copy: `<sync folder>/herdr/config.toml`
- App identifier: `herdr`
- Local presence detection: the local `config.toml` exists

The following Herdr state is explicitly excluded: `session.json`,
`session-history.json`, `plugins.json`, log files, Unix sockets,
`.plugins.lock`, and every other file under `~/.config/herdr/`.

## Architecture

Add a `HerdrApp` subclass following the existing simple file-app pattern.
It declares one `FilePair` from the local path to the sync-folder path and
does not override `sync_from`, `sync_to`, `plan_from`, `plan_to`, or `status`.
The base `App` implementation therefore owns previewing, copying, status
comparison, symlink rejection, destination-directory creation, and the local
backup performed before apply.

Register `HerdrApp` in `APP_CLASSES`. The existing derived registry behavior
then exposes Herdr to app selection, automatic local detection, `--all`,
configuration validation, and app construction without Herdr-specific CLI
branches.

## Data flow and failure behavior

For backup, dotsync copies `~/.config/herdr/config.toml` to
`<sync folder>/herdr/config.toml`. For apply, dotsync first copies an existing
local config into the active dotsync backup session, then writes the stored
config to the local path. Status compares only these two files.

Missing files and symlinks retain the standard file-app behavior: a missing
required source fails rather than producing an empty configuration, and
symlinked managed paths are rejected. No Herdr process is started or reloaded,
and no network or external command is invoked.

## Tests

Add focused Herdr app tests that verify:

- backup copies `config.toml` to the exact stored path;
- unrelated Herdr runtime files are not copied;
- apply creates the local directory when necessary;
- apply backs up an existing local config before overwriting it;
- local presence detection depends only on `config.toml`;
- status uses the declared config pair.

Add integration round-trip coverage for local-to-stored-to-local and
stored-to-local-to-stored flows. Extend registry detection coverage so a local
Herdr config is recognized. These tests use the real base file-sync behavior
with filesystem-isolated fixtures.

## Documentation

Update the English and Korean README sections in parity: the product summary,
picker example, tracked-app summary, and supported-app list will include
Herdr. State that Herdr sync covers only `~/.config/herdr/config.toml` and
excludes session/runtime state.

## Out of scope

- Herdr-specific sync methods
- `herdr server reload-config`
- Session, pane, workspace, plugin-registry, log, socket, or lock-file sync
- New dependencies, network access, version bumps, or Homebrew formula changes
- Any change under `local_dev/`
