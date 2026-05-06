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

## Core Architecture

- `lib/dotsync/cli.py` owns argparse command dispatch for `welcome`, `init`,
  `config`, `apps`, `status`, `from`, and `to`.
- `lib/dotsync/config.py` owns sync-folder discovery and `dotsync.toml`
  persistence. Config lives only at `<sync folder>/dotsync.toml`.
- `lib/dotsync/backup.py` creates `to` backups inside the sync folder, normally
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
- The tool must not create files outside the user-selected sync folder, except
  for the explicit, consent-based shell rc update handled by `shellrc.py` and
  `cli.py`.
- Never create `~/.dotsync`, `~/.config/dotsync`, or any hidden global pointer
  file for application state.
- Direction names are important: `from` means local app config to sync folder;
  `to` means sync folder to local app config.
- `to` must back up local files before overwriting. `from` does not back up the
  sync folder.

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
