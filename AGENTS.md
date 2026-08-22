# AGENTS.md

This file gives Codex persistent project guidance for this repository.

## Repository Identity

This repository is the `changja88/homebrew-dotsync` Homebrew tap. It contains
three closely related deliverables:

- `dotsync`, a Python CLI under `lib/dotsync/` with entry points at
  `bin/dotsync` and `dotsync.cli:main`.
- `Formula/dotsync.rb`, the Homebrew formula used by
  `brew install changja88/dotsync/dotsync`.
- `macos/DotSyncApp/`, the native macOS 13+ menu-bar host for the Formula
  backend. Local builds are development artifacts until the public Cask gate
  is satisfied.

`dotsync` is a macOS-only CLI for syncing selected app configuration files
between local app locations and one user-chosen sync folder.

> **`local_dev/` is unrelated to `dotsync`.** Anything under `local_dev/` is an
> internal-only development tool (currently a Serena-aware codex/claude
> launcher) that merely co-lives in this checkout. It is not
> packaged by the Homebrew formula, shares no runtime code with
> `lib/dotsync/`, and must not appear in the public `README.md` or in the root
> `Makefile`'s `make help`. Its own targets and docs live inside that
> directory (`local_dev/Makefile`, `local_dev/README.md`). Do not bundle
> `local_dev` changes with `dotsync` changes in the same commit.
> **The runtime copy lives at `~/Desktop/dotsync_config/agent_launcher/` and
> `~/.zshrc` only references that stable location — promote dev edits via
> `make -C local_dev install-shim`, which mirrors the dev tree there and
> rewrites the managed block in `~/.zshrc` in one step (no separate deploy
> command; this is a local copy, not an external publish).**

## Core Architecture

- `lib/dotsync/cli.py` owns argparse command dispatch for `welcome`, `init`,
  `config`, `apps`, `status`, `backup`, `apply`, and `ui`.
- `macos/DotSyncApp/` owns native lifecycle, backend supervision, strict WebKit
  hosting, and the menu-bar/management-window shell; Python retains the domain
  rules.
- `lib/dotsync/config.py` owns sync-folder discovery and `dotsync.toml`
  persistence. Config lives only at `<sync folder>/dotsync.toml`.
- `lib/dotsync/backup.py` creates `apply` backups inside the sync folder, normally
  `<sync folder>/.backups/<timestamp>/<app>/`.
- `lib/dotsync/shellrc.py` owns shell rc detection and idempotent
  `DOTSYNC_DIR` export insertion/update logic.
- `lib/dotsync/ui.py` and `lib/dotsync/ui_picker.py` own terminal output,
  colors, prompts, summaries, and picker behavior.
- `lib/dotsync/apps/base.py` defines the app plugin contract:
  `App`, `AppStatus`, `FilePair`, and `diff_files`.
- `lib/dotsync/apps/__init__.py` is the single source of truth for registered
  apps through `APP_CLASSES`.
- Concrete app modules live in `lib/dotsync/apps/`: `claude`, `ghostty`,
  `bettertouchtool`, and `zsh`.

## Non-Negotiable Design Rules

- Runtime dependencies must stay stdlib-only. Do not add `click`, `requests`,
  `pydantic`, or similar dependencies. This keeps the Homebrew formula simple.
- Target runtime is Python 3.12+. Keep `pyproject.toml`,
  `lib/dotsync/__init__.py`, and `Formula/dotsync.rb` aligned when changing
  versions.
- Treat the project as macOS-only. Do not add Linux or Windows branches unless
  explicitly requested.
- `dotsync` itself must not make network calls. External tools invoked by a
  user's existing app CLI are acceptable when already part of app behavior.
- The original config-sync commands must not create files outside the
  user-selected sync folder, except for the explicit, consent-based shell rc
  update handled by `shellrc.py` and `cli.py`, and the local-file backup/write
  behavior of an explicitly confirmed `apply`.
