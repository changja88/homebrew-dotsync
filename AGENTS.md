# AGENTS.md

This file gives Codex persistent project guidance for this repository.

## Repository Identity

This repository is the `changja88/homebrew-dotsync` Homebrew tap. It contains
two closely related deliverables:

- `dotsync`, a Python CLI under `lib/dotsync/` with entry points at
  `bin/dotsync` and `dotsync.cli:main`.
- `Formula/dotsync.rb`, the Homebrew formula used by
  `brew install changja88/dotsync/dotsync`.

`dotsync` is a macOS-only CLI that dotsync.app runs to sync selected app
configuration files between local app locations and one user-chosen sync
folder. It has no terminal use: every command takes `--json` and answers the
app with one JSON object; without `--json` it says so and exits 2.

## Core Architecture

- `lib/dotsync/cli.py` owns argparse command dispatch for `config` (`dir`,
  `apps`), `status`, `push`, `pull`, `account`, and `sessions` — the
  commands dotsync.app runs, and nothing else.
- `lib/dotsync/config.py` owns sync-folder discovery and `dotsync.toml`
  persistence. Config lives only at `<sync folder>/dotsync.toml`; the folder
  is found only through the marked `export DOTSYNC_DIR=…` line in the shell
  rc (`$DOTSYNC_DIR` and the cwd are not read: the app has neither).
- `lib/dotsync/cli_sessions.py` owns the `dotsync sessions` group (`list`,
  `delete`, `recover`, `cleanup`); the logic is in `lib/dotsync/sessions/`:
  `claude.py` (scan, liveness, list), `claude_delete.py` (quarantine-rename
  delete, `recover`), `codex.py` (list, delete through `codex delete`),
  `codex_blockers.py` (who uses `~/.codex`), `codex_keepset.py` (which thread
  ids are live), `codex_cleanup.py` (orphans, dry run, the cleanup run,
  vacuum), `grouping.py` (project grouping), `procs.py` (`ps`/`lsof`, always
  with a timeout), `safety.py` (id validation, `child` path rule), `roots.py`
  (every default location, read at call time), `log.py`, `stop.py`. Session
  commands need no sync folder and never load `dotsync.toml`.
  - Safety: a session id must match the lowercase UUID regex before any
    filesystem call (`invalid_session_id`); a path to delete is a known root
    plus an id-derived name whose parent is exactly that root; no globs;
    unknown means keep (a check that cannot finish makes the item `unknown`
    or skips the cleanup item); every child process has a timeout.
  - JSON contract (the dotsync app depends on it; change both together):
    every command prints exactly one JSON object on stdout, with anything
    an app prints on stderr (`cli_json.emit`). Success exits 0; failure exits 1 with
    `{"error": {"code", "message"}}`. A partial outcome is a success with
    per-item results. Long `sessions … --json` commands write
    `dotsync-progress <done>/<total>` lines, and nothing else, to stderr.
  - Locks (`lib/dotsync/locks.py`): `tool_lock(name, shared=…)` holds
    `~/Library/Application Support/dotsync/locks/<name>.lock` and fails with
    `busy` after 10 s. Names: `claude-sessions` (exclusive for
    `delete claude` and `recover claude`) and `codex` (exclusive for
    `delete codex` and `cleanup codex --yes`; shared for `push`/`pull`/`status`
    of the `codex` app). `~/.claude.json` writers use `accounts.locked()`.
  - Log (`sessions/log.py`): every delete, recover and `cleanup --yes` writes
    `~/Library/Logs/dotsync/sessions-<timestamp>-<pid>.json` (mode 0600, even
    when stopped) with ids, paths and outcomes, never titles or transcript or
    history text. `SIGTERM` (`StopSignal`) stops at the next safe point
    (between sessions, cleanup items, the databases a vacuum rewrites; it
    interrupts a running `VACUUM`, which leaves that database as it was); a
    running child (`codex delete`) is waited for, never signalled.
  - Codex cleanup runs only while nothing uses `~/.codex` (ChatGPT.app, a
    terminal Codex, or any other process holding a file there). It also
    refuses as a whole (`incomplete_scan`, `codex_version`) when the keep-set
    cannot be trusted. The CLI refuses
    with `codex_running` and quits nothing; dotsync.app quits ChatGPT.app
    first and relaunches it, and it refuses while a terminal Codex runs.
  - `.zst` Codex rollouts are read in-process with `compression.zstd`
    (Python 3.14+), not a `zstd` child per file.
  - The Codex list shows every thread no spawn edge points at, with its
    spawn subtree folded in: a sub-agent no thread spawned (Codex's guardian
    reviews) is listed on its own so it can be deleted.
  - `codex delete`s run four at a time. One that stops halfway (it lost a
    database to another, and left its state rows) is tried once more, alone,
    after the others.
  - The cleanup's vacuum rewrites the history and state databases whole
    (`VACUUM`), which keeps their auto_vacuum mode.
