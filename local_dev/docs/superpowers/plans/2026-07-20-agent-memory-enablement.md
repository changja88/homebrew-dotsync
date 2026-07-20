# Agent Memory Enablement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explicitly enable native main auto-memory generation and use for both Codex homes and the current Claude user configuration without changing memory paths, memory contents, sessions, or launcher behavior.

**Architecture:** Treat the three user configuration files as one atomic policy update. Validate and back them up first, apply narrow textual patches that preserve unrelated content, verify the effective product settings with the installed CLIs and parsers, and restore all three backups if a configuration, permission, or memory-preservation validation fails. Session trees are audited but are not an atomicity gate because the active Codex/Claude processes executing this plan append to their own sessions concurrently.

**Tech Stack:** macOS, TOML, JSON, Python 3.12 `tomllib`, `jq`, Codex CLI 0.144.6, Claude Code 2.1.205

## Global Constraints

- Change only `~/.codex/config.toml`, Orca's runtime `config.toml`, and `~/.claude/settings.json`.
- Preserve all unrelated settings, comments, ordering, permissions, and Orca hook trust state.
- Do not set `autoMemoryDirectory`.
- Do not edit Orca account-copy configurations under `codex-accounts/` or `claude-accounts/`.
- Do not create product memory directories, launch either agent interactively, generate memory, delete memory, or directly write session files. Read-only session inventory is allowed; concurrent writes by the already-running controller and subagents are expected and must be recorded rather than attributed to the configuration patch.
- Keep Chronicle, Claude subagent memory, durable instruction files, and session transcripts outside this phase.
- Keep the public `dotsync` implementation, root README, and root Makefile unchanged.

---

## File Structure

- Modify: `/Users/hyun/.codex/config.toml` — canonical Codex feature and memory policy.
- Modify: `/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml` — effective Codex policy in the current Orca terminal.
- Modify: `/Users/hyun/.claude/settings.json` — explicit Claude auto-memory enablement.
- Create at execution time: `/private/tmp/agent-memory-enable-a2151a3-retry1/` — permission-preserving rollback copies and before/after file manifests. Keep it after success for recoverability. The first-attempt rollback evidence at `/private/tmp/agent-memory-enable-8a1b944/` remains untouched.
- No repository source or test file changes are required.

### Task 1: Apply and Verify the Atomic Memory Policy

**Files:**
- Modify: `/Users/hyun/.codex/config.toml`
- Modify: `/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml`
- Modify: `/Users/hyun/.claude/settings.json`
- Create: `/private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.toml`
- Create: `/private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.toml`
- Create: `/private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.json`
- Create: `/private/tmp/agent-memory-enable-a2151a3-retry1/*.before`, `*.after`, and `*.delta` verification artifacts

**Interfaces:**
- Consumes: the existing valid TOML/JSON configuration and the current default and Orca `CODEX_HOME` paths.
- Produces: `features.memories = true`, `memories.generate_memories = true`, `memories.use_memories = true`, and `autoMemoryEnabled = true`; no launcher interface changes.

- [ ] **Step 1: Prove the current files are safe patch targets**

Run:

```bash
test -f /Users/hyun/.codex/config.toml
test ! -L /Users/hyun/.codex/config.toml
test -f '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml'
test ! -L '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml'
test -f /Users/hyun/.claude/settings.json
test ! -L /Users/hyun/.claude/settings.json
python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1], "rb"))' /Users/hyun/.codex/config.toml
python3 -c 'import sys,tomllib; tomllib.load(open(sys.argv[1], "rb"))' '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml'
/usr/bin/jq empty /Users/hyun/.claude/settings.json
```

Expected: exit code `0` and no output. If any command fails, stop without changing any file.

Confirm the baseline:

```bash
env CODEX_HOME=/Users/hyun/.codex /opt/homebrew/bin/codex features list | rg '^memories[[:space:]]'
env CODEX_HOME='/Users/hyun/Library/Application Support/orca/codex-runtime-home/home' /opt/homebrew/bin/codex features list | rg '^memories[[:space:]]'
/usr/bin/jq '{autoMemoryEnabled, autoMemoryDirectory}' /Users/hyun/.claude/settings.json
```

Expected:

```text
memories                             experimental       false
memories                             experimental       false
{
  "autoMemoryEnabled": null,
  "autoMemoryDirectory": null
}
```

