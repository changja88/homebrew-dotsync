# dotsync

A CLI that consolidates your macOS app configs into **one folder of your choice**, with two-way sync · macOS 앱 설정을 **사용자가 지정한 한 폴더**에서 관리하는 양방향 sync CLI

> **macOS only · Python 3.12+**

---

## English

### Purpose

dotsync consolidates your macOS app configs (Claude Code, Codex CLI, Herdr, agent skills from `npx skills`, Ghostty, BetterTouchTool, zsh) into **one folder of your choice** and keeps it in two-way sync with the apps. That folder can be anywhere — a fresh directory like `~/my-configs`, or a folder you already track in git or sync via iCloud Drive. Tool (dotsync) and data (the folder) are separated, so setting up a new Mac is just a matter of bringing the folder along.

### Install

```bash
brew install changja88/dotsync/dotsync
dotsync             # bare command prints the help (same as dotsync --help)
dotsync --version   # print installed version
```

If Python 3.12 or 3.13 already exists at a canonical path
(`/opt/homebrew/bin/python3.{12,13}`, `/usr/local/bin/python3.{12,13}`,
or `/Library/Frameworks/Python.framework/Versions/3.{12,13}/...`),
dotsync reuses it — no duplicate install. Otherwise Homebrew pulls in
`python@3.12` as a dependency.

> Note: Pythons installed via pyenv / uv are at non-canonical paths and
> won't be auto-detected — Homebrew will still install its own
> `python@3.12`. Functionality is unaffected, but the duplicate install
> is not avoided.

> Always start with **`dotsync init`** — it picks the sync folder. After that, `push` / `pull` work from anywhere.

### Usage

#### 1. One-time setup — pick a sync folder and the apps to track

`dotsync init` is a two-step wizard.

**Step 1 — Sync folder.** Type the folder path. Bare Enter accepts the default `~/Desktop/dotsync_config`.

**Step 2 — Pick apps to track.** An arrow-key picker opens immediately. Apps installed on this machine come pre-checked (`installed` hint); the rest start unchecked (`not installed`). Toggle with the keys, Enter to confirm.

```bash
dotsync init
# ▶ Step 1 — Sync folder
# ? sync folder (absolute path) [/Users/you/Desktop/dotsync_config] › ⏎
# ✔ folder ready → /Users/you/Desktop/dotsync_config
#
# ▶ Step 2 — Pick apps to track
#   Pick apps to track   ↑/↓ move · space toggle · enter submit
#
#   ▸ [x] claude              installed
#     [x] codex               installed
#     [x] herdr               installed
#     [x] skills              installed
#     [x] ghostty             installed
#     [x] bettertouchtool     installed · 2 presets
#     [x] zsh                 installed
#
# ✔ tracked: claude · codex · herdr · skills · ghostty · bettertouchtool · zsh
# ✔ BetterTouchTool presets = Master_bt, Mini_bt   (auto-detected)
# ✔ config saved → /Users/you/Desktop/dotsync_config/dotsync.toml
```

Row colors flag misconfigured states at a glance:

- `[x] + installed` → default (healthy)
- `[x] + not installed` → red dim ("cleanup candidate")
- `[ ] + installed` → yellow dim ("add candidate")
- `[ ] + not installed` → dim

**BetterTouchTool presets are auto-discovered and all tracked.** dotsync reads BTT's internal SQLite store and writes every registered preset name into `presets` under `[options.bettertouchtool]` (e.g. `["Master_bt", "Mini_bt"]`). No per-preset prompt. `--yes` mode skips discovery in favor of a deterministic default (`["Master_bt"]`) — useful for CI.

Non-interactive (scripts / new-machine bootstrap):

```bash
# Both --dir and --apps can be omitted:
#   --dir defaults to ~/Desktop/dotsync_config
#   --apps defaults to all auto-detected apps
dotsync init --yes

# Or specify explicitly (BTT presets are comma-separated)
dotsync init --dir ~/my-configs --apps claude,zsh --btt-presets Master_bt,Mini_bt --yes

# No post-init hints (for first-boot scripts)
dotsync init --yes --no-hints

# Skip the shell-rc auto-write (e.g. you manage rc files via your dotfiles repo)
dotsync init --yes --no-shell-init
```

**dotsync creates no files or directories anywhere on your machine outside the sync folder you chose.** All settings live in `<sync folder>/dotsync.toml`. The single exception — opt-in by design — is the one-line `export DOTSYNC_DIR="…"` that `init` writes into your shell rc (see below); pass `--no-shell-init` to disable.

#### How does dotsync find the sync folder?

Either of these works:

1. The `DOTSYNC_DIR` environment variable holds an absolute path. `init` automatically appends this line to your shell rc (`~/.zshrc` for zsh, `~/.bash_profile` for bash) on your behalf — interactive mode asks first, `--yes` writes silently. Re-running `init` with a different `--dir` updates the line in place; identical lines are left untouched. Skip the auto-write with `--no-shell-init`. Resulting line:
   ```bash
   export DOTSYNC_DIR="/Users/you/my-configs"
   ```
2. Or, run `dotsync` from inside the sync folder (or any subdirectory) — it walks upward looking for `dotsync.toml` (git-style).

#### Restoring on a new machine

If the folder already contains a `dotsync.toml`, `init` adopts it as-is. Passing `--apps` or `--btt-presets` overrides this and rewrites the file with the new values.

```bash
git clone git@github.com:you/my-configs.git ~/my-configs
export DOTSYNC_DIR="$HOME/my-configs"   # once (add to your shell rc)
dotsync init --dir ~/my-configs --yes   # reuses existing dotsync.toml as-is
dotsync pull --all
```

#### 2. Local app configs → folder (take a snapshot)

