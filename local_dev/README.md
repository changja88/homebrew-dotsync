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

Interactive no-argument `codex` / `claude` launches show a single ANSI
preflight box from the Python launcher: workspace, Serena project status,
machine-wide Serena MCP inventory, Graphify status (4 rows: global / graph /
integration / hook), context, session inventory, memory inventory, and the
cleanup criteria. After Run/Abort confirmation (and an optional
Initialize/Skip prompt when `.serena/project.yml` is absent), the launcher
runs cleanup and starts the scoped Serena MCP server with inline progress rows
below the preflight box. When the agent TUI exits, a summary box reports
session duration, cleanup result, MCP lifecycle, and any accumulated warnings.

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

The session and memory rows are computed in Python from the current launcher
context, not predicted by the zsh shim:

| Context | Sessions | Memory | Cleanup criteria |
|---|---|---|---|
| `codex` | `$CODEX_HOME/sessions` (`~/.codex/sessions` by default), recursive `*.jsonl` files whose `session_meta.payload.cwd` matches the current working directory | `$CODEX_HOME/memories` (`~/.codex/memories` by default) | Delete matching sessions older than 3 days; reset all Codex memory files. |
| `claude` | `~/.claude/projects/<encoded cwd>/*.jsonl` | `~/.claude/projects/<encoded project root>/memory` | Delete project sessions older than 3 days; reset all Claude memory files for the project. |

Preflight displays each row as `client total . to delete/reset . to keep`, and
the final summary uses `N sessions deleted . M memory files reset`.

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