- [ ] **Step 2: Create permission-preserving backups, immutable-memory manifests, and session audit manifests**

Run:

```bash
test ! -e /private/tmp/agent-memory-enable-a2151a3-retry1
mkdir -m 700 /private/tmp/agent-memory-enable-a2151a3-retry1
cp -p /Users/hyun/.codex/config.toml /private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.toml
cp -p '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml' /private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.toml
cp -p /Users/hyun/.claude/settings.json /private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.json
find /Users/hyun/.codex/sessions '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/sessions' -type f -exec stat -f '%N|%z|%m' {} + 2>/dev/null | LC_ALL=C sort > /private/tmp/agent-memory-enable-a2151a3-retry1/codex-sessions.before
find /Users/hyun/.claude/projects -type f ! -path '*/memory/*' -exec stat -f '%N|%z|%m' {} + 2>/dev/null | LC_ALL=C sort > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-sessions.before
find /Users/hyun/.claude/projects -type f -path '*/memory/*' -exec stat -f '%N|%z|%m' {} + 2>/dev/null | LC_ALL=C sort > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-memory.before
stat -f '%OLp' /Users/hyun/.codex/config.toml > /private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.mode.before
stat -f '%OLp' '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml' > /private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.mode.before
stat -f '%OLp' /Users/hyun/.claude/settings.json > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.mode.before
```

Expected: exit code `0`. The backup directory contains three configuration copies, three state manifests, and three permission manifests.

- [ ] **Step 3: Apply the narrow Codex and Claude configuration patches**

Request filesystem approval for these three user-owned configuration paths, then apply this patch with the patch editor:

```diff
*** Begin Patch
*** Update File: /Users/hyun/.codex/config.toml
@@
 [features]
+# 이전 작업에서 얻은 유용한 맥락을 로컬 메모리로 생성하고 재사용합니다.
+memories = true
 # ChatGPT Apps/connectors 지원입니다.
@@
 prevent_idle_sleep = true
 js_repl = false
 
+[memories]
+generate_memories = true
+use_memories = true
+
 [marketplaces.openai-bundled]
*** Update File: /Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml
@@
 [features]
+# 이전 작업에서 얻은 유용한 맥락을 로컬 메모리로 생성하고 재사용합니다.
+memories = true
 # ChatGPT Apps/connectors 지원입니다.
@@
 prevent_idle_sleep = true
 js_repl = false
 
+[memories]
+generate_memories = true
+use_memories = true
+
 [marketplaces.openai-bundled]
*** Update File: /Users/hyun/.claude/settings.json
@@
 {
+  "autoMemoryEnabled": true,
*** End Patch
```

Expected: the patch applies to all three files. If any file fails to patch, immediately restore all three files with the rollback commands in Step 5.

- [ ] **Step 4: Verify effective settings, preservation, and unchanged agent state**

Parse only the intended settings:

```bash
python3 -c 'import json,sys,tomllib; d=tomllib.load(open(sys.argv[1], "rb")); print(json.dumps({"features.memories": d["features"]["memories"], "memories.generate_memories": d["memories"]["generate_memories"], "memories.use_memories": d["memories"]["use_memories"]}, sort_keys=True))' /Users/hyun/.codex/config.toml
python3 -c 'import json,sys,tomllib; d=tomllib.load(open(sys.argv[1], "rb")); print(json.dumps({"features.memories": d["features"]["memories"], "memories.generate_memories": d["memories"]["generate_memories"], "memories.use_memories": d["memories"]["use_memories"]}, sort_keys=True))' '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml'
/usr/bin/jq '{autoMemoryEnabled, autoMemoryDirectory}' /Users/hyun/.claude/settings.json
```

Expected:

```text
{"features.memories": true, "memories.generate_memories": true, "memories.use_memories": true}
{"features.memories": true, "memories.generate_memories": true, "memories.use_memories": true}
{
  "autoMemoryEnabled": true,
  "autoMemoryDirectory": null
}
```

Verify both effective Codex homes:

```bash
env CODEX_HOME=/Users/hyun/.codex /opt/homebrew/bin/codex features list | rg '^memories[[:space:]]'
env CODEX_HOME='/Users/hyun/Library/Application Support/orca/codex-runtime-home/home' /opt/homebrew/bin/codex features list | rg '^memories[[:space:]]'
```