`dotsync push` previews the sync-folder changes before copying anything. Press `y`
or type `yes` to apply the plan. Use `--dry-run` to preview only, or `--yes`
for automation.

```bash
dotsync push --all --dry-run  # preview folder changes only
dotsync push --all            # interactive (asks y/N)
dotsync push --all --yes      # automation (no prompt)
```

The summary separates apps that changed (`✓ changed`) from apps that were
already in sync (`· unchanged`).

```
╭──────────────────────────────────────────────────────────────────╮
│ dotsync push                                                     │
│ 5 apps  →  /Users/you/Desktop/dotsync_config                     │
╰──────────────────────────────────────────────────────────────────╯
... (per-app sections) ...
╭──────────────────────────────────────────────────────────────────╮
│ ✓ changed    ghostty · bettertouchtool                           │
│ · unchanged  claude · codex · zsh                                │
│ 5 ok  ·  0 warn  ·  0 error  ·  2.3s                             │
╰──────────────────────────────────────────────────────────────────╯
```

Then commit the folder to git or let iCloud sync it.

#### 3. Folder → local apps (restore on another machine)

`dotsync pull` previews local-machine changes before overwriting your local configs.
Press `y` or type `yes` to apply the plan. Use `--dry-run` to preview only,
or `--yes` for automation. dotsync keeps no backup copies: the preview and
the confirmation are the safety net, so read the plan before you confirm.

```bash
dotsync pull --all --dry-run     # preview only
dotsync pull --all                # interactive (asks y/N)
dotsync pull --all --yes          # automation (no prompt)
```

The summary box separates apps that actually changed (`✓ changed`) from apps that were already in sync (`· unchanged`).

```
╭──────────────────────────────────────────────────────────────────╮
│ ✓ changed    ghostty · bettertouchtool                           │
│ · unchanged  claude · codex · zsh                                │
│ 5 ok  ·  0 warn  ·  0 error  ·  3.1s                             │
╰──────────────────────────────────────────────────────────────────╯
```

The preview annotates each file-copy entry with line counts (`+added −removed`) — binary files show a size summary instead (e.g. `binary · 50B → 200B`). JSON/TOML files additionally list their changed top-level keys (e.g. `+113 −1 · model, hooks`), and directories list the changed file names.

Type `d` at the confirmation prompt to inspect the full diff before
applying (capped at 200 lines per file; `y`/`n` behave as before):

    ? Apply these changes to the sync folder? [y/N/d] › d

`dotsync status` shows the same per-file summaries under any app that
differs.

**Claude restoration goes beyond file copy.** dotsync replays the recorded marketplaces (`claude plugin marketplace add`) and runs `claude plugin install --scope user` for every plugin in `installed_plugins.json`, then re-applies the `enabledPlugins` map so disabled plugins stay disabled. If the `claude` CLI isn't installed, plugin replay is skipped (logged as a warning) and the file copy still succeeds. dotsync also mirrors your user-level global rules — `~/.claude/CLAUDE.md` and the `commands/`, `agents/`, `skills/`, `output-styles/` directories — so personal slash commands, subagents, and skills follow you across machines. `skills/synced/` (skills Claude Code syncs from your claude.ai account) and `skills/.trash/` (old versions Claude Code moved aside) are managed by Claude Code itself, so they are never stored (`push` removes any copy already in the sync folder), and `pull` leaves the local copies in place.

**Skills another tool installs can be left to that tool.** Some tools install their own skill and rewrite it on upgrade (graphify does this for both Claude and Codex). Syncing such a skill fights the tool, so list it in `dotsync.toml`:

```toml
[options.claude]
skills_ignore = ["graphify"]

[options.codex]
skills_ignore = ["graphify"]
```

Listed skill directories are treated like `skills/synced/` above: `push` removes the stored copy, `pull` never touches the local one, and `status` ignores them. Reinstall them on a new machine with the tool itself (for example `graphify install --platform claude`). Each entry must be a top-level directory name under `skills/`; anything else is rejected as a config error.

dotsync also excludes dynamic local Serena MCP entries from Claude's `mcpServers` sync. Other MCP servers are still synced normally.

**Codex sync mirrors user-authored global settings.** dotsync copies `~/.codex/config.toml`, optional instruction/config files (`AGENTS.md`, `AGENTS.override.md`, `hooks.json`, `requirements.toml`), and the user-managed `rules/` and `skills/` directories. `dotsync push codex` converges the sync folder to the current local managed set: if one of those optional files or managed directories is absent locally, the stored copy is removed from the sync folder. It skips generated or sensitive state such as `auth.json`, history, sessions, logs, sqlite state, caches, system skills, plugin caches, memories, and vendor imports; `skills/.system` is intentionally not synced.

**Codex plugins are recorded, not copied.** Codex rewrites the `[marketplaces.*]` and `[plugins.*]` tables of `config.toml` by itself (local paths of the marketplaces it bundles, `last_updated` / `last_revision`), so dotsync stores `config.toml` without them. Instead, `push` asks `codex plugin list --json` what is installed and writes `codex/plugins.json`:

```json
{
  "marketplaces": [
    {"name": "my-market", "source": "https://github.com/me/my-market.git", "ref": "main"}
  ],
  "plugins": ["mine@my-market", "superpowers@openai-curated-remote"]
}
```

- Recorded: the enabled plugins you installed, and the marketplaces you added (with `ref` and `sparse`).
- Left to Codex: plugins it installs by default (`INSTALLED_BY_DEFAULT`), plugins from marketplaces it keeps under its own directories (`~/.codex`, `~/.cache/codex-runtimes`, such as `openai-bundled`), and those marketplaces. Disabled plugins are not recorded.

