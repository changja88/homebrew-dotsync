# dotsync

A macOS app that consolidates your app configs into **one folder of your choice**, with two-way sync · macOS 앱 설정을 **사용자가 지정한 한 폴더**에서 관리하는 양방향 sync 앱

> **macOS only · Python 3.12+**

---

## English

### Purpose

dotsync consolidates your macOS app configs (Claude Code, Codex CLI, Herdr, agent skills from `npx skills`, Ghostty, BetterTouchTool, zsh) into **one folder of your choice** and keeps it in two-way sync with the apps. That folder can be anywhere — a fresh directory like `~/my-configs`, or a folder you already track in git or sync via iCloud Drive. Tool (dotsync) and data (the folder) are separated, so setting up a new Mac is just a matter of bringing the folder along.

You use it through **dotsync.app**: its 설정 menu syncs the configs, 사용량 switches Claude Code accounts, and 세션 cleans up Claude Code and Codex sessions.

### Install

```bash
brew install --cask changja88/dotsync/dotsync-app   # dotsync.app, and the dotsync engine it runs
```

- The app is signed but not notarized: the first time, and after each update, open it once via System Settings → Privacy & Security → "Open Anyway".
- The cask installs the `dotsync` formula too. That command is the app's engine, not a terminal tool: every command answers the app with one JSON object (`--json`), and run without it, it only says so (see [For developers](#for-developers-the-json-contract)).
- If Python 3.12 or 3.13 already exists at a canonical path (`/opt/homebrew/bin/python3.{12,13}`, `/usr/local/bin/python3.{12,13}`, or `/Library/Frameworks/Python.framework/Versions/3.{12,13}/...`), dotsync reuses it — no duplicate install. Otherwise Homebrew pulls in `python@3.12` as a dependency. Pythons installed via pyenv / uv are at non-canonical paths and won't be auto-detected; functionality is unaffected, but the duplicate install is not avoided.

### Usage

#### 1. Choose the sync folder and the apps

Open 설정 and pick the sync folder with "다른 폴더…".

- A folder that already holds a `dotsync.toml` — another Mac's, brought along with git or iCloud — is used as is: its apps and options stay.
- Any other folder gets a new `dotsync.toml`: it keeps the apps of the folder you used before, or tracks nothing on a Mac that had none.
- The table below lists every supported app; its switches pick the ones to track. Turning BetterTouchTool on reads the preset names from BTT's own store and tracks them all (`["Master_bt"]` when none can be read).

On a new Mac: bring the folder (`git clone`, iCloud Drive), choose it in 설정, then pull.

**dotsync creates no files or directories on your machine outside the sync folder you chose, with these exceptions:**

