# dotsync

> Sync macOS app configs (Claude Code, Ghostty, BetterTouchTool, zsh) bidirectionally with a folder of your choice.

The folder is just a folder. You can `git init` it, sync it through iCloud Drive / Dropbox, or leave it local. dotsync doesn't care — it only copies files in and out of well-known app config locations.

---

## Why dotsync?

If you've ever set up a new Mac and lost an evening hand-copying terminal configs, restoring Claude plugins one by one, or trying to remember which `defaults` keys you tweaked — that's the problem dotsync solves.

- **Tool and data are separated.** dotsync (this repo) is the tool. Your configs (your repo / your iCloud folder) are the data.
- **No vendor lock-in.** Files on disk, in their original formats. Stop using dotsync any time and your configs are still right there.
- **Stdlib only.** Single Homebrew dependency: `python@3.12`. No `pip install` step, no virtualenv, no transitive dependencies.

---

## Requirements

- macOS (Apple Silicon or Intel)
- Homebrew

The Homebrew formula installs `python@3.12` automatically.

---

## Install

```bash
brew install changja88/dotsync/dotsync
```

Verify:

```bash
dotsync --version
# → dotsync 0.1.0
```

---

## Quickstart

### Scenario A — first time on your main machine

```bash
# 1. Pick a folder + which apps to track
dotsync init
# (interactive: asks for folder path, app list, BTT preset name if applicable)

# 2. Pull your current local configs into the folder
dotsync from --all

# 3. (optional) Track the folder with git
cd <your-folder>
git init && git add . && git commit -m "init: dotsync snapshot"
git remote add origin git@github.com:you/my-configs.git
git push -u origin main
```

### Scenario B — restoring on a new Mac

```bash
brew install changja88/dotsync/dotsync

# Clone (or otherwise materialize) your sync folder
git clone git@github.com:you/my-configs.git ~/my-configs

# Tell dotsync where it is, then push to local apps
dotsync init \
  --dir ~/my-configs \
  --apps claude,ghostty,bettertouchtool,zsh \
  --btt-preset Master_bt \
  --yes
dotsync to --all
```

### Scenario C — daily workflow

```bash
# After tweaking your local configs:
dotsync from --all          # pull local changes into the folder
cd <your-folder> && git diff   # review what changed
git commit -am "tweak font / add plugin / ..." && git push

# After pulling someone else's (or your other machine's) changes:
dotsync to --all            # push folder contents back into local apps
                            # (a backup is taken automatically; see below)
```

---

## Commands

| Command | Description |
|---|---|
| `dotsync init` | Interactive setup. Writes `~/.config/dotsync/config.toml`. |
| `dotsync init --dir <path> --apps <a,b,c> [--btt-preset <name>] --yes` | Non-interactive setup. |
| `dotsync config show` | Print current config. |
| `dotsync config dir <path>` | Change sync folder. |
| `dotsync config apps <a,b,c>` | Change tracked apps. |
| `dotsync config btt-preset <name>` | Change BetterTouchTool preset name. |
| `dotsync apps` | List supported apps + descriptions. |
| `dotsync status` | Per-app diff state (sha256 comparison). |
| `dotsync from <app>` | Local app config → folder (one app). |
| `dotsync from --all` | Local app configs → folder (all tracked apps). |
| `dotsync to <app>` | Folder → local app config (one app, with backup). |
| `dotsync to --all` | Folder → local app configs (all tracked apps, with backup). |
| `dotsync --version` | Print version. |
| `dotsync --help` | Show top-level help. Use `dotsync <cmd> --help` for subcommand help. |

### Mental model

| Direction | Command | Backup taken? |
|---|---|---|
| Local apps → folder | `dotsync from ...` | No (the folder is your responsibility — git, iCloud, etc.) |
| Folder → local apps | `dotsync to ...` | Yes (timestamped under `~/.local/share/dotsync/backups/`) |

---

## Configuration

`~/.config/dotsync/config.toml` (or `$XDG_CONFIG_HOME/dotsync/config.toml` if set):

```toml
# Absolute path to your sync folder
dir = "/Users/you/my-configs"

# Which apps to track (subset of supported apps)
apps = ["claude", "ghostty", "bettertouchtool", "zsh"]

[options]
# Where backups go (default shown)
backup_dir = "~/.local/share/dotsync/backups"

# How many backup sessions to keep (0 = unlimited)
backup_keep = 10

# BetterTouchTool preset name (must match a preset that exists in BTT)
bettertouchtool_preset = "Master_bt"
```

You can edit this file by hand or use the `dotsync config ...` subcommands.

---

## Supported apps

| App | Local source(s) | Folder layout under `<dir>/<app>/` | Notes |
|---|---|---|---|
| `claude` | `~/.claude/settings.json`, `~/.claude/plugins/installed_plugins.json`, `~/.claude/plugins/known_marketplaces.json`, `~/.claude/plugins/<plugin>/config.json`, `mcpServers` field in `~/.claude.json` | `settings.json`, `mcp-servers.json`, `plugins/installed_plugins.json`, `plugins/known_marketplaces.json`, `plugins/<plugin>/config.json` | `dotsync to claude` shells out to `claude plugin marketplace add --scope user` and `claude plugin install --scope user` to restore any missing plugin caches, then re-disables any plugin marked `false` in `enabledPlugins`. |
| `ghostty` | `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty` | `config.ghostty` | One file in, one file out. |
| `bettertouchtool` | the live preset inside BTT (named via `bettertouchtool_preset`) | `presets/<preset>.bttpreset` | Uses `osascript` to `export_preset` / `import_preset`. BetterTouchTool must be running. |
| `zsh` | `~/.zshrc` | `.zshrc` | One file in, one file out. |