`pull` keeps the local marketplace/plugin tables and replaces the rest of `config.toml` with the stored settings. It then adds each recorded marketplace that is missing (`codex plugin marketplace add <source>`, with `--ref` / repeated `--sparse`) and installs each recorded plugin that is missing (`codex plugin add <plugin@marketplace> --json`). Whatever is already present is left alone, so running `pull` again installs nothing. If a marketplace cannot be added, its plugins are skipped with a warning; on a fresh machine, start Codex once so its own marketplaces exist, then pull again. If the `codex` CLI is missing or prints something unexpected, files still sync, `push` keeps the existing `plugins.json`, and plugin restore is skipped with a warning. `status` and the previews compare `config.toml` without those tables and compare `plugins.json` with what is installed.

dotsync intentionally excludes dynamic local Serena MCP URLs from Codex config sync. Serena ports are per-project runtime state, so a copied `127.0.0.1:<port>` URL is treated as machine-local state rather than user-authored config.

**Herdr sync tracks only `~/.config/herdr/config.toml`.** Session and runtime state such as `session*.json`, plugin registry state, logs, sockets, and lock files are intentionally excluded. Existing dotsync users can enable Herdr with `dotsync apps` (or include `herdr` when replacing the list with `dotsync config apps ...`); existing tracked-app selections are never changed automatically.

**Skills sync records what `npx skills add -g` installed, not the files.** `push` reads `~/.agents/.skill-lock.json` and writes `skills/skills.json` with each skill's source and the managed agents (`claude-code`, `codex`) whose `~/.claude/skills/<name>` / `~/.codex/skills/<name>` link points at it — the lock file itself is never copied into the sync folder, since it can hold a GitHub token. `pull` runs `npx -y skills add <source> --global --skill <name> --agent <agent> --yes` for every recorded skill that is missing locally; failures, a missing `npx`, and non-GitHub sources are reported as warnings. Skills installed with `--copy` are plain directories and sync as files through the claude/codex apps instead.

**BetterTouchTool must be running** for `push` / `pull` / `status` — dotsync drives BTT via `osascript`. If BTT isn't running, `status` reports `unknown` and `push` / `pull` raise an error. Preset names are treated as literal BTT names; empty names, path separators, quotes, and control characters are rejected before any AppleScript is generated.

#### 4. Check sync state (per-file sha256 diff)

Status lines use color + glyph; for 'dirty' rows, the direction hint shows which side is newer.

```bash
$ dotsync status
▸ status                              ~/dotsync_config

  ✓ zsh              clean
  ✓ codex            clean
  ⚠ ghostty          dirty  local-newer — config.ghostty
    ⚠ update         config.ghostty  — +2 −1
  ✗ claude           missing
  · bettertouchtool  unknown — BTT not running
```

Each dirty app gets an indented change-summary line per differing file,
the same annotation shown in `push`/`pull` previews.

Legend:

- `✓ clean` — local and stored bytes match (sha256 equal). Claude's `installed_plugins.json` and `known_marketplaces.json` are compared the way `push`/`pull` previews compare them, ignoring runtime metadata Claude Code rewrites on its own (install paths, versions, timestamps, commit SHAs)
- `⚠ dirty` — differ; direction is `local-newer`, `folder-newer`, or `diverged` (mix of both)
- `✗ missing` — at least one side is absent
- `· unknown` — couldn't determine (e.g., BTT not running)

#### 5. Switch Claude Code accounts

Keep several claude.ai logins and switch the one Claude Code uses — the same effect as `/login`, without the browser each time. Running sessions follow the switch the way they follow a `/login`.

```bash
dotsync account login work      # save the account the browser approves as "work" (once per account)
dotsync account use work        # make Claude use it
dotsync account list            # saved accounts; ● marks the one in use
dotsync account usage           # each account's 5-hour and weekly use and reset time
dotsync account rename work "Work Max"   # change the name shown for it
dotsync account remove work     # log it out and delete it
```

- Claude Code saves each account itself: `login` runs `claude auth login` with `CLAUDE_CONFIG_DIR=~/.claude-accounts/<name>`, so the tokens stay in that folder's Keychain entry.
- `use` copies that login into Claude's default Keychain entry and the account details into `~/.claude.json`. It first hands the login in use back to its own account, because Claude refreshes tokens as it runs and a used refresh token stops working.
- Tokens reach `security` on stdin, never on a command line.
- If the login in use isn't saved (the first time, for example), `use` asks before dropping it. Save it first with `dotsync account login <name>`.
- Once you use `dotsync account`, switch with `use` instead of `/login`: `/login` replaces the login in use without handing it back, and that account then needs `dotsync account login` again.
- Accounts are separate from syncing: they need no sync folder, and `push`/`pull` never touch them.
- `usage` asks Claude Code itself (`claude -p` with its `get_usage` request), so it spends no tokens. The account in use is read through Claude's default folder, the others through their own folders, all at once; one slow account (30 s limit) doesn't hold up the rest.
- Every `account` command takes `--json` and then prints exactly one JSON object — the dotsync app reads these. Errors come back as `{"error": {"code": …, "message": …}}` with exit status 1. Commands that change accounts or run Claude wait for each other (`~/.claude-accounts/.lock`).

#### 6. The dotsync app (macOS)

A window and widgets for the same accounts: each account's 5-hour and weekly usage with reset times, and one click to switch.

```bash
brew install --cask changja88/dotsync/dotsync-app   # installs the dotsync CLI too
```

- The app is signed but not notarized: the first time, and after each update, open it once via System Settings → Privacy & Security → "Open Anyway".
- Add a widget from Edit Widgets, on the desktop or in Notification Center:
  - "Claude 계정" (large or extra large): the account in use and the other accounts together.
  - "지금 사용 중" (medium): the account in use alone.
  - "Claude 계정 목록" (large): the other accounts, up to six. Under "지금 사용 중" in Notification Center, the two read as one tall widget.
