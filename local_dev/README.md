# Local Dev Tooling

> **Scope.** This directory is internal-only development tooling that lives in
> this checkout for convenience. It is **unrelated to the `dotsync` CLI**: it
> ships nothing through the Homebrew formula, exposes no `dotsync` user-facing
> behavior, and shares no runtime code with `lib/dotsync/`. Treat it as a
> separate small project that just happens to be co-located.

## Two locations: dev (here) and runtime (stable)

Develop here, run from a stable directory. `make install-shim` mirrors the
launcher tree to a long-lived path before patching `~/.zshrc`. The
`SERENA_AGENT_LAUNCHER` line in `~/.zshrc` points inside `$STABLE_DIR` — never
at this checkout — so moving or deleting this repo does **not** break the
installed `claude` / `codex` shim functions. `SERENA_AGENT_PYTHON` points at a
durable system-managed interpreter (Homebrew/python.org, first choice
`/opt/homebrew/bin/python3.12`), **not** a generated venv: uv-managed
standalone pythons get garbage-collected and would leave `SERENA_AGENT_PYTHON`
dangling (the v0.1.x uv-3.13 breakage). The launcher is stdlib-only, so any
3.12+ works.

| | Path | Purpose |
|---|---|---|
| Dev source | `local_dev/serena_mcp_management/` (this dir) | Edit, test, iterate. |
| Runtime mirror | `~/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/` | What `~/.zshrc` actually executes. |
| Runtime interpreter | `/opt/homebrew/bin/python3.12` (auto-detected) | The stdlib-only Python that runs the launcher (`SERENA_AGENT_PYTHON`). |

The mirror copies the source tree exactly (same `local_dev/serena_mcp_management/`
layout) because `serena_agent_launcher.py` resolves its package root via
`Path(__file__).resolve().parents[2]` and imports `local_dev.serena_mcp_management.*`.
Preserving the depth keeps imports working without code changes.

Override the runtime location with `STABLE_DIR=...` if you want a different
stable home.

## External CLI prerequisites (serena / graphify)

The launcher shells out to two external CLIs. Neither is assumed to be on
PATH — `external_cli.py` resolves each one as **PATH → `~/.local/bin` (uv
tool bin)**, and the managed zshrc block prepends `$HOME/.local/bin` to PATH
so agent sessions see the same CLIs.

**Self-install prompts.** CLI가 해석되지 않으면 interactive preflight가 설치
여부를 직접 묻는다 (`uv tool install …`, default Yes) — serena CLI는
Initialize 프롬프트 직전에 항상, graphify CLI는 graphify 행 중 하나라도
missing일 때 graphify 질문들 직전에. 거절하면 아래의 degrade 동작이 그대로
적용되고(graphify 질문들은 통째로 skip), `uv` 자체가 없으면 묻지 않고 경고
행만 남긴다. 설치가 도는 동안 uv의 패키지 벽 출력은 캡처해 숨기고 spinner
행 하나에 마지막 진행 줄(패키지 1개)만 갱신해 보여준다 — 캡처한 전체 출력은
설치가 실패했을 때만 들여쓰기로 풀어 남긴다. 디자인 문서:
`docs/cli-self-install-prompt-spec.md`.

| CLI | Install | Resolution rules |
|---|---|---|
| `graphify` | `uv tool install graphifyy` | Direct binary only. **No uvx fallback**: graphify writes its own absolute path into project hooks (`.codex/hooks.json` 등), so an ephemeral uvx cache path would rot there after `uv cache clean`. |
| `serena` | `uv tool install --from "git+https://github.com/oraios/serena" serena-agent` | One-shot commands (`serena project create`) fall back to `uvx --from git+…oraios/serena` when no direct binary exists. The **long-running scoped server requires a direct binary** so registry identity, direct-child ownership, health checks, and final shutdown all refer to the real server process rather than an intermediate wrapper. |

When the serena CLI is unresolvable the launcher degrades gracefully: the
Initialize prompt still works (uvx), and the scoped-server phase prints
`! serena unavailable …` and launches the bare agent instead of crashing.

Graphify preflight paths follow graphifyy 0.8.x behavior: the codex
user-level skill lives at `~/.codex/skills/graphify` (claude:
`~/.claude/skills/graphify`).

## Serena MCP Management

`serena_mcp_management/` contains the local Serena MCP launcher, zsh shim
generator, and scoped server lifecycle code used for Codex and Claude
development sessions.