### Folder layout (your sync folder)

```
<your-folder>/
├── claude/
│   ├── settings.json
│   ├── mcp-servers.json
│   └── plugins/
│       ├── installed_plugins.json
│       ├── known_marketplaces.json
│       └── <plugin-name>/config.json
├── ghostty/
│   └── config.ghostty
├── bettertouchtool/
│   └── presets/<preset>.bttpreset
└── zsh/
    └── .zshrc
```

---

## Backups

Every `dotsync to ...` snapshots the local files it's about to overwrite to:

```
~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/...
```

Restore is a manual `cp` — there's no `dotsync restore`. The backup directory mirrors the folder layout above, so it's straightforward.

`backup_keep` (default 10) prunes oldest sessions; set to `0` to keep all.

---

## Status

`dotsync status` compares each tracked file by sha256 and reports one of:

| State | Meaning |
|---|---|
| `clean` | Local and stored copies are byte-identical. |
| `dirty` | All files present, but at least one differs (details list which). |
| `missing` | At least one expected file is absent on either side. |
| `unknown` | The app does not implement status. (Currently only `bettertouchtool` — exporting via osascript is too expensive to do on every status check.) |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `config not found at ... Run dotsync init first.` | Run `dotsync init` (or `dotsync init --dir ... --apps ... --yes` for non-interactive). |
| `~/.zshrc 없음` / `config.ghostty 미존재` etc. on `from` | The local app config doesn't exist yet — open the app once to generate it, or skip the app in your tracked list. |
| `osascript failed ... Is BetterTouchTool running?` | Launch BetterTouchTool, then re-run. AppleScript can't talk to BTT if it's not running. |
| `claude: command not found` during `to claude` | Install Claude Code CLI, or remove `claude` from your tracked apps. dotsync still syncs the JSON files; only auto-restore needs the `claude` binary. |
| Plugin re-enabled after `to claude` even though I disabled it | Make sure the plugin is marked `false` under `enabledPlugins` in the **stored** `settings.json` (i.e. the one inside your sync folder). dotsync re-runs `claude plugin disable --scope user` for any plugin in that map with value `false`. |
| Want to use a different python | dotsync's Homebrew formula pins shebang to `python@3.12`. If you build from source (`PYTHONPATH=lib python3 bin/dotsync`), make sure your `python3` is ≥3.12. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Bad argument (e.g. unknown app, missing `--dir`) |
| `3` | Config missing / invalid (`ConfigError`) |
| `4` | Expected file not found (`FileNotFoundError`) |
| `5` | Runtime error (e.g. osascript / subprocess failure) |

---

## Migrating from a Make-based dotfiles repo

If you're moving from a `Makefile` + `make claude-sync-from` setup, your existing `settings/` directory layout is already compatible with dotsync's folder layout. Point dotsync at it:

```bash
dotsync init \
  --dir ~/Desktop/dotfiles/settings \
  --apps claude,ghostty,bettertouchtool,zsh \
  --btt-preset <your-preset> \
  --yes
dotsync from --all   # refresh from current local state
```

You can then delete the `Makefile` and `make/*.mk` files at your leisure. dotsync provides the same operations (`-sync-from` / `-sync-to`) with the same on-disk shapes.

---

## Privacy / Security

- dotsync makes **no network calls of its own.** It reads/writes local files and shells out to `claude` (for plugin restore) and `osascript` (for BTT). Both are local operations as far as dotsync is concerned.
- Your sync folder may contain personal data — `mcpServers` configs can include API keys, `.zshrc` may include `export FOO=...` secrets. Be deliberate about pushing it to a public git remote.
- The Homebrew formula fetches a sha256-pinned tarball from this repo's GitHub release. It does not run any post-install hooks beyond the standard Homebrew install steps.

---

## Development

This repo is the Homebrew tap **and** the source of truth for the `dotsync` CLI.

```bash
# Run from source without installing
PYTHONPATH=lib python3 -m dotsync --help

# Run the test suite (pytest configured in pyproject.toml)
python3 -m pytest

# Single test file / single test
python3 -m pytest tests/apps/test_claude.py -v
python3 -m pytest tests/apps/test_claude.py::test_status_clean -v

# Editable install (creates a `dotsync` entry-point in your venv)
pip install -e .

# Validate the formula locally before tagging
brew install --build-from-source ./Formula/dotsync.rb
brew test dotsync
```

Project layout:

```
homebrew-dotsync/
├── bin/dotsync                 # entry script (shebang pinned by formula)
├── lib/dotsync/                # the package
│   ├── cli.py                  # argparse dispatch
│   ├── config.py               # ~/.config/dotsync/config.toml read/write
│   ├── backup.py               # timestamped backups + rotation
│   ├── ui.py                   # ANSI output (honors NO_COLOR)
│   └── apps/                   # one module per supported app
├── tests/                      # pytest, stdlib only
├── Formula/dotsync.rb          # Homebrew formula
└── docs/superpowers/
    ├── specs/                  # design docs (source of truth)
    └── plans/                  # TDD-shaped implementation plans
```

See [`docs/superpowers/specs/2026-04-25-dotsync-design.md`](docs/superpowers/specs/2026-04-25-dotsync-design.md) for the full design rationale and [`docs/superpowers/plans/2026-04-25-dotsync-implementation.md`](docs/superpowers/plans/2026-04-25-dotsync-implementation.md) for the task-by-task implementation plan.

---

## License

MIT — see [LICENSE](LICENSE).