- On a widget, ↻ refreshes (about 10 s, no tokens) and "사용" switches Claude like `dotsync account use`. The app runs only while you use it — nothing stays in the background.
- The window adds accounts, logs one in again, renames and removes them.
- An install or update stops the widget that was running, so the new one takes over. If a widget still stays blank, open the app once: it reloads the widgets.

#### Change the folder or app list later

`dotsync apps` opens the same picker as init's Step 2. Toggling BTT on re-runs preset discovery and writes the result back to config — no separate command needed.

```bash
dotsync apps                              # picker to change the tracked apps (Enter = keep current)
dotsync config show                       # print current config
dotsync config dir ~/another-folder       # change sync folder
dotsync config apps claude,zsh            # replace the tracked-apps list (for automation)
dotsync config btt-presets MyPreset,Other # replace BTT preset list (comma-separated)
```

`dotsync config show` includes app-specific options such as BTT presets. `dotsync.toml` holds only `apps` and `[options.<app>]` tables; any other key under `[options]` is rejected as a config error. Top-level managed files and directories that are symlinks are rejected instead of followed. Inside mirrored directories such as Claude `skills/` or Codex `rules/`, symlinked entries (for example the links `npx skills add` creates) are skipped with a warning and never read or written through, and `pull` leaves them in place. When the `skills` app is tracked, the links `npx skills add` made are skipped without a warning, since that app records them. `.DS_Store` files and `*.bak` files (copies tools such as graphify keep before rewriting a file) inside mirrored directories are ignored the same way: never stored, never removed.

Picker keys:

- `↑` / `↓` — move
- `space` — toggle the current row
- `enter` — confirm
- `q` / `esc` / `ctrl+c` — cancel (no config change)

In non-TTY environments (CI, piped stdin) it automatically falls back to
sequential per-app y/n prompts.

Supported apps: `claude`, `codex`, `herdr`, `skills`, `ghostty`, `bettertouchtool`, `zsh`

### Adding a new app

See `docs/adding-an-app.md`. A simple file-based app is one module + one line in `apps/__init__.py`'s `APP_CLASSES`. Complex apps (external processes, custom CLI options) extend in place by overriding `App` hooks.

Help: `dotsync --help`, `dotsync <command> --help`. All output respects `NO_COLOR=1`.

---

## 한국어

### 목적

dotsync는 macOS의 앱 설정(Claude Code, Codex CLI, Herdr, `npx skills`로 설치한 agent skill, Ghostty, BetterTouchTool, zsh)을 **사용자가 지정한 단일 폴더**에 모아서 양방향으로 동기화한다. 그 폴더는 어디든 OK — 새 폴더(`~/my-configs`)일 수도 있고, 이미 git이나 iCloud Drive로 관리 중인 폴더일 수도 있다. 도구(dotsync)와 데이터(폴더)를 분리해서, 새 Mac 셋업도 폴더만 옮겨오면 끝난다.

### 설치

```bash
brew install changja88/dotsync/dotsync
dotsync             # 인자 없이 실행하면 도움말 출력 (dotsync --help 와 같음)
dotsync --version   # 설치된 버전 확인
```

Python 3.12 또는 3.13 이 canonical 경로 (`/opt/homebrew/bin/python3.{12,13}`,
`/usr/local/bin/python3.{12,13}`, `/Library/Frameworks/Python.framework/Versions/3.{12,13}/...`)
에 이미 있으면 그것을 그대로 재사용한다 — 중복 설치하지 않는다. 없을 때만 brew 가
`python@3.12` 를 함께 설치한다.

> 참고: pyenv / uv 처럼 비-canonical 경로에 깔린 Python 은 자동 감지되지 않아
> brew 가 자기 `python@3.12` 를 설치한다. 동작은 정상이지만 중복 설치는 발생.

> 첫 단계는 항상 **`dotsync init`** — sync 폴더를 정한다. 그 다음 `push` / `pull`이 동작한다.

### 사용법

#### 1. 처음 한 번 — sync 폴더 정하고 추적할 앱 고르기

`dotsync init` 은 두 단계 wizard 다.

**Step 1 — Sync folder.** 폴더 경로를 입력한다. 그냥 Enter면 default `~/Desktop/dotsync_config` 를 사용한다.

**Step 2 — Pick apps to track.** 화살표 키 picker 가 곧바로 뜬다. 이 머신에 설치된 앱들은 미리 체크돼 있고(`installed` 표시), 미설치 앱은 비어 있다(`not installed`). 키로 토글한 뒤 Enter 로 확정.

```bash
dotsync init
# ▶ Step 1 — Sync folder
# ? sync folder (absolute path) [/Users/you/Desktop/dotsync_config] › ⏎
# ✔ folder ready → /Users/you/Desktop/dotsync_config
#
# ▶ Step 2 — Pick apps to track
#   Pick apps to track   ↑/↓ move · space toggle · enter submit
#
#   ▸ [x] claude              installed
#     [x] codex               installed
#     [x] herdr               installed
#     [x] skills              installed
#     [x] ghostty             installed
#     [x] bettertouchtool     installed · 2 presets
#     [x] zsh                 installed
#
# ✔ tracked: claude · codex · herdr · skills · ghostty · bettertouchtool · zsh
# ✔ BetterTouchTool presets = Master_bt, Mini_bt   (auto-detected)
# ✔ config saved → /Users/you/Desktop/dotsync_config/dotsync.toml
```

picker 의 색상은 행 상태를 한눈에 보여준다.

- `[x] + installed` → 정상 (기본 색)
- `[x] + not installed` → 빨강 dim ("정리 후보")
- `[ ] + installed` → 노랑 dim ("추가 후보")
- `[ ] + not installed` → 그냥 dim