Expected twice:

```text
memories                             experimental       true
```

Build preservation manifests. Treat memory and target permissions as immutable. Record session deltas for audit, but do not fail only because active Codex/Claude processes appended to their own session files while this task ran:

```bash
find /Users/hyun/.codex/sessions '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/sessions' -type f -exec stat -f '%N|%z|%m' {} + 2>/dev/null | LC_ALL=C sort > /private/tmp/agent-memory-enable-a2151a3-retry1/codex-sessions.after
find /Users/hyun/.claude/projects -type f ! -path '*/memory/*' -exec stat -f '%N|%z|%m' {} + 2>/dev/null | LC_ALL=C sort > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-sessions.after
find /Users/hyun/.claude/projects -type f -path '*/memory/*' -exec stat -f '%N|%z|%m' {} + 2>/dev/null | LC_ALL=C sort > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-memory.after
stat -f '%OLp' /Users/hyun/.codex/config.toml > /private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.mode.after
stat -f '%OLp' '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml' > /private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.mode.after
stat -f '%OLp' /Users/hyun/.claude/settings.json > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.mode.after
diff -u /private/tmp/agent-memory-enable-a2151a3-retry1/codex-sessions.before /private/tmp/agent-memory-enable-a2151a3-retry1/codex-sessions.after > /private/tmp/agent-memory-enable-a2151a3-retry1/codex-sessions.delta || test $? -eq 1
diff -u /private/tmp/agent-memory-enable-a2151a3-retry1/claude-sessions.before /private/tmp/agent-memory-enable-a2151a3-retry1/claude-sessions.after > /private/tmp/agent-memory-enable-a2151a3-retry1/claude-sessions.delta || test $? -eq 1
cmp /private/tmp/agent-memory-enable-a2151a3-retry1/claude-memory.before /private/tmp/agent-memory-enable-a2151a3-retry1/claude-memory.after
cmp /private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.mode.before /private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.mode.after
cmp /private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.mode.before /private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.mode.after
cmp /private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.mode.before /private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.mode.after
```

Expected: the memory and three mode `cmp` commands exit `0` with no output. Both session-delta commands exit successfully whether their delta is empty or contains append/mtime activity from already-running agents; report any non-empty deltas. Keep `/private/tmp/agent-memory-enable-a2151a3-retry1/` after success as a recoverable backup and report its path.

- [ ] **Step 5: Roll back all products if any patch or validation fails**

Run only on failure:

```bash
cp -p /private/tmp/agent-memory-enable-a2151a3-retry1/default-codex.toml /Users/hyun/.codex/config.toml
cp -p /private/tmp/agent-memory-enable-a2151a3-retry1/orca-codex.toml '/Users/hyun/Library/Application Support/orca/codex-runtime-home/home/config.toml'
cp -p /private/tmp/agent-memory-enable-a2151a3-retry1/claude-settings.json /Users/hyun/.claude/settings.json
env CODEX_HOME=/Users/hyun/.codex /opt/homebrew/bin/codex features list | rg '^memories[[:space:]]'
env CODEX_HOME='/Users/hyun/Library/Application Support/orca/codex-runtime-home/home' /opt/homebrew/bin/codex features list | rg '^memories[[:space:]]'
/usr/bin/jq '{autoMemoryEnabled, autoMemoryDirectory}' /Users/hyun/.claude/settings.json
```

Expected after rollback: both Codex feature rows return to `false`; Claude returns `autoMemoryEnabled: null` and `autoMemoryDirectory: null`. Report the failed validation and do not proceed to launcher work.

- [ ] **Step 6: Report the verified configuration-only result**

Report:

```text
Codex canonical home: memories enabled; generation enabled; use enabled
Codex Orca runtime home: memories enabled; generation enabled; use enabled
Claude: auto-memory explicitly enabled; default memory path retained
Memory contents changed: no
Session files directly modified by configuration task: no
Concurrent active-agent session deltas: recorded in *.delta audit artifacts
Rollback backup: /private/tmp/agent-memory-enable-a2151a3-retry1
Launcher behavior changed: no
```

Do not create a repository commit for the three personal configuration files. The next work item is a separate launcher memory inventory and deletion design.
