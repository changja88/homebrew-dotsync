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
| `serena` | `uv tool install --from "git+https://github.com/oraios/serena" serena-agent` | One-shot commands (`serena project create`) fall back to `uvx --from git+…oraios/serena` when no direct binary exists. The **long-running scoped server requires a direct binary** — uvx keeps the real server as a child process, so the registry would record the wrapper pid and same-scope orphan cleanup would kill its own server. |

When the serena CLI is unresolvable the launcher degrades gracefully: the
Initialize prompt still works (uvx), and the scoped-server phase prints
`! serena unavailable …` and launches the bare agent instead of crashing.

Graphify preflight paths follow graphifyy 0.8.x behavior: the codex
user-level skill lives at `~/.codex/skills/graphify` (claude:
`~/.claude/skills/graphify`).

## Notification guard

launcher는 매 관리 launch 시작 시 알림 설정 불변식을 점검하고 드리프트를
자동 수리한다 (`notification_guard.py`, 설계: `docs/notification-guard-spec.md`).
알림 정책: **입력 필요·메인 작업 완료 시에만, 포커스 무관 항상** — 서브에이전트
완료 알림 금지, 벨(terminal bell) 계열 설정은 사용자 관리라 가드가 관여하지 않는다.
대상: codex `notify = []`·permission_request 훅 비활성(auto_review 및 legacy
guardian_subagent 자동 검토 구성)·**subagent_start/subagent_stop 훅 비활성(무조건 — 서브에이전트 완료 알림
금지의 실질 보장 장치, hooks.json이 없는 홈은 공허 충족으로 조용히 통과)**,
claude 알림 채널, orca 알림 토글(`enabled`·`agentTaskComplete` ON,
`suppressWhenFocused` OFF — 경고만). codex hooks 점검은 orca 관리 홈뿐 아니라
**user 홈(`~/.codex`)에도 적용**된다 — orca 07-23 업데이트 후 codex 패널이
user 홈으로 실행되고 `~/.codex/hooks.json`이 설치되기 때문.
정상이면 출력이 없고, 수리/경고 시에만 `notif guard` 행이 표시된다.
비대화식 호출(`codex exec` 등)과 orca **worktree 패널**(`bash -lc … exec codex`
형태로 zsh shim을 우회)은 launcher를 거치지 않으므로 가드 실행 시점 밖이다 —
가드는 interactive launch 때마다 전체 config를 수렴시켜 이를 보완한다.

## Serena MCP Management

`serena_mcp_management/` contains the local Serena MCP launcher, zsh shim
generator, and scoped server lifecycle code used for Codex and Claude
development sessions.

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
`.serena/project.yml` is absent), the launcher collects independent memory and
session choices, performs the selected product-scoped actions, and starts the
scoped Serena MCP server with inline progress rows below the preflight box.
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

Every interactive launch asks both questions below. Claude uses the same exact
wording with `Claude` substituted for `Codex`:

```text
? Delete Codex auto-memory before launch?
  ▶ Keep all memory (default)
    Delete all Codex auto-memory

? Delete Codex sessions before launch?
  ▶ No full deletion — automatic cleanup after 5 days (default)
    Delete all inactive sessions — running sessions are preserved
```

The memory default keeps all memory. The session default means no full
deletion, not no deletion: sessions strictly older than five days still use the
normal cleanup path. Choosing explicit session deletion removes every safely
identified inactive session for the selected product regardless of age while
preserving sessions proven to be running. A Codex launch touches only Codex
memory and sessions; a Claude launch touches only Claude memory and sessions.
Memory prompts and action rows use purple, while session prompts and action rows
use yellow.

The launcher records both answers before any mutation. Ctrl+C at either question
therefore leaves memory and sessions unchanged, does not launch a child, prints
the existing `! cancelled` row, and returns exit code `130` without a traceback.
There is no separate `Cancel` option. Non-interactive bypass commands show
neither prompt and never opt into full memory or session deletion.

`Delete all <product> auto-memory` explicitly deletes the selected product's
complete main auto-memory scope before session cleanup; the launcher never
deletes memory by age or without this selection:

- **Codex:** the exact `memories/` directory under every known Codex home —
  the default `~/.codex`, the active absolute `$CODEX_HOME`, and Orca's managed
  runtime home at
  `~/Library/Application Support/orca/codex-runtime-home/home` — with duplicate
  homes collapsed. `memories_extensions/`, sessions, and other Codex state are
  outside this scope.