**BetterTouchTool 의 preset 은 자동으로 모두 추적된다.** dotsync 가 BTT 내부 SQLite 를 읽어 등록된 preset 이름을 모두 가져와 `[options.bettertouchtool]` 의 `presets` 에 넣는다 (예: `["Master_bt", "Mini_bt"]`). 사용자가 따로 고를 일은 없다. `--yes` 모드는 deterministic 한 결정을 위해 자동 감지를 건너뛰고 default(`["Master_bt"]`)를 쓴다.

비대화형(스크립트/새 머신 셋업용):

```bash
# --dir 생략 시 default ~/Desktop/dotsync_config 사용
# --apps 생략 시 자동 감지된 전체를 추적
dotsync init --yes

# 명시적으로 지정도 가능 (BTT presets 는 콤마 구분)
dotsync init --dir ~/my-configs --apps claude,zsh --btt-presets Master_bt,Mini_bt --yes

# post-init 힌트 끔 (셋업 스크립트용)
dotsync init --yes --no-hints

# 셸 rc 자동 쓰기를 끄고 싶을 때 (rc 파일을 dotfiles 리포로 직접 관리하는 경우 등)
dotsync init --yes --no-shell-init
```

**dotsync는 사용자가 지정한 sync 폴더 외에는 컴퓨터 어디에도 파일/디렉토리를 만들지 않는다.** 모든 설정은 `<sync 폴더>/dotsync.toml`에만 저장된다. 단 하나의 예외는 — 설계상 opt-in으로 — `init`이 셸 rc에 추가하는 `export DOTSYNC_DIR="…"` 한 줄이다 (아래 참고). `--no-shell-init`으로 끌 수 있다.

#### dotsync는 sync 폴더를 어떻게 찾나?

두 가지 중 하나면 된다.

1. 환경변수 `DOTSYNC_DIR`이 절대경로로 설정돼 있으면 그것을 사용한다. `init`이 사용자 셸 rc(zsh 면 `~/.zshrc`, bash 면 `~/.bash_profile`)에 이 한 줄을 자동으로 추가해 준다 — 대화형 모드에서는 한 번 물어보고, `--yes`면 묻지 않고 바로 쓴다. 다른 `--dir`로 다시 `init` 하면 기존 라인이 새 경로로 갱신되고, 동일하면 그대로 둔다. 자동 쓰기를 끄려면 `--no-shell-init`. 결과 라인:
   ```bash
   export DOTSYNC_DIR="/Users/you/my-configs"
   ```
2. 또는 sync 폴더 안(또는 그 하위 어디)에서 dotsync 명령을 실행하면 자동으로 `dotsync.toml`을 위로 거슬러 올라가며 찾는다 (git 방식).

#### 새 머신에서 복원할 때

폴더에 이미 `dotsync.toml`이 있으면 dotsync는 그걸 그대로 채택한다. 단, `--apps` 나 `--btt-presets` 를 함께 주면 채택을 건너뛰고 새 값으로 파일을 덮어쓴다.

```bash
git clone git@github.com:you/my-configs.git ~/my-configs
export DOTSYNC_DIR="$HOME/my-configs"   # 한 번만 (.zshrc 등에 추가)
dotsync init --dir ~/my-configs --yes   # 폴더 안 dotsync.toml 그대로 사용
dotsync pull --all
```

#### 2. 로컬 앱 설정 → 폴더 (스냅샷 뜨기)

`dotsync push`는 sync folder에 생길 변경 사항을 먼저 보여준 뒤 복사한다.
적용하려면 `y` 또는 `yes`를 입력한다. 미리보기만 하려면 `--dry-run`,
자동화에서는 `--yes`를 사용한다.

```bash
dotsync push --all --dry-run  # sync folder 변경사항만 preview
dotsync push --all            # interactive (y/N 확인)
dotsync push --all --yes      # automation (prompt 없음)
```

summary box는 실제로 변경된 앱(`✓ changed`)과 이미 같은 상태였던 앱
(`· unchanged`)을 분리해서 보여준다.

```
╭──────────────────────────────────────────────────────────────────╮
│ dotsync push                                                     │
│ 5 apps  →  /Users/you/Desktop/dotsync_config                     │
╰──────────────────────────────────────────────────────────────────╯
... (per-app sections) ...
╭──────────────────────────────────────────────────────────────────╮
│ ✓ changed    ghostty · bettertouchtool                           │
│ · unchanged  claude · codex · zsh                                │
│ 5 ok  ·  0 warn  ·  0 error  ·  2.3s                             │
╰──────────────────────────────────────────────────────────────────╯
```

이후 그 폴더를 git에 커밋하거나 iCloud로 동기화해두면 된다.

#### 3. 폴더 → 로컬 앱 (다른 머신에서 복원하기)

`dotsync pull`은 로컬 설정을 덮어쓰기 전에 local-machine 변경 사항을 먼저
보여준다. 적용하려면 `y` 또는 `yes`를 입력한다. 미리보기만 하려면
`--dry-run`, 자동화에서는 `--yes`를 사용한다. dotsync 는 백업 사본을 남기지
않는다. 미리보기와 확인이 안전장치이므로 확인 전에 계획을 읽어 본다.

```bash
dotsync pull --all --dry-run     # preview only
dotsync pull --all                # interactive (y/N 확인)
dotsync pull --all --yes          # automation (no prompt)
```

`pull` 의 summary box 는 실제로 변경된 앱(`✓ changed`)과 이미 같은 상태였던 앱(`· unchanged`)을 분리해서 보여준다.

```
╭──────────────────────────────────────────────────────────────────╮
│ ✓ changed    ghostty · bettertouchtool                           │
│ · unchanged  claude · codex · zsh                                │
│ 5 ok  ·  0 warn  ·  0 error  ·  3.1s                             │
╰──────────────────────────────────────────────────────────────────╯
```

