# AGENTS.md

This file gives Codex persistent project guidance for this repository.

## Repository Identity

This repository is the `changja88/homebrew-dotsync` Homebrew tap. It contains
two closely related deliverables:

- `dotsync`, a Python CLI under `lib/dotsync/` with entry points at
  `bin/dotsync` and `dotsync.cli:main`.
- `Formula/dotsync.rb`, the Homebrew formula used by
  `brew install changja88/dotsync/dotsync`.

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

- `lib/dotsync/cli.py` owns argparse command dispatch for `init`, `config`,
  `apps`, `status`, `push`, `pull`, and `account`. A bare `dotsync` prints the
  help.
- `lib/dotsync/config.py` owns sync-folder discovery and `dotsync.toml`
  persistence. Config lives only at `<sync folder>/dotsync.toml`.
- `lib/dotsync/shellrc.py` owns shell rc detection and idempotent
  `DOTSYNC_DIR` export insertion/update logic.
- `lib/dotsync/accounts.py` owns Claude Code account switching
  (`dotsync account`). Saved logins are Claude Code config folders under
  `~/.claude-accounts/<name>`; `use` copies one into Claude's default Keychain
  entry and `~/.claude.json` after handing the login in use back to its own
  account. A display label lives in `<name>/.dotsync-account.json`; commands
  that change accounts or run Claude hold `~/.claude-accounts/.lock`.
  `lib/dotsync/claude_usage.py` asks Claude Code for one login's usage with
  the undocumented `get_usage` control request (zero tokens). `--json` output
  and its error codes are the contract the dotsync app depends on — change
  them together with the app. Accounts are separate from syncing: no sync
  folder, no push/pull.
- `macos/` is dotsync.app (SwiftUI) and its three widgets ("Claude 계정",
  "지금 사용 중", "Claude 계정 목록"). `macos/DotsyncKit` holds
  everything testable (`swift test --package-path macos/DotsyncKit`): models
  of the `--json` output, usage.json in the app group `GR53VV7ZD2.dotsync`,
  the merge rule, texts, the dotsync runner. The app runs
  `dotsync account … --json -- <args>`; the widget only reads usage.json and
  its buttons are `AudioPlaybackIntent`s so they run in the app process.
  `macos/project.yml` is XcodeGen (the `.xcodeproj` is generated, not
  committed). `make app` builds, signs (Developer ID, hardened runtime, no
  notarization) and zips; `make release` ships the Formula and the Cask
  `Casks/dotsync-app.rb` from one tag. For a local build, point the app at
  this checkout with `defaults write com.changja88.dotsync cliPath
  "$PWD/macos/dev/dotsync-dev"`. Replacing the app leaves the old widget
  process running, and macOS then refuses what it draws (blank widgets): the
  Cask stops it after each install (`terminate_process "dotsyncWidget"`);
  after a local install run `killall dotsyncWidget` yourself.
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
- The tool must not create files outside the user-selected sync folder, except
  for the explicit, consent-based shell rc update handled by `shellrc.py` and
  `cli.py`, and the `~/.claude-accounts/<name>` folders an explicit
  `dotsync account login` creates for Claude Code.
- Claude tokens never go on a command line or into output: pass them to
  `security` on stdin, and read a Keychain entry back after writing it.
- Never create `~/.dotsync`, `~/.config/dotsync`, or any hidden global pointer
  file for application state.
- Public command names are important: `push` means local app config to sync
  folder; `pull` means sync folder to local app config. The internal app
  plugin methods still use `sync_from` and `sync_to(target_dir)`.
- dotsync keeps no backup copies in either direction. Every `push`/`pull`
  previews the plan and asks for confirmation (unless `--yes`/`--dry-run`);
  that preview is the safety net, so keep it accurate.
- `dotsync.toml` holds only `apps` and `[options.<app>]` tables; reject any
  other key under `[options]` instead of ignoring it.

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

## Testing Discipline

This codebase was built test-first. For behavior changes, follow this order:

1. Add or update a failing test.
2. Run the targeted test and confirm it fails for the expected reason.
3. Implement the smallest change that makes it pass.
4. Run the relevant targeted tests.
5. Run the full test suite when the change has shared behavior or release
   impact.

Common commands:

```bash
make test
.venv/bin/python3 -m pytest
.venv/bin/python3 -m pytest tests/test_config.py -v
.venv/bin/python3 -m pytest tests/apps/test_claude.py::test_status_clean -v
PYTHONPATH=lib python3 -m dotsync --help
PYTHONPATH=lib python3 bin/dotsync --help
```

Tests isolate `$HOME`, scrub `DOTSYNC_DIR`, and block accidental
`subprocess.run` calls by default in `tests/conftest.py`. If a test needs an
external command, mock or monkeypatch it explicitly.

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

`make release` (`scripts/release.sh`) runs the whole release, in this order:

- bump version strings (`pyproject.toml`, `lib/dotsync/__init__.py`,
  `Formula/dotsync.rb`, `macos/project.yml`, `Casks/dotsync-app.rb`)
- run the Python and Swift tests
- build, sign and zip dotsync.app; put the zip's sha256 into the Cask
- commit and tag locally, then push the tag only
- compute the real sha256 of the tag's tarball, patch `Formula/dotsync.rb`,
  commit
- create the GitHub release with the app zip (the Cask downloads it)
- push main last

brew reads the tap's main directly, so main must never hold a placeholder
`sha256` or a Cask whose zip isn't uploaded yet — that is why main is pushed
last. Never guess the formula `sha256`. It must be computed from the actual
GitHub tarball of the pushed tag.

Before Homebrew-facing changes, validate locally when possible:

```bash
brew install --build-from-source ./Formula/dotsync.rb
brew test dotsync
brew style Casks/dotsync-app.rb
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