- `lib/dotsync/shellrc.py` owns the marked `DOTSYNC_DIR` line: finding it
  (`rc_candidates`, `marked_dir`) and keeping it (`update_shell_rc`, which
  `config dir` calls; with no rc file it starts `~/.bash_profile`, never the
  `~/.zshrc` the zsh app syncs). The marker text
  stays word for word: existing rc files carry it.
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
- `macos/` is dotsync.app (SwiftUI) and its four widgets ("Claude 계정",
  "지금 사용 중", "Claude 계정 목록", "맥 상태"). `macos/DotsyncKit` holds
  everything testable (`swift test --package-path macos/DotsyncKit`): models
  of the `--json` output, usage.json in the app group `GR53VV7ZD2.dotsync`,
  the merge rule, texts, the dotsync runner. The app runs
  `dotsync account … --json -- <args>`; the Claude widgets only read usage.json
  and their buttons are `AudioPlaybackIntent`s so they run in the app process. "맥 상태" needs
  neither: the widget reads CPU, GPU and RAM itself (`SystemReader`) when its
  timeline is reloaded, and its ↻ is a plain `AppIntent`.
  Beside usage.json, claude-hud's `display.externalUsageWritePath` may write
  live-usage.json: the status line's `rate_limits` after each Claude answer.
  When their weekly reset matches the active account's, the widget and the
  window draw it with whichever is later per window: the larger percent in the
  same window (claude-hud's `updated_at` is when it wrote, so an idle session
  rewrites old numbers), or the later window (`UsageMerge.withLive`).
  usage.json itself keeps only what dotsync probed.
  Auto switch (the 자동 교체 menu): DotsyncKit's `AutoSwitchRules` decides
  from live-usage.json (only numbers newer than usage.json's `fetched_at`)
  and picks the next account; `AutoSwitchService` is one wake-up (probe,
  pick, `use`, rest, log), judged on the probe alone. Settings and the log are
  auto-switch.json and auto-switch-log.json beside usage.json; a run changes
  only its own fields (`updateSettings`). The app writes
  `~/Library/LaunchAgents/com.changja88.dotsync.autoswitch.plist` (WatchPaths
  = the app group folder) and `launchctl bootstrap`s it; launchd runs the
  app's own executable with `--auto-switch-check` (`App/Entry.swift`,
  `CheckMode`), which never writes the folder and opens
  `dotsync://auto-switch?why=…` in the app without a window. The Cask boots
  the agent out on uninstall and upgrade (no sudo); the app puts it back at
  launch (`AutoSwitchModel.reconcileAgent`).
  `macos/project.yml` is XcodeGen (the `.xcodeproj` is generated, not
  committed). `make app` builds, signs (Developer ID, hardened runtime, no
  notarization) and zips; `make release` ships the Formula and the Cask
  `Casks/dotsync-app.rb` from one tag. For a local build, point the app at
  this checkout with `defaults write com.changja88.dotsync cliPath
  "$PWD/macos/dev/dotsync-dev"`. Replacing the app leaves the old widget
  process running, and macOS then refuses what it draws (blank widgets): the
  Cask stops it after each install (`terminate_process "dotsyncWidget"`);
  after a local install run `killall dotsyncWidget` yourself.
- `lib/dotsync/apps/base.py` defines the app plugin contract:
  `App`, `AppStatus`, `FilePair`, and `diff_files`.