프리뷰는 파일 복사 항목마다 `+추가 −삭제` 줄 수 요약을 보여준다 — 바이너리 파일은 대신 크기 요약을 보여준다(예: `binary · 50B → 200B`). JSON/TOML은 여기에 바뀐 최상위 키 이름이 덧붙고(예: `+113 −1 · model, hooks`), 디렉토리는 바뀐 파일 이름 목록을 보여준다.

확인 프롬프트에서 `d`를 입력하면 적용 전에 전체 diff를 볼 수 있다
(파일당 최대 200줄, `y`/`n`은 그대로 진행/중단):

    ? Apply these changes to the sync folder? [y/N/d] › d

`dotsync status`도 차이가 있는 앱 아래에 같은 요약을 표시한다.

**Claude 복원은 파일 복사 이상이다.** dotsync 가 기록된 marketplace 들을 다시 등록하고 (`claude plugin marketplace add`), `installed_plugins.json` 에 적힌 모든 plugin 을 `claude plugin install --scope user` 로 재설치한 뒤, `enabledPlugins` 맵에 따라 비활성 상태였던 plugin 은 다시 disable 한다. `claude` CLI 가 설치돼 있지 않으면 plugin 복원만 skip되고 (warning 으로 노출) 파일 복사는 정상 진행된다. 사용자 레벨 글로벌 룰 — `~/.claude/CLAUDE.md` 와 `commands/`, `agents/`, `skills/`, `output-styles/` 디렉토리 — 도 mirror 되므로, 개인 슬래시 커맨드·서브에이전트·스킬이 머신 간에 따라온다. `skills/synced/`(Claude Code 가 claude.ai 계정에서 동기화한 스킬)와 `skills/.trash/`(Claude Code 가 치워 둔 옛 버전)는 Claude Code 가 직접 관리하므로 저장하지 않고(`push` 가 sync 폴더에 이미 있는 사본을 지운다), `pull` 때도 로컬 사본을 그대로 둔다.

**다른 도구가 설치하는 스킬은 그 도구에 맡길 수 있다.** 어떤 도구는 자기 스킬을 직접 설치하고 업그레이드할 때 고쳐 쓴다 (graphify 가 Claude 와 Codex 양쪽에서 그렇다). 이런 스킬을 동기화하면 도구와 충돌하므로 `dotsync.toml` 에 적어 둔다:

```toml
[options.claude]
skills_ignore = ["graphify"]

[options.codex]
skills_ignore = ["graphify"]
```

적어 둔 스킬 디렉터리는 위의 `skills/synced/` 와 같게 다룬다. `push` 는 저장본을 지우고, `pull` 은 로컬 사본을 건드리지 않으며, `status` 는 무시한다. 새 머신에서는 그 도구로 다시 설치한다 (예: `graphify install --platform claude`). 각 항목은 `skills/` 바로 아래 디렉터리 이름이어야 하고, 그 밖의 값은 설정 오류로 거부한다.

dotsync 는 Claude 의 `mcpServers` sync 에서도 동적 로컬 Serena MCP 항목을 제외한다. 다른 MCP 서버 설정은 계속 정상적으로 sync 된다.

**Codex sync 는 사용자가 작성한 글로벌 설정을 mirror 한다.** dotsync 는 `~/.codex/config.toml`, 선택적 instruction/config 파일(`AGENTS.md`, `AGENTS.override.md`, `hooks.json`, `requirements.toml`), 그리고 사용자가 관리하는 `rules/`, `skills/` 디렉토리를 복사한다. `dotsync push codex` 는 sync 폴더를 현재 로컬의 관리 대상 상태로 수렴시킨다. 즉, 위 선택 파일이나 관리 디렉토리가 로컬에 없으면 sync 폴더의 저장본도 삭제된다. `auth.json`, history, sessions, logs, sqlite state, caches, system skills, plugin cache, memories, vendor imports 같은 생성/민감 상태는 복사하지 않고, `skills/.system` 은 의도적으로 동기화하지 않는다.

**Codex plugin 은 파일이 아니라 목록으로 기록한다.** Codex 는 `config.toml` 의 `[marketplaces.*]`, `[plugins.*]` table 을 스스로 고쳐 쓴다 (자기가 번들한 marketplace 의 로컬 경로, `last_updated` / `last_revision`). 그래서 dotsync 는 이 table 들을 뺀 `config.toml` 을 저장하고, 대신 `push` 때 `codex plugin list --json` 으로 설치 상태를 물어 `codex/plugins.json` 을 쓴다:

```json
{
  "marketplaces": [
    {"name": "my-market", "source": "https://github.com/me/my-market.git", "ref": "main"}
  ],
  "plugins": ["mine@my-market", "superpowers@openai-curated-remote"]
}
```

- 기록하는 것: 사용자가 설치해 켜 둔 plugin, 사용자가 추가한 marketplace (`ref`, `sparse` 포함).
- Codex 에 맡기는 것: Codex 가 기본 설치하는 plugin (`INSTALLED_BY_DEFAULT`), Codex 가 자기 디렉터리(`~/.codex`, `~/.cache/codex-runtimes`)에 두는 marketplace(예: `openai-bundled`)와 그 plugin. 꺼 둔 plugin 은 기록하지 않는다.