- **Claude:** every direct
  `$CLAUDE_CONFIG_DIR/projects/<project>/memory/` directory (or the equivalent
  paths below `~/.claude/projects` when unset), plus the exact valid custom
  directory configured by `autoMemoryDirectory`. Subagent `agent-memory/`,
  transcripts, instructions, and other Claude state are outside this scope.

Deletion always rescans and validates the complete scope immediately before
mutation. A warning-free inventory with zero stores is a successful no-op: no
process scan is needed, and the launcher continues to the selected session
policy and agent launch with a `0 stores · 0 files deleted` result. For a
non-empty inventory, another real native or official Node process for the same
product blocks explicit memory deletion; ChatGPT/Claude GUI helper processes do
not. The failure names up to three representative PID/executable pairs and
summarizes any remainder. If a process conflict, scan, safety validation, or
filesystem deletion fails, the launcher reports the failure and continues to the
selected session policy and agent launch. The failed deletion is never reported
as successful; partial deletion keeps its exact counts and is not automatically
backed up. Cleanup choices affect cleanup only — after both questions have been
answered, only an explicit cancellation or a launch/setup failure prevents the
agent from starting.

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

| Context | Session scope | Default five-day retention | Explicit all-inactive deletion |
|---|---|---|---|
| `codex` | Logical top-level sessions across `~/.codex`, the active `$CODEX_HOME`, and Orca's managed Codex home. Root/descendant rollouts and hard-linked bridge copies count once; the newest member controls retention. | Open or concurrently changed groups are kept. Eligible roots are deleted source-home first through the official `codex delete --force <UUID>` command in each owning Codex home. JSONL and SQLite are never edited directly. | A fresh all-inactive scan ignores age, preserves every open logical group, revalidates paths and fingerprints, and deletes each inactive group only through official `codex delete --force <UUID>` calls. |
| `claude` | Top-level session JSONL files for every project under `$CLAUDE_CONFIG_DIR/projects` (or `~/.claude/projects` when unset). Subagent files are counted with their parent. | The child process receives the official execution-only setting `--settings '{"cleanupPeriodDays":5}'`; Claude Code performs its native startup sweep. | A fresh scan builds bounded bundles only for exact valid session UUIDs across the supported transcript, subagent/tool-result, file-history, session-env, tasks, and debug roots. It preserves bundles proven active by validated running-session markers or open files, revalidates the complete manifest, never follows symlinks, and never uses project purge. |

The cutoff is strictly older than `5 * 24h`; a session exactly on the cutoff is
kept. The five-day rule applies only to sessions: it never deletes Codex or
Claude auto-memory, and Codex `archived_sessions` remain outside session
cleanup. Explicit Claude deletion removes only complete, unchanged inactive
UUID bundles; it leaves memory, settings, credentials, unrelated project files,
and Claude's session-marker files untouched. After either session choice, the
normal five-day policy remains configured for the new child. Session counts are
grouped by data type under one top-level row. Normal preflight rows read:

```text
· sessions    codex
              ├─ groups   58 total · 35 to delete · 23 to keep
              ├─ records  855 total · 358 to delete · 497 to keep
              └─ cleanup  inactive longer than 5 days
```

```text
· sessions    claude
              ├─ records  108 total · 75 to delete · 33 to keep
              └─ cleanup  inactive longer than 5 days · native Claude cleanup
```

Interactive memory prompt/action rows are purple and session prompt/action rows
are yellow. The inventory tree keeps its existing detail palette: total segments
are pink, the cleanup condition is purple, delete segments are yellow, keep
segments and child labels are mint, and tree glyphs are gray. The final summary
reports `N sessions deleted` for default Codex retention,
`native retention 5d . N eligible` for default Claude retention, or
`N sessions deleted · M running preserved` after explicit session deletion.
If explicit cleanup fails after mutation starts, its immediate yellow row keeps
fully deleted logical sessions separate from completed member/root operations
inside the incomplete session. It names up to three affected members or paths,
adds `+N more` for any remainder, prints the exact failure, and continues to
launch without claiming rollback. A strict inventory failure likewise shows up
to three concrete path/reason warnings plus a remainder count before launch.

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