Serena is an exact project opt-in: the nearest project/worktree root must
contain `.serena/project.yml`. Without that marker the launcher starts the real
Codex or Claude binary without creating Serena state or attempting a server.
For an opted-in root, the sharing key is the canonical worktree path plus the
fixed `dotsync-shared-cli-v1` profile; the client type is deliberately not part
of the key. Every Codex and Claude process launched from the same worktree
therefore shares one Serena server/proxy generation, while different
worktrees—even from the same repository—use independent generations.

Each launcher owns one lease. Exiting a client releases only that lease; the
server, proxy, and watchdog remain while any lease survives and stop when the
last lease is released. Registry, lock, and log state lives in a private
per-user runtime/cache directory outside the repository. Process-table
discovery is diagnostic-only: termination requires a private registry record
with PID identity or direct child ownership.

The managed zsh flow is:

```text
~/.zshrc
  -> $STABLE_DIR/local_dev/serena_mcp_management/serena_agent_launcher.py
  -> $STABLE_DIR/local_dev/serena_mcp_management/serena_mcp/
  -> real codex or claude binary
```

Interactive no-argument `codex` / `claude` launches and session-management
commands (`codex resume|fork`, `claude -c|--continue|-r|--resume`) show a single
ANSI preflight box from the Python launcher: workspace, Serena project status,
machine-wide Serena MCP inventory, Graphify status (4 rows: global / graph /
integration / hook), context, grouped agent memory inventory, and one grouped
global session inventory that contains its cleanup condition and candidate
counts. After setup prompts (including an optional Initialize/Skip prompt when
`.serena/project.yml` is absent), the launcher shows the same default-safe
keep/reset choice for Claude and Codex. A confirmed reset is product-wide and
has no per-session preserve list. The launcher performs the selected
product-scoped action and starts the scoped Serena MCP server with inline
progress rows below the preflight box.
When the agent TUI exits, a summary box reports session duration, cleanup
result, MCP lifecycle, and any accumulated warnings.
Non-interactive commands (`codex exec`, `claude -p`, help/version) and Claude
calls that explicitly supply their own `--settings` bypass the launcher.

Pressing Ctrl+C at any pre-launch prompt cancels the whole launcher. The active
prompt is removed, the terminal state is restored, and the launcher prints one
row before returning exit code `130` without a Python traceback:

```text
  ! cancelled
```

### Startup memory and session choices

#### Codex

Interactive Codex launches have a two-stage combined reset flow and no
independent memory question. The first choice is default-safe:

```text
? Reset Codex sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Delete all sessions, memories, and conversation traces
```

Keeping sessions and memories is an exact no-op and does not run the former
five-day Codex cleanup. Choosing the destructive option does not open a session
picker. It asks for one final confirmation:

```text
Permanently delete ALL Codex sessions, memories, history, logs, snapshots,
and currently running sessions? The Codex app will be restarted if it is open.
[y/N]
```

A confirmed reset deliberately has no preserve list. The launcher first stops
detected Codex CLI and app-server runtimes. If Codex Desktop is open, the
launcher temporarily closes it so cached state cannot recreate deleted traces,
then reopens it after verification.
Runtime discovery and identity-pinned termination repeat before and after
mutation. If any Codex runtime keeps respawning, the reset fails instead of
reporting a clean state.

The reset covers the default `~/.codex` and the active absolute `$CODEX_HOME`.
It also honors
safe `sqlite_home`, `CODEX_SQLITE_HOME`, and `log_dir` locations from the user
config, every user profile, `/etc/codex/config.toml`, trusted project config
layers, current `-c` / `--config` overrides, and detected running Codex
invocations. `-C` / `--cd` and each running process working directory are used
when resolving relative paths. The preflight inventory still counts logical
sessions from rollout JSONL and `state_<n>.sqlite` so the result can report how
many existed, but deletion does not depend on selecting every ID.

After confirmation the launcher:

1. removes `sessions/` and `archived_sessions/` in every known Codex home;
2. removes complete SQLite conversation stores and their
   WAL/SHM/rollback-journal companions: `state_<n>.sqlite`,
   `goals_<n>.sqlite`, `memories_<n>.sqlite`, and `logs_<n>.sqlite`;
3. removes generated memory and history:
   `memories/`, `memories_extensions/`, and `history.jsonl`;
4. removes recognized logs, snapshots, visualizations, ambient suggestions,
   and chat-process state, plus the documented Codex Desktop logs under
   `~/Library/Logs/com.openai.codex/`; external configured log directories
   retain unrelated files;
5. surgically removes prompt history, drafts, queued follow-ups, unread thread
   IDs, permissions, and per-thread UI state from Desktop global-state files
   while preserving model, project, window, onboarding, and other app settings;