`pull` 은 로컬의 marketplace/plugin table 은 그대로 두고 `config.toml` 의 나머지만 저장본으로 바꾼다. 이어서 기록된 marketplace 중 없는 것만 추가하고 (`codex plugin marketplace add <source>`, `--ref` / 반복 `--sparse` 포함), 기록된 plugin 중 없는 것만 설치한다 (`codex plugin add <plugin@marketplace> --json`). 이미 있는 것은 건드리지 않으므로 `pull` 을 다시 실행해도 새로 설치하지 않는다. marketplace 를 추가하지 못하면 그 plugin 들은 warning 과 함께 건너뛴다. 새 머신에서는 Codex 를 한 번 실행해 Codex 자체 marketplace 가 생긴 뒤 다시 pull 하면 된다. `codex` CLI 가 없거나 출력이 예상과 다르면 파일 동기화는 그대로 진행하고, `push` 는 기존 `plugins.json` 을 유지하며, plugin 복원은 warning 과 함께 건너뛴다. `status` 와 미리보기는 그 table 들을 뺀 `config.toml` 을 비교하고, `plugins.json` 을 실제 설치 상태와 비교한다.

dotsync 는 Codex 설정을 sync 할 때 동적 로컬 Serena MCP URL 을 의도적으로 제외한다. Serena 포트는 프로젝트별 runtime state 이므로, 복사된 `127.0.0.1:<port>` URL 은 사용자가 작성한 설정이 아니라 머신 로컬 상태로 취급한다.

**Herdr sync는 `~/.config/herdr/config.toml`만 추적한다.** `session*.json`, plugin registry state, log, socket, lock 파일 같은 session/runtime 상태는 의도적으로 제외한다. 기존 dotsync 사용자는 `dotsync apps`에서 Herdr를 켜거나 `dotsync config apps ...`로 목록을 교체할 때 `herdr`를 포함하면 된다. 기존 추적 앱 선택은 자동으로 바꾸지 않는다.

**Skills sync는 `npx skills add -g`로 무엇을 설치했는지를 기록하고, 파일은 복사하지 않는다.** `push`는 `~/.agents/.skill-lock.json`을 읽어 각 스킬의 source와, `~/.claude/skills/<name>` / `~/.codex/skills/<name>` 링크가 그 스킬을 가리키는 관리 대상 에이전트(`claude-code`, `codex`)를 `skills/skills.json`에 쓴다. lock 파일에는 GitHub 토큰이 들어갈 수 있어 sync 폴더로는 절대 복사하지 않는다. `pull`은 로컬에 없는 스킬마다 `npx -y skills add <source> --global --skill <name> --agent <agent> --yes`를 실행하고, 실패·`npx` 없음·GitHub가 아닌 source는 warning으로 보고한다. `--copy`로 설치한 스킬은 일반 디렉터리이므로 claude/codex 앱이 파일로 동기화한다.

**BetterTouchTool 은 실행 중이어야 한다.** `push` / `pull` / `status` 모두 `osascript` 으로 BTT 를 제어하기 때문. BTT 가 꺼져 있으면 `status` 는 `unknown`, `push` / `pull` 은 에러로 멈춘다. preset 이름은 BTT 이름 그대로 취급되며, 빈 이름, 경로 구분자, 따옴표, 제어문자는 AppleScript 생성 전에 거부된다.

#### 4. 동기화 상태 확인 (파일별 sha256 비교)

상태 라인은 색·심볼로 한눈에 구분되며, 'dirty'면 어느 쪽이 더 최신인지(direction)도 함께 표시됩니다.

```bash
$ dotsync status
▸ status                              ~/dotsync_config

  ✓ zsh              clean
  ✓ codex            clean
  ⚠ ghostty          dirty  local-newer — config.ghostty
    ⚠ update         config.ghostty  — +2 −1
  ✗ claude           missing
  · bettertouchtool  unknown — BTT not running
```

dirty 상태인 앱마다 파일별 변경 요약이 들여쓰기된 줄로 붙는다.
`push`/`pull` 프리뷰와 같은 요약이다.

범례:

- `✓ clean` — local 과 stored 의 sha256 일치. 단 Claude 의 `installed_plugins.json` 과 `known_marketplaces.json` 은 `push`/`pull` 미리보기와 같은 방식으로 비교해서, Claude Code 가 스스로 고쳐 쓰는 실행 기록(설치 경로, 버전, 시각, 커밋 SHA)은 무시한다
- `⚠ dirty` — 다름; direction 은 `local-newer`, `folder-newer`, `diverged` (양쪽 섞임) 중 하나
- `✗ missing` — 한쪽이라도 파일이 없음
- `· unknown` — 비교 불가 (예: BTT 미실행)

#### 5. Claude Code 계정 바꾸기

claude.ai 로그인 여러 개를 저장해 두고 Claude Code 가 쓰는 계정을 바꾼다. `/login` 과 같은 효과지만 매번 브라우저를 거치지 않는다. 실행 중인 세션도 `/login` 때처럼 바뀐 계정을 따라간다.

```bash
dotsync account login work      # 브라우저가 승인한 계정을 "work" 로 저장 (계정마다 한 번)
dotsync account use work        # Claude 가 그 계정을 쓰게 함
dotsync account list            # 저장된 계정 목록, ● 는 지금 쓰는 계정
dotsync account usage           # 계정별 5시간·주간 사용량과 초기화 시각
dotsync account rename work "업무용 Max"   # 보이는 이름 바꾸기
dotsync account remove work     # 로그아웃하고 삭제
```