- Never create `~/.dotsync`, `~/.config/dotsync`, or any hidden global pointer
  file for application state.
- UI account metadata and usage caches may live only under
  `~/Library/Application Support/DotSync/`.
- Codex credentials for a managed account may live only in that account's
  DotSync-owned `CODEX_HOME` under the Application Support root. Account and
  usage operations must never write the default `~/.codex`, `~/.claude`, or
  `~/.claude.json` profiles.
- Future profile-scoped Claude Keychain behavior remains internal. Public
  Claude account operations stay policy-disabled until explicit Anthropic
  permission is recorded; do not replace them with private OAuth, cookies, or
  direct provider HTTP calls.
- Native Swift code may supervise the Formula backend and render safe DTOs,
  but must never inspect Claude or Codex provider homes.
- Public command names are important: `backup` means local app config to sync
  folder; `apply` means sync folder to local app config. The internal app
  plugin methods still use `sync_from` and `sync_to`.
- `apply` must back up local files before overwriting. `backup` does not back
  up the sync folder.

## App Plugin Pattern

Simple file-based apps should usually only implement:

- `name`
- `description`
- `is_present_locally()`
- `tracked_files(target_dir) -> list[FilePair]`

The base `App` implementation handles default `sync_from`, `sync_to`, and
`status` from `tracked_files()`.

Only override sync methods for app-specific behavior such as external
processes, live exports, plugin replay, or non-file state.

For external commands, use `self._run_external(cmd, desc=..., fail_mode=...)`.
Use `fail_mode="warn"` for best-effort behavior and `fail_mode="raise"` when
the app sync should abort.

When adding an app:

1. Add `lib/dotsync/apps/<name>.py`.
2. Register the class in `APP_CLASSES` in `lib/dotsync/apps/__init__.py`.
3. Add focused tests under `tests/apps/test_<name>.py`.
4. Add or update round-trip coverage in `tests/integration/test_roundtrip.py`
   when sync safety is relevant.
5. Update `README.md` in both English and Korean sections.

See `docs/adding-an-app.md` for the detailed checklist.

## Development and Verification Workflow

Do not use TDD for application development unless the user explicitly asks for
it. Do not add automated tests by default.

Work in short user-visible slices:

1. Implement only the requested change.
2. Run the minimum build, syntax, or packaging checks needed to produce a usable
   artifact.
3. Publish and reinstall the application so the user can inspect the real
   installed build.
4. Use the user's feedback to choose the next small change.

Do not treat a large passing test count as proof that the installed GUI works.
Actual installed-app behavior and user acceptance are the completion criteria.

## Documentation Expectations

Update `README.md` whenever user-visible behavior changes, including:

- CLI commands or options
- output wording or status states
- supported app list
- config schema
- install or release behavior

The README has English and Korean sections. Keep them in parity; do not update
only one language.

## Release Notes

Local `DotSync.app` builds are unsigned development artifacts only. They must
not be published, described as Cask-ready, or used to generate a public Cask.
A public Cask change requires the real universal archive to pass Developer ID
signing, notarization, Gatekeeper verification, and checksum calculation. Never
invent or bypass those results.

Release flow:

- bump version strings
- run tests
- commit and push
- tag and create a GitHub release
- compute the real tarball sha256
- patch `Formula/dotsync.rb`

Never guess the formula `sha256`. It must be computed from the actual GitHub
release tarball after the release exists.

Before Homebrew-facing changes, validate locally when possible:

```bash
brew install --build-from-source ./Formula/dotsync.rb
brew test dotsync
```

## Local Style

- Prefer small, explicit functions and dataclasses over broad abstractions.
- Keep app-specific config inside `cfg.app_options[<app_name>]` and let each
  app parse its own options in `from_config`.
- Preserve the existing terminal tone and glyph vocabulary in UI output.
- Respect `NO_COLOR=1` in output paths.
- Keep command behavior idempotent where user files are touched.
- Do not silently swallow partial failures; surface warnings through the
  app warning channel and CLI summaries.