- One marked line in your shell rc, `export DOTSYNC_DIR="…"`, under the comment ``# Added by `dotsync init` — points dotsync at your sync folder.``. It is how dotsync finds the folder: dotsync.app has no shell environment, so dotsync reads that line (it never runs it), as long as the folder holds a `dotsync.toml`. Choosing another folder rewrites it (a line you added by hand gets the comment); with no rc file at all, dotsync starts `~/.bash_profile` with it, so a `~/.zshrc` from your other Mac can still come down.
- `~/.claude-accounts/` — the Claude Code logins of the 사용량 menu.
- What the session commands keep: lock files in `~/Library/Application Support/dotsync/locks/` and result logs in `~/Library/Logs/dotsync/` (see [section 5](#5-clean-up-claude-code-and-codex-sessions)).

All settings live in `<sync folder>/dotsync.toml`.

#### 2. Push and pull

Push copies the local app configs into the folder; pull copies the folder back onto this Mac. Then commit the folder to git or let iCloud sync it.

The app previews every push and pull before it applies one. dotsync keeps no backup copies in either direction: the preview is the safety net, so read it before you apply. Each file-copy entry carries line counts (`+added −removed`) — binary files show a size summary instead (e.g. `binary · 50B → 200B`). JSON/TOML files additionally list their changed top-level keys (e.g. `+113 −1 · model, hooks`), directories list the changed file names, and the preview can show each change's full diff (capped at 200 lines per file).

**Claude restoration goes beyond file copy.** dotsync replays the recorded marketplaces (`claude plugin marketplace add`) and runs `claude plugin install --scope user` for every plugin in `installed_plugins.json`, then re-applies the `enabledPlugins` map so disabled plugins stay disabled. If the `claude` CLI isn't installed, plugin replay is skipped (reported as a warning) and the file copy still succeeds. dotsync also mirrors your user-level global rules — `~/.claude/CLAUDE.md` and the `commands/`, `agents/`, `skills/`, `output-styles/` directories — so personal slash commands, subagents, and skills follow you across machines. `skills/synced/` (skills Claude Code syncs from your claude.ai account) and `skills/.trash/` (old versions Claude Code moved aside) are managed by Claude Code itself, so they are never stored (a push removes any copy already in the sync folder), and a pull leaves the local copies in place.

**Skills another tool installs can be left to that tool.** Some tools install their own skill and rewrite it on upgrade. Syncing such a skill fights the tool, so list it in `dotsync.toml`:

```toml
[options.claude]
skills_ignore = ["my-tool"]

[options.codex]
skills_ignore = ["my-tool"]
```

Listed skill directories are treated like `skills/synced/` above: a push removes the stored copy, a pull never touches the local one, and the sync state ignores them. Reinstall them on a new machine with the tool itself. Each entry must be a top-level directory name under `skills/`; anything else is rejected as a config error.

**Codex sync mirrors user-authored global settings.** dotsync copies `~/.codex/config.toml`, optional instruction/config files (`AGENTS.md`, `AGENTS.override.md`, `hooks.json`, `requirements.toml`), and the user-managed `rules/` and `skills/` directories. A push of codex converges the sync folder to the current local managed set: if one of those optional files or managed directories is absent locally, the stored copy is removed from the sync folder. It skips generated or sensitive state such as `auth.json`, history, sessions, logs, sqlite state, caches, system skills, plugin caches, memories, and vendor imports; `skills/.system` is intentionally not synced.

**Codex plugins are recorded, not copied.** Codex rewrites the `[marketplaces.*]` and `[plugins.*]` tables of `config.toml` by itself (local paths of the marketplaces it bundles, `last_updated` / `last_revision`), so dotsync stores `config.toml` without them. Instead, a push asks `codex plugin list --json` what is installed and writes `codex/plugins.json`:

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

A pull keeps the local marketplace/plugin tables and replaces the rest of `config.toml` with the stored settings. It then adds each recorded marketplace that is missing (`codex plugin marketplace add <source>`, with `--ref` / repeated `--sparse`) and installs each recorded plugin that is missing (`codex plugin add <plugin@marketplace> --json`). Whatever is already present is left alone, so pulling again installs nothing. If a marketplace cannot be added, its plugins are skipped with a warning; on a fresh machine, start Codex once so its own marketplaces exist, then pull again. If the `codex` CLI is missing or prints something unexpected, files still sync, a push keeps the existing `plugins.json`, and plugin restore is skipped with a warning. The sync state and the previews compare `config.toml` without those tables and compare `plugins.json` with what is installed.

**Herdr sync tracks only `~/.config/herdr/config.toml`.** Session and runtime state such as `session*.json`, plugin registry state, logs, sockets, and lock files are intentionally excluded. Turn Herdr on in the 설정 table; the tracked apps are never changed for you.

**Skills sync records what `npx skills add -g` installed, not the files.** A push reads `~/.agents/.skill-lock.json` and writes `skills/skills.json` with each skill's source and the managed agents (`claude-code`, `codex`) whose `~/.claude/skills/<name>` / `~/.codex/skills/<name>` link points at it — the lock file itself is never copied into the sync folder, since it can hold a GitHub token. A pull runs `npx -y skills add <source> --global --skill <name> --agent <agent> --yes` for every recorded skill that is missing locally; failures, a missing `npx`, and non-GitHub sources are reported as warnings. Skills installed with `--copy` are plain directories and sync as files through the claude/codex apps instead.

**BetterTouchTool must be running** to push, pull or check it — dotsync drives BTT via `osascript`, which needs the Automation permission (설정 opens it for you). If BTT isn't running, its state is `unknown` and a push or pull of it fails. Preset names are treated as literal BTT names; empty names, path separators, quotes, and control characters are rejected before any AppleScript is generated.

#### 3. Sync state

설정 shows each tracked app's state (per-file sha256), and for an app that differs, which side is newer and the same per-file summaries as the previews.

- `clean` — local and stored bytes match (sha256 equal). Claude's `installed_plugins.json` and `known_marketplaces.json` are compared the way the previews compare them, ignoring runtime metadata Claude Code rewrites on its own (install paths, versions, timestamps, commit SHAs)
- `dirty` — differ; the direction is `local-newer`, `folder-newer`, or `diverged` (mix of both)
- `missing` — at least one side is absent
- `unknown` — couldn't determine (e.g., BTT not running)

#### 4. Claude Code accounts and widgets

The 사용량 menu keeps several claude.ai logins and switches the one Claude Code uses — the same effect as `/login`, without the browser each time. Running sessions follow the switch the way they follow a `/login`. It shows each account's 5-hour and weekly usage with reset times, and adds, logs in again, renames and removes accounts.

- Claude Code saves each account itself: adding one runs `claude auth login` with `CLAUDE_CONFIG_DIR=~/.claude-accounts/<name>`, so the tokens stay in that folder's Keychain entry.
- 사용 copies that login into Claude's default Keychain entry and the account details into `~/.claude.json`. It first hands the login in use back to its own account, because Claude refreshes tokens as it runs and a used refresh token stops working.
- Tokens reach `security` on stdin, never on a command line.
- If the login in use isn't saved (the first time, for example), the app asks before dropping it, and offers to save it first.
- Once you switch with dotsync, keep switching with it instead of `/login`: `/login` replaces the login in use without handing it back, and that account then needs logging in again.
- Accounts are separate from syncing: they need no sync folder, and push/pull never touch them.
- Usage comes from Claude Code itself (`claude -p` with its `get_usage` request), so it spends no tokens. The account in use is read through Claude's default folder, the others through their own folders, all at once; one slow account (30 s limit) doesn't hold up the rest.

Widgets — add one from Edit Widgets, on the desktop or in Notification Center:

- "Claude 계정" (large or extra large): the account in use and the other accounts together.
- "지금 사용 중" (medium): the account in use alone.
- "Claude 계정 목록" (large): the other accounts, up to seven (with more, six and "외 n개"). Under "지금 사용 중" in Notification Center, the two read as one tall widget.

On a widget, ↻ refreshes (about 10 s, no tokens) and "사용" switches Claude. The app runs only while you use it; with auto switch on, only a short check runs in the background (below). An install or update stops the widget that was running, so the new one takes over. If a widget still stays blank, open the app once: it reloads the widgets.

Auto switch — the 자동 교체 menu, off until you turn it on:

- When the account in use reaches 90% of its 5-hour window or 98% of its week, dotsync switches Claude to another saved account: the one with the least weekly use (균등 사용), or the one whose week resets soonest (주간 초기화 가까운 순). Any account below both limits qualifies, except the ones you exclude.
- It needs the live usage claude-hud writes: set claude-hud's `display.externalUsageWritePath` to `~/Library/Group Containers/GR53VV7ZD2.dotsync/live-usage.json` (as an absolute path).
- Nothing stays running. A LaunchAgent (`com.changja88.dotsync.autoswitch`) runs the app's executable for a moment whenever that folder changes. When it's time to switch, it opens the app without a window; the app probes every account, switches, notes it in the menu's log, sends a notification and quits. macOS asks you to allow the background item the first time.
- When no account can take over, or a switch fails, it rests 15 minutes; when the probe shows the account was below the limits after all, 5 minutes.
- While it's on, "사용" is gone from the window and the widgets: turn it off to switch by hand. Claude's own `/login` can't be stopped; on a login dotsync hasn't saved, auto switch waits until you save it.
- An update removes the background item until you open the new app (after "Open Anyway"), which puts it back.

#### 5. Clean up Claude Code and Codex sessions

The 세션 menu lists, deletes and recovers the sessions Claude Code and Codex keep on disk. Sessions need no sync folder, and push/pull never touch them.

- A session is not deleted if it is running or if dotsync cannot tell whether it is in use.
- **Claude Code** — a session written in the last 30 minutes is not deleted either. dotsync checks again right before each delete and skips what changed meanwhile. A delete removes the conversation, subagent transcripts, tool results, MCP task records, file-history, the session environment, debug log, pasted files, and the temp images and task folder. It leaves the prompt history, to-do lists, plans, project memory, settings, and tool results other sessions still use (those files are shared, so they are not picked apart per session). A delete that stops halfway shows as "interrupted": it can be undone (the transcripts are renamed back) or finished (what is left is deleted). Claude Code deletes the same files itself once 30 days have passed since the last entry.
- **Codex** — dotsync checks once per batch, before deleting, which sessions are open. A delete is Codex's own (`codex delete`), so Codex may stay open; it refuses sessions open in another window. It removes the conversation, the thread's rows in the history database, the name-list entry and logs, including the sessions forked below it. dotsync then removes the reference cache files. It leaves the prompt history, attachments, desktop-app state and generated images. The history database does not shrink right away.
- **Codex cleanup** removes what deleted sessions left in Codex's files (`history_rows`, `history_lines`, `session_index`, `queue`, plus the cache files), and can also reclaim the history database's free space (vacuum). It only runs while nothing uses `~/.codex`; dotsync.app quits ChatGPT.app for the cleanup and relaunches it afterwards, but a terminal Codex is never closed for you: the cleanup refuses while one runs. It also refuses as a whole when it cannot be sure what to keep: a rollout file cannot be read, `zstd` is missing while compressed rollouts exist, or Codex's database version is not the one dotsync knows.
- Codex rollouts compressed as `.zst` are read with the `zstd` command, which the Homebrew formula installs.
- Every delete, recover and cleanup writes a log, even when it was stopped: `~/Library/Logs/dotsync/sessions-<timestamp>-<pid>.json`. It holds session ids, file paths and outcomes, never conversation contents (no titles, no transcript or history text).
- Stopping a running delete or cleanup (`SIGTERM`) stops it at the next safe point (between sessions, cleanup items or vacuum batches), then reports and logs what was done.

#### 6. `dotsync.toml`

`dotsync.toml` holds only `apps` and `[options.<app>]` tables; any other key under `[options]` is rejected as a config error. Top-level managed files and directories that are symlinks are rejected instead of followed. Inside mirrored directories such as Claude `skills/` or Codex `rules/`, symlinked entries (for example the links `npx skills add` creates) are skipped with a warning and never read or written through, and a pull leaves them in place. When the `skills` app is tracked, the links `npx skills add` made are skipped without a warning, since that app records them. `.DS_Store` files and `*.bak` files (copies some tools keep before rewriting a file) inside mirrored directories are ignored the same way: never stored, never removed.

Supported apps: `claude`, `codex`, `herdr`, `skills`, `ghostty`, `bettertouchtool`, `zsh`

#### For developers: the JSON contract

dotsync.app runs these commands and decodes their answers:

```bash
dotsync status --json
dotsync push|pull <app>|--all --dry-run|--yes --json   # preview, or apply
dotsync config dir --json -- <folder>
dotsync config apps <app,app,…> --json
dotsync account login|use [--yes]|rename|remove|usage --json -- <args>
dotsync sessions list|delete|recover|cleanup … --json
dotsync --version
```

- Each prints exactly one JSON object on stdout; anything else goes to stderr. Failures are `{"error": {"code": …, "message": …}}` with exit status 1. Without `--json` a command prints that dotsync is run by dotsync.app and exits 2.
- Long session commands write `dotsync-progress <done>/<total>` lines to stderr; the app draws its progress from them.
- Commands that change the same tool wait for each other through lock files in `~/Library/Application Support/dotsync/locks/`: `claude-sessions.lock` (`delete claude`, `recover claude`) and `codex.lock` (`delete codex`, `cleanup codex --yes`; push/pull/status of `codex` hold it shared). Account commands hold `~/.claude-accounts/.lock`. A lock not free within 10 seconds fails with `busy`.

### Adding a new app

See `docs/adding-an-app.md`. A simple file-based app is one module + one line in `apps/__init__.py`'s `APP_CLASSES`. Complex apps (external processes, app-specific options) extend in place by overriding `App` hooks.

---

## 한국어

### 목적

dotsync는 macOS의 앱 설정(Claude Code, Codex CLI, Herdr, `npx skills`로 설치한 agent skill, Ghostty, BetterTouchTool, zsh)을 **사용자가 지정한 단일 폴더**에 모아서 양방향으로 동기화한다. 그 폴더는 어디든 OK — 새 폴더(`~/my-configs`)일 수도 있고, 이미 git이나 iCloud Drive로 관리 중인 폴더일 수도 있다. 도구(dotsync)와 데이터(폴더)를 분리해서, 새 Mac 셋업도 폴더만 옮겨오면 끝난다.

**dotsync.app**으로 쓴다. 설정 메뉴에서 설정을 동기화하고, 사용량에서 Claude Code 계정을 바꾸고, 세션에서 Claude Code와 Codex 세션을 정리한다.

### 설치

```bash
brew install --cask changja88/dotsync/dotsync-app   # dotsync.app과, 앱이 실행하는 dotsync 엔진
```

- 앱은 서명했지만 공증은 안 했다. 처음과 업데이트할 때마다 시스템 설정 → 개인정보 보호 및 보안 → "그래도 열기"로 한 번 연다.
- cask는 `dotsync` formula도 함께 설치한다. 이 명령은 앱의 엔진이지 터미널 도구가 아니다. 모든 명령은 JSON 객체 하나(`--json`)로 앱에 답하고, `--json` 없이 실행하면 그렇다고만 알린다([개발자용](#개발자용-json-규약) 참고).
- Python 3.12 또는 3.13 이 canonical 경로 (`/opt/homebrew/bin/python3.{12,13}`, `/usr/local/bin/python3.{12,13}`, `/Library/Frameworks/Python.framework/Versions/3.{12,13}/...`)에 이미 있으면 그것을 그대로 재사용한다 — 중복 설치하지 않는다. 없을 때만 brew 가 `python@3.12` 를 함께 설치한다. pyenv / uv 처럼 비-canonical 경로에 깔린 Python 은 자동 감지되지 않아 brew 가 자기 `python@3.12` 를 설치한다. 동작은 정상이지만 중복 설치는 발생.

### 사용법

#### 1. sync 폴더와 앱 고르기

설정을 열고 "다른 폴더…"로 sync 폴더를 고른다.

- 이미 `dotsync.toml`이 있는 폴더(git이나 iCloud로 가져온 다른 Mac의 폴더)는 그대로 쓴다. 그 폴더의 앱과 옵션이 유지된다.
- 그 밖의 폴더에는 새 `dotsync.toml`을 만든다. 전에 쓰던 폴더의 앱을 이어받고, 처음 쓰는 Mac이면 아무 앱도 추적하지 않는다.
- 아래 표에 지원하는 앱이 모두 나오고, 스위치로 추적할 앱을 고른다. BetterTouchTool을 켜면 BTT 내부 저장소에서 preset 이름을 읽어 모두 추적한다(읽을 수 없으면 `["Master_bt"]`).

새 Mac에서는: 폴더를 가져오고(`git clone`, iCloud Drive), 설정에서 그 폴더를 고른 뒤, 가져오기(pull)를 한다.

**dotsync는 사용자가 지정한 sync 폴더 외에는 컴퓨터 어디에도 파일/디렉토리를 만들지 않는다. 예외는 다음뿐이다.**

- 셸 rc의 표식 붙은 한 줄 `export DOTSYNC_DIR="…"`(표식 주석은 ``# Added by `dotsync init` — points dotsync at your sync folder.``). dotsync는 이 줄로 폴더를 찾는다. dotsync.app에는 셸 환경이 없어서, 그 폴더에 `dotsync.toml`이 있으면 이 줄을 읽는다(실행하지 않고 읽기만 한다). 다른 폴더를 고르면 이 줄을 고쳐 쓰고(손으로 넣은 줄이면 표식 주석도 붙인다), rc 파일이 하나도 없으면 `~/.bash_profile`을 만들어 이 줄을 넣는다. 그래야 다른 맥의 `~/.zshrc`를 그대로 받아올 수 있다.
- `~/.claude-accounts/` — 사용량 메뉴의 Claude Code 로그인.
- 세션 명령이 남기는 것: `~/Library/Application Support/dotsync/locks/`의 락 파일과 `~/Library/Logs/dotsync/`의 결과 로그(아래 [5번](#5-claude-code와-codex-세션-정리) 참고).

모든 설정은 `<sync 폴더>/dotsync.toml`에 저장된다.

#### 2. 내보내기와 가져오기

내보내기(push)는 로컬 앱 설정을 폴더로 복사하고, 가져오기(pull)는 폴더를 이 Mac으로 복사한다. 이후 그 폴더를 git에 커밋하거나 iCloud로 동기화해두면 된다.

앱은 내보내기와 가져오기를 적용하기 전에 항상 미리보기를 보여준다. dotsync 는 어느 쪽으로도 백업 사본을 남기지 않는다. 미리보기가 안전장치이므로 적용 전에 읽어 본다. 파일 복사 항목마다 `+추가 −삭제` 줄 수 요약이 붙는다 — 바이너리 파일은 대신 크기 요약을 보여준다(예: `binary · 50B → 200B`). JSON/TOML은 여기에 바뀐 최상위 키 이름이 덧붙고(예: `+113 −1 · model, hooks`), 디렉토리는 바뀐 파일 이름 목록을 보여주며, 미리보기에서 항목별 전체 diff도 볼 수 있다(파일당 최대 200줄).

**Claude 복원은 파일 복사 이상이다.** dotsync 가 기록된 marketplace 들을 다시 등록하고 (`claude plugin marketplace add`), `installed_plugins.json` 에 적힌 모든 plugin 을 `claude plugin install --scope user` 로 재설치한 뒤, `enabledPlugins` 맵에 따라 비활성 상태였던 plugin 은 다시 disable 한다. `claude` CLI 가 설치돼 있지 않으면 plugin 복원만 skip되고 (warning 으로 알린다) 파일 복사는 정상 진행된다. 사용자 레벨 글로벌 룰 — `~/.claude/CLAUDE.md` 와 `commands/`, `agents/`, `skills/`, `output-styles/` 디렉토리 — 도 mirror 되므로, 개인 슬래시 커맨드·서브에이전트·스킬이 머신 간에 따라온다. `skills/synced/`(Claude Code 가 claude.ai 계정에서 동기화한 스킬)와 `skills/.trash/`(Claude Code 가 치워 둔 옛 버전)는 Claude Code 가 직접 관리하므로 저장하지 않고(내보내기가 sync 폴더에 이미 있는 사본을 지운다), 가져오기 때도 로컬 사본을 그대로 둔다.

**다른 도구가 설치하는 스킬은 그 도구에 맡길 수 있다.** 어떤 도구는 자기 스킬을 직접 설치하고 업그레이드할 때 고쳐 쓴다. 이런 스킬을 동기화하면 도구와 충돌하므로 `dotsync.toml` 에 적어 둔다:

```toml
[options.claude]
skills_ignore = ["my-tool"]

[options.codex]
skills_ignore = ["my-tool"]
```

적어 둔 스킬 디렉터리는 위의 `skills/synced/` 와 같게 다룬다. 내보내기는 저장본을 지우고, 가져오기는 로컬 사본을 건드리지 않으며, 동기화 상태는 무시한다. 새 머신에서는 그 도구로 다시 설치한다. 각 항목은 `skills/` 바로 아래 디렉터리 이름이어야 하고, 그 밖의 값은 설정 오류로 거부한다.

**Codex sync 는 사용자가 작성한 글로벌 설정을 mirror 한다.** dotsync 는 `~/.codex/config.toml`, 선택적 instruction/config 파일(`AGENTS.md`, `AGENTS.override.md`, `hooks.json`, `requirements.toml`), 그리고 사용자가 관리하는 `rules/`, `skills/` 디렉토리를 복사한다. codex 내보내기는 sync 폴더를 현재 로컬의 관리 대상 상태로 수렴시킨다. 즉, 위 선택 파일이나 관리 디렉토리가 로컬에 없으면 sync 폴더의 저장본도 삭제된다. `auth.json`, history, sessions, logs, sqlite state, caches, system skills, plugin cache, memories, vendor imports 같은 생성/민감 상태는 복사하지 않고, `skills/.system` 은 의도적으로 동기화하지 않는다.

**Codex plugin 은 파일이 아니라 목록으로 기록한다.** Codex 는 `config.toml` 의 `[marketplaces.*]`, `[plugins.*]` table 을 스스로 고쳐 쓴다 (자기가 번들한 marketplace 의 로컬 경로, `last_updated` / `last_revision`). 그래서 dotsync 는 이 table 들을 뺀 `config.toml` 을 저장하고, 대신 내보내기 때 `codex plugin list --json` 으로 설치 상태를 물어 `codex/plugins.json` 을 쓴다:

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

가져오기는 로컬의 marketplace/plugin table 은 그대로 두고 `config.toml` 의 나머지만 저장본으로 바꾼다. 이어서 기록된 marketplace 중 없는 것만 추가하고 (`codex plugin marketplace add <source>`, `--ref` / 반복 `--sparse` 포함), 기록된 plugin 중 없는 것만 설치한다 (`codex plugin add <plugin@marketplace> --json`). 이미 있는 것은 건드리지 않으므로 다시 가져와도 새로 설치하지 않는다. marketplace 를 추가하지 못하면 그 plugin 들은 warning 과 함께 건너뛴다. 새 머신에서는 Codex 를 한 번 실행해 Codex 자체 marketplace 가 생긴 뒤 다시 가져오면 된다. `codex` CLI 가 없거나 출력이 예상과 다르면 파일 동기화는 그대로 진행하고, 내보내기는 기존 `plugins.json` 을 유지하며, plugin 복원은 warning 과 함께 건너뛴다. 동기화 상태와 미리보기는 그 table 들을 뺀 `config.toml` 을 비교하고, `plugins.json` 을 실제 설치 상태와 비교한다.

**Herdr sync는 `~/.config/herdr/config.toml`만 추적한다.** `session*.json`, plugin registry state, log, socket, lock 파일 같은 session/runtime 상태는 의도적으로 제외한다. 설정 표에서 Herdr를 켜면 된다. 추적 앱 선택은 자동으로 바꾸지 않는다.

**Skills sync는 `npx skills add -g`로 무엇을 설치했는지를 기록하고, 파일은 복사하지 않는다.** 내보내기는 `~/.agents/.skill-lock.json`을 읽어 각 스킬의 source와, `~/.claude/skills/<name>` / `~/.codex/skills/<name>` 링크가 그 스킬을 가리키는 관리 대상 에이전트(`claude-code`, `codex`)를 `skills/skills.json`에 쓴다. lock 파일에는 GitHub 토큰이 들어갈 수 있어 sync 폴더로는 절대 복사하지 않는다. 가져오기는 로컬에 없는 스킬마다 `npx -y skills add <source> --global --skill <name> --agent <agent> --yes`를 실행하고, 실패·`npx` 없음·GitHub가 아닌 source는 warning으로 알린다. `--copy`로 설치한 스킬은 일반 디렉터리이므로 claude/codex 앱이 파일로 동기화한다.

**BetterTouchTool 은 실행 중이어야 한다.** 내보내기·가져오기·상태 확인 모두 `osascript` 으로 BTT 를 제어하기 때문이고, 자동화 권한이 필요하다(설정이 그 화면을 열어 준다). BTT 가 꺼져 있으면 상태는 `unknown`, 내보내기·가져오기는 에러로 멈춘다. preset 이름은 BTT 이름 그대로 취급되며, 빈 이름, 경로 구분자, 따옴표, 제어문자는 AppleScript 생성 전에 거부된다.

#### 3. 동기화 상태

설정은 추적 중인 앱마다 상태(파일별 sha256 비교)를 보여주고, 다른 앱은 어느 쪽이 더 최신인지와 미리보기와 같은 파일별 요약을 함께 보여준다.

- `clean` — local 과 stored 의 sha256 일치. 단 Claude 의 `installed_plugins.json` 과 `known_marketplaces.json` 은 미리보기와 같은 방식으로 비교해서, Claude Code 가 스스로 고쳐 쓰는 실행 기록(설치 경로, 버전, 시각, 커밋 SHA)은 무시한다
- `dirty` — 다름; 방향은 `local-newer`, `folder-newer`, `diverged` (양쪽 섞임) 중 하나
- `missing` — 한쪽이라도 파일이 없음
- `unknown` — 비교 불가 (예: BTT 미실행)

#### 4. Claude Code 계정과 위젯

사용량 메뉴는 claude.ai 로그인 여러 개를 저장해 두고 Claude Code 가 쓰는 계정을 바꾼다. `/login` 과 같은 효과지만 매번 브라우저를 거치지 않는다. 실행 중인 세션도 `/login` 때처럼 바뀐 계정을 따라간다. 계정별 5시간·주간 사용량과 초기화 시각을 보여주고, 계정 추가, 다시 로그인, 이름 바꾸기, 삭제를 한다.

- 계정 저장은 Claude Code 가 직접 한다. 계정을 추가하면 `CLAUDE_CONFIG_DIR=~/.claude-accounts/<이름>` 으로 `claude auth login` 을 실행하므로 토큰은 그 폴더의 키체인 항목에 남는다.
- 사용은 그 로그인을 Claude 의 기본 키체인 항목에, 계정 정보를 `~/.claude.json` 에 복사한다. 그 전에 지금 쓰던 로그인을 원래 계정으로 돌려놓는다. Claude 는 쓰는 동안 토큰을 갱신하고, 한 번 쓴 갱신 토큰은 다시 못 쓰기 때문이다.
- 토큰은 명령줄이 아니라 표준 입력으로 `security` 에 넘긴다.
- 지금 쓰는 로그인이 저장돼 있지 않으면(처음 쓸 때 등) 앱이 버려도 되는지 묻고, 먼저 저장하는 길도 준다.
- dotsync로 계정을 바꾸기 시작했으면 `/login` 대신 계속 dotsync로 바꾼다. `/login` 은 쓰던 로그인을 돌려놓지 않고 덮어써서, 그 계정은 다시 로그인해야 한다.
- 계정은 동기화와 별개다. sync 폴더가 필요 없고 내보내기·가져오기는 계정을 건드리지 않는다.
- 사용량은 Claude Code 에 직접 묻는다(`claude -p` 의 `get_usage` 요청). 토큰을 쓰지 않는다. 지금 쓰는 계정은 Claude 기본 폴더로, 나머지는 각자 폴더로 동시에 조회하고, 한 계정이 느려도(30초 제한) 나머지는 기다리지 않는다.

위젯 — 위젯 편집에서 추가한다. 바탕화면과 알림 센터 어디에나 놓을 수 있다.

- "Claude 계정"(큰 크기, 아주 큰 크기): 지금 쓰는 계정과 나머지 계정을 함께 본다.
- "지금 사용 중"(중간 크기): 지금 쓰는 계정만 본다.
- "Claude 계정 목록"(큰 크기): 나머지 계정을 일곱 개까지 본다(더 많으면 여섯 개와 "외 n개"). 알림 센터에서 "지금 사용 중" 아래에 두면 둘이 긴 위젯 하나처럼 보인다.

위젯에서 ↻ 는 새로고침(약 10초, 토큰 안 씀), "사용"은 계정을 바꾼다. 앱은 쓸 때만 실행된다. 자동 교체를 켜면 짧은 검사만 뒤에서 돈다(아래). 설치하거나 업데이트하면 실행 중이던 위젯을 끄고 새 위젯이 대신 뜬다. 그래도 위젯이 비어 있으면 앱을 한 번 연다. 앱이 위젯을 다시 그린다.

자동 교체 — 자동 교체 메뉴. 켜기 전에는 꺼져 있다.

- 지금 쓰는 계정이 5시간 90% 또는 주간 98%에 닿으면 저장된 다른 계정으로 바꾼다. 주간 사용률이 가장 낮은 계정(균등 사용)이나 주간 초기화가 가장 가까운 계정(주간 초기화 가까운 순)을 고른다. 두 기준 아래인 계정은 모두 후보다. 제외한 계정은 고르지 않는다.
- claude-hud 가 쓰는 실시간 사용량이 필요하다. claude-hud 설정의 `display.externalUsageWritePath` 를 `~/Library/Group Containers/GR53VV7ZD2.dotsync/live-usage.json` (절대 경로)로 둔다.
- 뒤에 계속 떠 있는 것은 없다. 그 폴더가 바뀔 때마다 LaunchAgent(`com.changja88.dotsync.autoswitch`)가 앱 실행 파일을 잠깐 돌린다. 바꿀 때가 되면 앱을 창 없이 연다. 앱은 모든 계정을 조회하고, 바꾸고, 메뉴의 기록에 남기고, 알림을 보내고 꺼진다. 처음 켤 때 macOS 가 백그라운드 항목을 허용할지 묻는다.
- 바꿀 계정이 없거나 교체가 실패하면 15분, 조회해 보니 기준 아래였으면 5분 쉰다.
- 켜져 있는 동안은 앱 창과 위젯에서 "사용"이 사라진다. 손으로 바꾸려면 끄고 바꾼다. Claude 의 `/login` 은 막을 수 없다. dotsync 에 저장 안 된 로그인이면 저장할 때까지 기다린다.
- 업데이트하면 백그라운드 항목이 지워진다. 새 앱을 열면("Open Anyway" 뒤) 다시 올라간다.

#### 5. Claude Code와 Codex 세션 정리

세션 메뉴는 Claude Code와 Codex가 디스크에 쌓아 둔 세션을 보고, 지우고, 되돌린다. 세션은 sync 폴더가 필요 없고, 내보내기·가져오기는 세션을 건드리지 않는다.

- 실행 중이거나 쓰는 중인지 알 수 없는 세션은 지우지 않는다.
- **Claude Code** — 최근 30분 안에 기록된 세션도 지우지 않는다. 지우기 직전에 한 번 더 확인하고, 그사이 바뀐 세션은 건너뛴다. 삭제하면 대화 기록, 하위 에이전트 기록, 도구 결과, MCP 작업 기록, 파일 되돌리기 기록, 세션 환경, 디버그 로그, 첨부한 파일, 임시 이미지·작업 폴더가 지워진다. 입력 기록, 할 일 목록, 계획, 프로젝트 기억(memory), 설정, 다른 세션이 쓰는 도구 결과는 남는다(여러 세션이 같이 쓰는 파일이라 세션별로 골라 지우지 않는다). 삭제가 중간에 멈추면 "지우다 멈춤"으로 보이고, 되돌리거나(대화 기록 이름을 되돌린다) 마저 지울 수 있다(남은 것을 지운다). 그냥 두어도 마지막 기록에서 30일이 지나면 Claude Code가 같은 범위를 지운다.
- **Codex** — 지우기 전에 열려 있는 세션을 배치마다 한 번 확인한다. 삭제는 Codex 자체의 `codex delete`라서 Codex를 켜 둔 채로 할 수 있고, 다른 창에서 열린 세션은 Codex가 거절한다. 대화 기록, 대화 저장소의 해당 내용, 이름 목록, 로그가 지워지고, 그 세션에서 갈라져 나온 하위 세션도 같이 지워진다. 이어서 dotsync가 참조 캐시 파일을 지운다. 입력 기록, 첨부 파일, 데스크톱 앱 상태, 생성 이미지는 남는다. 대화 저장소의 빈 공간은 바로 줄지 않는다.
- **Codex 남은 데이터 정리**는 지운 세션이 Codex 파일에 남긴 것(`history_rows`, `history_lines`, `session_index`, `queue`, 그리고 캐시 파일)을 지우고, 대화 저장소의 빈 공간도 돌려받을 수 있다(vacuum). `~/.codex`를 쓰는 것이 없을 때만 실행된다. dotsync.app은 정리하는 동안 ChatGPT.app을 끄고 끝나면 다시 켜지만, 터미널 Codex는 대신 닫아 주지 않으며 실행 중이면 정리를 거절한다. 무엇을 남겨야 할지 확신할 수 없을 때도 통째로 거절한다: 기록 파일을 읽을 수 없거나, 압축된 기록이 있는데 `zstd`가 없거나, Codex 데이터베이스 버전이 dotsync가 아는 것과 다를 때다.
- `.zst`로 압축된 Codex 기록은 `zstd` 명령으로 읽는다. Homebrew Formula가 설치한다.
- 삭제, 되돌리기, 정리는 멈춘 경우에도 로그를 남긴다: `~/Library/Logs/dotsync/sessions-<timestamp>-<pid>.json`. 세션 id, 파일 경로, 결과만 담고 대화 내용(제목, 대화·입력 기록 텍스트)은 담지 않는다.
- 실행 중인 삭제나 정리를 멈추면(`SIGTERM`) 다음 안전한 지점(세션 사이, 정리 항목 사이, vacuum 배치 사이)에서 멈추고, 한 일을 알리고 로그에 남긴다.

#### 6. `dotsync.toml`

`dotsync.toml` 에는 `apps` 와 `[options.<app>]` table 만 둔다. `[options]` 아래의 다른 key 는 설정 오류로 거부한다. 최상위 관리 파일·디렉터리가 symlink 이면 따라가지 않고 거부한다. Claude `skills/` 나 Codex `rules/` 처럼 미러링하는 디렉터리 안의 symlink 항목(예: `npx skills add` 가 만든 링크)은 warning 과 함께 건너뛰며, 읽거나 쓰지 않고 가져오기 때도 그대로 둔다. `skills` 앱을 추적 중이면 `npx skills add` 가 만든 링크는 그 앱이 기록하므로 warning 없이 건너뛴다. 미러링하는 디렉터리 안의 `.DS_Store` 와 `*.bak` 파일(파일을 고쳐 쓰기 전에 사본을 남기는 도구가 만든 것)도 같은 방식으로 무시한다. 저장하지도, 지우지도 않는다.

지원 앱: `claude`, `codex`, `herdr`, `skills`, `ghostty`, `bettertouchtool`, `zsh`

#### 개발자용: JSON 규약

dotsync.app은 아래 명령을 실행하고 그 답을 읽는다.

```bash
dotsync status --json
dotsync push|pull <app>|--all --dry-run|--yes --json   # 미리보기, 또는 적용
dotsync config dir --json -- <folder>
dotsync config apps <app,app,…> --json
dotsync account login|use [--yes]|rename|remove|usage --json -- <args>
dotsync sessions list|delete|recover|cleanup … --json
dotsync --version
```

- 모두 stdout에 JSON 객체 하나만 출력하고, 나머지는 stderr로 보낸다. 실패는 `{"error": {"code": …, "message": …}}`와 종료 코드 1이다. `--json` 없이 실행하면 dotsync.app이 실행하는 명령이라고 알리고 종료 코드 2로 끝난다.
- 오래 걸리는 세션 명령은 stderr에 `dotsync-progress <done>/<total>` 줄을 쓰고, 앱이 이걸로 진행 막대를 그린다.
- 같은 도구를 바꾸는 명령끼리는 `~/Library/Application Support/dotsync/locks/`의 락 파일로 서로 기다린다: `claude-sessions.lock`(`delete claude`, `recover claude`), `codex.lock`(`delete codex`, `cleanup codex --yes`. `codex`의 push/pull/status는 공유로 잡는다). 계정 명령은 `~/.claude-accounts/.lock`을 잡는다. 10초 안에 락이 풀리지 않으면 `busy`로 실패한다.

### 새 앱 추가

`docs/adding-an-app.md` 참고. 단순 파일 기반 앱은 모듈 1개 + `apps/__init__.py`에 한 줄로 끝난다. 복잡한 앱(외부 프로세스, 앱별 옵션)은 base의 hook을 override해서 같은 자리에서 확장한다.

---

## License

MIT