- 계정 저장은 Claude Code 가 직접 한다. `login` 은 `CLAUDE_CONFIG_DIR=~/.claude-accounts/<이름>` 으로 `claude auth login` 을 실행하므로 토큰은 그 폴더의 키체인 항목에 남는다.
- `use` 는 그 로그인을 Claude 의 기본 키체인 항목에, 계정 정보를 `~/.claude.json` 에 복사한다. 그 전에 지금 쓰던 로그인을 원래 계정으로 돌려놓는다. Claude 는 쓰는 동안 토큰을 갱신하고, 한 번 쓴 갱신 토큰은 다시 못 쓰기 때문이다.
- 토큰은 명령줄이 아니라 표준 입력으로 `security` 에 넘긴다.
- 지금 쓰는 로그인이 저장돼 있지 않으면(처음 쓸 때 등) `use` 가 버려도 되는지 묻는다. 먼저 `dotsync account login <이름>` 으로 저장해 두면 된다.
- `dotsync account` 를 쓰기 시작했으면 `/login` 대신 `use` 로 바꾼다. `/login` 은 쓰던 로그인을 돌려놓지 않고 덮어써서, 그 계정은 `dotsync account login` 을 다시 해야 한다.
- 계정은 동기화와 별개다. sync 폴더가 필요 없고 `push`/`pull` 은 계정을 건드리지 않는다.
- `usage` 는 Claude Code 에 직접 묻는다(`claude -p` 의 `get_usage` 요청). 토큰을 쓰지 않는다. 지금 쓰는 계정은 Claude 기본 폴더로, 나머지는 각자 폴더로 동시에 조회하고, 한 계정이 느려도(30초 제한) 나머지는 기다리지 않는다.
- 모든 `account` 명령은 `--json` 을 받으면 JSON 객체 하나만 출력한다. dotsync 앱이 이걸 읽는다. 오류는 `{"error": {"code": …, "message": …}}` 와 종료 코드 1. 계정을 바꾸거나 Claude 를 실행하는 명령은 서로 기다린다(`~/.claude-accounts/.lock`).

#### 6. dotsync 앱 (macOS)

같은 계정들을 창과 위젯으로 본다. 계정별 5시간·주간 사용량과 초기화 시각, 클릭 한 번으로 교체.

```bash
brew install --cask changja88/dotsync/dotsync-app   # dotsync 명령어도 함께 설치
```

- 앱은 서명했지만 공증은 안 했다. 처음과 업데이트할 때마다 시스템 설정 → 개인정보 보호 및 보안 → "그래도 열기"로 한 번 연다.
- 위젯 편집에서 위젯을 추가한다. 바탕화면과 알림 센터 어디에나 놓을 수 있다.
  - "Claude 계정"(큰 크기, 아주 큰 크기): 지금 쓰는 계정과 나머지 계정을 함께 본다.
  - "지금 사용 중"(중간 크기): 지금 쓰는 계정만 본다.
  - "Claude 계정 목록"(큰 크기): 나머지 계정을 여섯 개까지 본다. 알림 센터에서 "지금 사용 중" 아래에 두면 둘이 긴 위젯 하나처럼 보인다.
- 위젯에서 ↻ 는 새로고침(약 10초, 토큰 안 씀), "사용"은 `dotsync account use`처럼 계정을 바꾼다. 앱은 쓸 때만 실행되고 뒤에 남지 않는다.
- 창에서 계정 추가, 다시 로그인, 이름 바꾸기, 삭제를 한다.
- 설치하거나 업데이트하면 실행 중이던 위젯을 끄고 새 위젯이 대신 뜬다. 그래도 위젯이 비어 있으면 앱을 한 번 연다. 앱이 위젯을 다시 그린다.

#### 폴더/앱 목록을 나중에 바꾸고 싶으면

`dotsync apps` 가 init Step 2 와 똑같은 picker 를 띄운다. BTT 를 새로 토글하면 등록된 preset 들이 자동 재검색돼 config 에 반영된다.

```bash
dotsync apps                              # picker 로 추적 앱 변경 (Enter = 그대로)
dotsync config show                       # 현재 설정 보기
dotsync config dir ~/another-folder       # sync 폴더 변경
dotsync config apps claude,zsh            # 추적 앱 일괄 교체 (자동화용)
dotsync config btt-presets MyPreset,Other # BTT preset 목록 일괄 교체 (콤마 구분)
```

`dotsync config show` 는 BTT preset 같은 앱별 옵션도 함께 보여준다. `dotsync.toml` 에는 `apps` 와 `[options.<app>]` table 만 둔다. `[options]` 아래의 다른 key 는 설정 오류로 거부한다. 최상위 관리 파일·디렉터리가 symlink 이면 따라가지 않고 거부한다. Claude `skills/` 나 Codex `rules/` 처럼 미러링하는 디렉터리 안의 symlink 항목(예: `npx skills add` 가 만든 링크)은 warning 과 함께 건너뛰며, 읽거나 쓰지 않고 `pull` 때도 그대로 둔다. `skills` 앱을 추적 중이면 `npx skills add` 가 만든 링크는 그 앱이 기록하므로 warning 없이 건너뛴다. 미러링하는 디렉터리 안의 `.DS_Store` 와 `*.bak` 파일(graphify 처럼 파일을 고쳐 쓰기 전에 사본을 남기는 도구가 만든 것)도 같은 방식으로 무시한다. 저장하지도, 지우지도 않는다.

picker 키 안내:

- `↑` / `↓` — 항목 이동
- `space` — 현재 항목 체크 토글
- `enter` — 확정
- `q` / `esc` / `ctrl+c` — 취소 (설정 변경 없음)

CI나 파이프 같은 비-TTY 환경에서는 자동으로 앱별 y/n 프롬프트로 fallback 한다.

지원 앱: `claude`, `codex`, `herdr`, `skills`, `ghostty`, `bettertouchtool`, `zsh`

### 새 앱 추가

`docs/adding-an-app.md` 참고. 단순 파일 기반 앱은 모듈 1개 + `apps/__init__.py`에 한 줄로 끝난다. 복잡한 앱(외부 프로세스, 자체 CLI 옵션)은 base의 hook을 override해서 같은 자리에서 확장한다.

도움말: `dotsync --help`, `dotsync <command> --help`. 모든 출력은 `NO_COLOR=1` 을 존중한다.

---

## License

MIT
