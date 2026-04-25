# dotsync

Sync macOS app configs (Claude Code, Ghostty, BetterTouchTool, zsh) bidirectionally with a folder of your choice. The folder is just a folder — you can git-track it, sync it via iCloud Drive / Dropbox, or leave it local. dotsync doesn't care.

## Install

```bash
brew install changja88/dotsync/dotsync
```

## Quickstart

```bash
# 1. Initialize: pick a folder + which apps to track
dotsync init

# 2. Pull current local configs into the folder
dotsync from --all

# 3. (optional) git init the folder, push to GitHub for backup
cd <your-folder> && git init && git add . && git commit -m "init" && git push

# 4. On a new machine, install dotsync, clone your folder, then push configs to local apps:
dotsync init --dir ~/my-configs --apps claude,ghostty,bettertouchtool,zsh --btt-preset Master_bt --yes
dotsync to --all
```

## Commands

| Command | Purpose |
|---|---|
| `dotsync init` | interactive setup; writes `~/.config/dotsync/config.toml` |
| `dotsync config dir <path>` | change sync folder |
| `dotsync config apps <a,b,c>` | change tracked apps |
| `dotsync config btt-preset <name>` | change BetterTouchTool preset name |
| `dotsync config show` | print current config |
| `dotsync apps` | list supported apps |
| `dotsync status` | report sync state per app (sha256 diff) |
| `dotsync from <app>` / `dotsync from --all` | local → folder |
| `dotsync to <app>` / `dotsync to --all` | folder → local (with backup) |

## Supported apps (v0.1)

| App | What's synced |
|---|---|
| `claude` | `~/.claude/settings.json`, plugins (installed + marketplaces + per-plugin config), MCP servers from `~/.claude.json`. `dotsync to claude` auto-restores missing plugins via `claude plugin install --scope user` and re-disables any plugin marked `false` in `enabledPlugins`. |
| `ghostty` | `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty` |
| `bettertouchtool` | `<preset>.bttpreset` via osascript export/import. Preset name is configurable (`bettertouchtool_preset` in config; defaults to `Master_bt`). |
| `zsh` | `~/.zshrc` |

## Folder layout (your sync folder)

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

## Backups

Every `dotsync to ...` writes a timestamped snapshot of the local files it's about to overwrite to:

```
~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/...
```

`backup_keep = 10` (default) keeps the 10 most recent sessions; older ones are pruned.

## Status

`dotsync status` compares each tracked file by sha256 and reports one of:

- `clean` — local and stored copies are byte-identical
- `dirty` — every file present, but at least one differs
- `missing` — at least one expected file is absent on either side
- `unknown` — the app does not implement status (BetterTouchTool, since exporting via osascript is too expensive for a status check)

## Migration from Make-based dotfiles

If you currently have a `dotfiles` repo with `make claude-sync-from` etc., your `settings/` directory layout is already compatible. Just point dotsync at it:

```bash
dotsync init --dir ~/Desktop/dotfiles/settings --apps claude,ghostty,bettertouchtool,zsh --yes
dotsync from --all   # refresh from current local state
```

You can then remove the `Makefile` and `make/*.mk` files at your leisure.

## Privacy / Security

- dotsync makes **no network calls** of its own. It only reads/writes local files and shells out to `claude` (for plugin restore) and `osascript` (for BTT).
- Your sync folder may contain personal data — be deliberate about pushing it to a public git remote.
- The Homebrew formula fetches a signed (sha256) tarball from this repo's GitHub release.

## License

MIT