6. clears Desktop runtime tables from `sqlite/codex-dev.db`, preserves
   automation definitions and app-server feature enablement, then applies
   SQLite secure deletion, WAL checkpointing, and `VACUUM`; and
7. rescans every target and fails the reset if any old session row, rollout,
   memory, log, snapshot, or desktop thread row remains.

The launcher preserves `config.toml`, `auth.json`, plugins, skills, automation
definitions, feature enablement, and unrelated user files. A symlink in a
Codex-home or configured-root path fails closed; a final recognized reset
target that is itself a symlink is unlinked without following it. A recognized
file target with the wrong type is preserved and makes the reset fail. A failed
reset aborts the new Codex launch, so it cannot masquerade as a clean start.
This hard reset intentionally does not loop over the official per-session
[`codex delete --force <UUID>`](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-delete)
command: that command deletes one saved session, whereas this option is the
explicit product-wide reset requested by the user.

#### Claude

Interactive Claude launches use the same combined, default-safe choice:

```text
? Reset Claude sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Delete all sessions, memories, and conversation traces
```

Keeping state is an exact launcher-level no-op. The launcher no longer injects
`cleanupPeriodDays: 5`; Claude follows the user's own settings and native
defaults. The destructive choice requires a second default-no confirmation:

```text
Permanently delete all known local Claude Code sessions, memories, history,
generated traces, and currently running CLI sessions? [y/N]
```