- `lib/dotsync/apps/__init__.py` is the single source of truth for registered
  apps through `APP_CLASSES`.
- Concrete app modules live in `lib/dotsync/apps/`: `claude`, `codex`,
  `herdr`, `skills`, `ghostty`, `bettertouchtool`, and `zsh`.

## Executor Rules for Sessions

- Real `sessions delete`, `sessions recover`, and `sessions cleanup --yes`
  against the real home (`~/.claude`, `~/.codex`, `/private/tmp/claude-<uid>`,
  `~/Library`) need the user's explicit pick of the sessions and their
  confirmation. Agents never run them on their own.
- Read-only commands may run on the real home: `sessions list`,
  `cleanup --blockers`, `cleanup --dry-run`. Report counts, sizes and states
  only, never session titles or other projects' paths.
- Tests use fake homes only. `tests/sessions/conftest.py` points every root
  from `sessions/roots.py` at `tmp_path`, and `tests/conftest.py` blocks
  `subprocess.run`. A test that would touch a real location is a bug.
- Never quit or relaunch ChatGPT.app, Ghostty, or the herdr server on the
  user's behalf.

## Non-Negotiable Design Rules

- Runtime dependencies must stay stdlib-only. Do not add `click`, `requests`,
  `pydantic`, or similar dependencies. This keeps the Homebrew formula simple.
- Target runtime is Python 3.14+ (`compression.zstd`). Keep `pyproject.toml`,
  `lib/dotsync/__init__.py`, and `Formula/dotsync.rb` aligned when changing
  versions.
- Treat the project as macOS-only. Do not add Linux or Windows branches unless
  explicitly requested.
- `dotsync` itself must not make network calls. External tools invoked by a
  user's existing app CLI are acceptable when already part of app behavior.
- The tool must not create files outside the user-selected sync folder, except
  for the marked shell rc line `config dir` keeps (`shellrc.py`, `cli.py`),
  and the `~/.claude-accounts/<name>` folders an explicit
  `dotsync account login` creates for Claude Code, and the lock files and
  result logs of the session commands (`~/Library/Application Support/dotsync/locks/`,
  `~/Library/Logs/dotsync/`).
- Claude tokens never go on a command line or into output: pass them to
  `security` on stdin, and read a Keychain entry back after writing it.
- Never create `~/.dotsync`, `~/.config/dotsync`, or any hidden global pointer
  file for application state.
- Public command names are important: `push` means local app config to sync
  folder; `pull` means sync folder to local app config. The internal app
  plugin methods still use `sync_from` and `sync_to(target_dir)`.
- dotsync keeps no backup copies in either direction. dotsync.app previews
  every `push`/`pull` (`--dry-run`) before it applies one (`--yes`); that
  preview is the safety net, so keep it accurate.
- `dotsync.toml` holds only `apps` and `[options.<app>]` tables; reject any
  other key under `[options]` instead of ignoring it.

## App Plugin Pattern

Simple file-based apps should usually only implement:

- `name`
- `description`
- `tracked_files(target_dir) -> list[FilePair]`

The base `App` implementation handles default `sync_from`, `sync_to`, and
`status` from `tracked_files()`.

Only override sync methods for app-specific behavior such as external
processes, live exports, plugin replay, or non-file state.

An app with options reads them in `from_config` and gets them when
dotsync.app turns it on from `options_when_turned_on()` (BetterTouchTool's
preset names).

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
PYTHONPATH=lib python3 -m dotsync status --json
```

`tests/conftest.py` gives every test an empty `$HOME` of its own (so none
reads the real `~/.zshrc`; `fake_home` makes it `tmp_path`, `point_at`
points dotsync at a sync folder through the rc line) and blocks accidental
`subprocess.run` calls. If a test needs an external command, mock or
monkeypatch it explicitly.

## Documentation Expectations

Update `README.md` whenever user-visible behavior changes, including:

- what the app does, and the `--json` commands it runs
- status states
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
- Keep command behavior idempotent where user files are touched.
- Do not silently swallow partial failures; surface warnings through
  `App.warnings`, which each push/pull result carries.