After confirming, the launcher capability-probes the real Claude binary for
`project purge --all --yes`, stops the Claude Code daemon and remaining local
CLI/background processes, and invokes that official command. Per the official
[Claude directory documentation](https://code.claude.com/docs/en/claude-directory),
the purge owns project transcripts and default auto-memory, tasks, debug logs,
file history, prompt history, and generated project entries in Claude's mixed
global JSON. Claude documents exit status `1` when no project state matches.
The launcher treats only that exit code as an idempotent no-op, and only after
the official directories and global project mapping independently verify
empty. The command does not cover every generated trace needed by this
launcher's stricter reset contract, so a verified official postcondition is
followed by deletion of this fixed allowlist beneath the active config
directory:

```text
agent-memory/  plans/       paste-cache/       image-cache/
session-env/   shell-snapshots/  sessions/     feedback-bundles/
todos/         logs/
```

The reset also removes the generated memory store at the exact valid path named
by the user-scope `autoMemoryDirectory` setting. It never rewrites that setting:
`$CLAUDE_CONFIG_DIR/settings.json` must remain byte-for-byte unchanged. Default
project memory under `projects/*/memory` is handled by the official purge.

User-scope authored configuration remains: settings, authentication, plugin
enablement/configuration and persistent `plugins/data/`, skills, commands,
hooks, agents, MCP configuration, policies, managed/remote settings, the
non-project values and unrelated files in `backups/`, and usage/statistics
caches. Claude-managed plugin caches, marketplace clones, and registry metadata
may refresh while the real CLI runs. Repository `.claude/`
directories are not traversed. Claude's mixed global JSON is preserved except
for the official purge's generated `projects` entries and the volatile
experiment/feature-flag caches (`cachedExperimentData`,
`cachedExperimentFeatures`, `cachedGrowthBookFeatures`, and
`cachedGrowthBookFeaturesAt`); all other pre-existing non-project top-level
values must still match after reset. A user-scope custom
memory path is validated against broad paths, traversal, and symlink components
before deletion. Shared/system/temp roots and shallow volume roots are rejected;
recursive deletion pins every ancestor and target by file descriptor and inode
so a concurrent rename or symlink swap cannot redirect it. Claude does not
accept `autoMemoryDirectory` from project or
local settings. It can accept a higher-precedence managed policy, so the
launcher inspects file-based managed settings, managed drop-ins, the macOS
managed-preferences domain, and the recognized
`$CLAUDE_CONFIG_DIR/remote-settings.json` server-policy cache. If any source
defines `autoMemoryDirectory` or a dynamic `policyHelper`, the reset fails
closed because the effective external memory root cannot be proven safely. The
policy check runs before mutation, immediately after the official purge, and
again before reporting success in case server policy changes while the command
runs. Invocations with an explicit `--settings` override continue to bypass
this interactive reset flow.

Claude's `plansDirectory` can redirect generated plans to a repository-relative
path that may also contain user files, and historical custom locations have no
complete central index. The launcher therefore fails before mutation when this
setting is found in user or managed settings, the current repository, or a
project root still discoverable from current/global backup metadata. It never
guesses at or recursively searches repositories. For linked worktrees it also
checks the current worktree top level and the git common/main checkout because
Claude can read settings from both. The normal default
`$CLAUDE_CONFIG_DIR/plans/` store is deleted by the fixed allowlist above.

`backups/` contains Claude-generated migration snapshots of the mixed global
configuration, not conversation bodies or auto-memory. Claude keeps at most
five and may rotate an old recognized backup out while a command runs. The
reset tolerates that documented rotation, preserves non-project values in every
surviving pre-existing backup, and removes generated `projects` mappings from
all recognized backups present afterward because those mappings can retain
`lastSessionId` and session statistics.

Before mutation the launcher also records content manifests for named
user-authored roots and high-value local state, including credentials,
`plugins/data/`, skills, commands, hooks, agents, rules, themes, workflows,
keybindings, `CLAUDE.md`, MCP files, and `stats-cache.json`. The same manifests
must match after the official purge and at final verification; recognized
remote/policy caches are covered as well. Reads and recursive deletes are
descriptor-anchored and reject concurrent inode or content changes.

The reset applies only to local Claude Code CLI state in the active
`CLAUDE_CONFIG_DIR`. It does not close Claude Desktop and does not claim to
erase Claude.ai, web, Desktop, VS Code, or remote-session history. Any missing
purge capability, unsafe path, process that cannot be stopped, unexpected
non-zero purge, changed setting, unsupported custom plan store, or residual
state fails closed and aborts the new Claude launch. The documented no-match
exit status also fails if any official state remains.

Ctrl+C at any pre-launch prompt leaves state unchanged, does not launch a
child, prints the existing `! cancelled` row, and returns exit code `130`
without a traceback. Non-interactive bypass commands show no destructive
prompt and never opt into either product's full reset.

`serena project create` (run on Initialize) is **captured, not streamed**: its
verbose language detection, the interactive language prompts auto-answered via
`yes ""`, the stale last-project "skipping" notice, and the Pydantic-on-3.14
`UserWarning` (silenced via `PYTHONWARNINGS=ignore`) would otherwise flood the
box UI. On success the launcher prints a single `serena  project created` row;
on failure it dumps the captured output indented for diagnosis.

Graphify hooks are git `post-commit`/`post-checkout` hooks, so the hook step
requires a git repo. When the project isn't one yet, the launcher swaps the
"Install graphify hooks?" prompt for a one-line `git init` consent (default
Yes); accepting it runs `git init` and then installs the hooks, while declining
skips the hook step with a "needs a git repo — run `git init` first" note.

An early preflight step checks the **Node.js runtime** — it runs *before* the
graphify section so the graphify CLI being unavailable (which early-returns that
section) can't skip it; node and graphify are independent concerns.
context7/playwright MCP servers run via `npx` and the claude-hud statusLine runs
via `node`; all fail at startup with `os error 2` when node is absent. The
launcher scans the active
client's configured commands — the claude statusLine, each enabled plugin's
`.mcp.json`, and `.claude.json` mcpServers for claude; `~/.codex/config.toml`'s
`[mcp_servers.*]` for codex — and classifies the need into two kinds
(`node_preflight.NodeNeed`):

- **generic** — an `npx`/`node` command that *any* node on PATH satisfies
  (npx-based MCP servers).
- **homebrew** — a command that hardcodes `/opt/homebrew/bin/node` (the
  claude-hud statusLine), which only a node at that *exact* path satisfies. A
  PATH node elsewhere (e.g. nvm) does not make the HUD work.

The two are resolved independently (`node_command` vs `homebrew_node_command`),
so a machine with nvm node but no homebrew node is still offered an install for
the HUD. Only when an *unmet* need exists does the step act, and — mirroring the
serena/graphify CLI prompts — it checks Homebrew is available *before* asking:
no brew, no prompt, just a "brew not found — install node manually" note.
Otherwise it offers a one-line `brew install node` consent (default Yes); brew
node lands at `/opt/homebrew/bin/node`, exactly where the statusLine looks.
Clients with no node-based plugin/MCP are never prompted. Detection lives in
`node_preflight.py`; node resolution/install argv in `external_cli.py`.

> **Known limitation (Apple Silicon assumption).** The homebrew need is checked
> at the literal `/opt/homebrew/bin/node` because that is what the claude-hud
> statusLine hardcodes. On Intel macs (`brew --prefix` = `/usr/local`) a
> `brew install node` lands elsewhere, so the homebrew need stays unmet and the
> HUD can't be fixed from here regardless — the same hardcode is claude-hud's,
> not ours. npx-based MCP (the generic need) still works on Intel via PATH.

The session row is computed in Python from one immutable inventory snapshot and
reused for cleanup; the zsh shim does not predict counts:

The machine-wide Serena MCP row uses full user-facing names rather than process
table abbreviations:

```text
✓ serena mcp  server processes[3] → managed servers[3] · orphaned servers[0] · leases[4] · stale leases[0]
```

| Context | Session scope | No explicit selection | Explicit deletion |
|---|---|---|---|
| `codex` | Logical sessions from rollout JSONL and read-only `state_<n>.sqlite` thread rows across `~/.codex` and the active `$CODEX_HOME`, including archived and state-only entries. Linked descendants and copies count once for pre-reset reporting. | Exact no-op; no automatic five-day deletion. | One confirmed hard reset stops detected CLI/app-server runtimes, temporarily restarts an open Desktop app, and removes every known session, state, memory, history, log, snapshot, and desktop thread record. There is no per-session preserve list. Config, auth, plugins, skills, app preferences, and automation definitions remain. |
| `claude` | Top-level session JSONL files for every project under `$CLAUDE_CONFIG_DIR/projects` (or `~/.claude/projects` when unset). Subagent files are counted with their parent for pre-reset reporting. | Exact launcher-level no-op; no injected retention setting or automatic launcher deletion. | One confirmed reset stops local Claude Code CLI/daemon runtimes, runs official `project purge --all --yes`, removes fixed supplemental generated-data targets, sanitized backup `projects` mappings, and the validated user-scope custom memory store, then verifies no state remains. Settings, `autoMemoryDirectory`, auth, plugins, skills, commands, hooks, agents, MCP config, policies, non-project backup values, and repository `.claude/` data remain. A managed memory redirect fails closed before mutation. |

Session counts are grouped by data type under one top-level row. Normal
preflight rows read:

```text
· sessions    codex
              ├─ groups   58 total · 0 to delete · 58 to keep
              ├─ records  855 total · 0 to delete · 855 to keep
              └─ cleanup  full reset on confirmation · no automatic deletion
```

```text
· sessions    claude
              ├─ records  108 total · 0 to delete · 108 to keep
              └─ cleanup  full reset on confirmation · no automatic deletion
```

Session/reset prompt and action rows are yellow. The inventory tree keeps its
existing detail palette: total segments are pink, the cleanup condition is
purple, delete segments are yellow, keep segments and child labels are mint,
and tree glyphs are gray. The final summary reports
`N sessions deleted · M conversation-state targets reset` after either product's
reset, or `sessions and memories kept` when no reset was confirmed. A failed or
partially completed reset reports its exact error and aborts the child launch;
there is no claim of rollback across the official CLI and filesystem deletion.

## Workflow

```bash
cd local_dev
# Edit code under serena_mcp_management/ ...
make install-shim
dotsync from   # ~/.zshrc 변경을 dotsync sync 폴더본에도 반영 (안 하면 다음 `dotsync to`가 옛 managed block을 되살린다)
exec zsh   # reload claude/codex shell functions
```

`install-shim` is the only command. It rsyncs the dev tree to `$STABLE_DIR` and
rewrites the managed block in `~/.zshrc` in one step, pointing
`SERENA_AGENT_PYTHON` at a durable system-managed interpreter chosen by
`serena_zsh_shim.py`'s `default_python_executable()` (Homebrew/python.org,
first choice `/opt/homebrew/bin/python3.12`). No venv is created — uv-managed
standalone pythons get pruned and would leave the path dangling. The launcher
is stdlib-only, so any Python 3.12+ works. Override the recorded interpreter
with `PYTHON_EXECUTABLE=/path/to/python3.12` if you need a specific one.

If you ever need to remove the managed block (e.g. retiring this tool), the
prior `~/.zshrc` is auto-backed up to `~/.zshrc.dotsync-serena.bak` on every
install — restore that, or run the shim renderer with `--uninstall-zshrc`
directly:

```bash
python3 "$STABLE_DIR/local_dev/serena_mcp_management/serena_zsh_shim.py" \
  --uninstall-zshrc --rc-path ~/.zshrc
```

## Tests

Run the local dev tests with:

```bash
../.venv/bin/python3 -m pytest local_dev/tests -q
```

Run the public dotsync tests with:

```bash
../.venv/bin/python3 -m pytest tests -q
```

(Both invocations are run from the repo root; the relative paths above assume
you're inside `local_dev/`.)
