# Graphify Linked Worktree Hook Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the launcher recognize Graphify Git hooks installed in the common Git directory of a linked worktree.

**Architecture:** Keep the preflight contract and marker checks unchanged, but delegate hook path resolution to Git through `git rev-parse --git-path`. Resolve Git's absolute or project-relative output before checking the two managed hook files.

**Tech Stack:** Python 3.12+, pytest, zsh, Git CLI, Python standard library only

## Global Constraints

- Keep `local_dev` changes separate from the public `dotsync` CLI.
- Do not install or modify Git hooks as part of preflight detection.
- Preserve normal checkout and `core.hooksPath` behavior.
- Keep runtime code standard-library-only.
- Apply runtime changes only through `make -C local_dev install-shim`.

---

### Task 1: Resolve Graphify hook paths through Git

**Files:**
- Modify: `local_dev/tests/test_serena_zsh_shim.py`
- Modify: `local_dev/serena_mcp_management/serena_zsh_shim.py`
- Verify: `local_dev/docs/superpowers/specs/2026-07-29-graphify-linked-worktree-hook-detection-design.md`

**Interfaces:**
- Consumes: rendered zsh helper `_dotsync_agent_graphify_hooks_installed <project_root>`
- Produces: exit status `0` only when Git resolves both hooks and both Graphify marker checks pass

- [x] **Step 1: Add the linked-worktree regression test**

Add this test beside the existing `core.hooksPath` coverage:

```python
@pytest.mark.no_subprocess_block
def test_zsh_shim_graphify_hooks_check_resolves_linked_worktree_common_dir(
    tmp_path,
):
    shim_path, _real_codex, _real_claude, _launcher = _write_zsh_fixture(
        tmp_path
    )
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    subprocess.run(
        ["git", "-C", str(repository), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(worktree),
        ],
        check=True,
        capture_output=True,
    )
    hooks_dir = repository / ".git" / "hooks"
    (hooks_dir / "post-commit").write_text(
        "#!/bin/sh\n# graphify-hook-start\n"
    )
    (hooks_dir / "post-checkout").write_text(
        "#!/bin/sh\n# graphify-checkout-hook-start\n"
    )

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f"source {shim_path}; "
                f"_dotsync_agent_graphify_hooks_installed {worktree}; "
                "print hooks=$?"
            ),
        ],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "hooks=0" in result.stdout
```

- [x] **Step 2: Run the targeted test and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_serena_zsh_shim.py::test_zsh_shim_graphify_hooks_check_resolves_linked_worktree_common_dir \
  -v
```

Expected: FAIL because the existing helper checks
`<linked-worktree>/.git/hooks`, while `.git` is a worktree pointer file.

- [x] **Step 3: Replace manual hook-directory construction**

In `_dotsync_agent_graphify_hooks_installed`, replace the `core.hooksPath`
branch and `$project_root/.git/hooks` fallback with:

```zsh
  pc="$(git -C "$project_root" rev-parse --git-path hooks/post-commit 2>/dev/null)" \
    || return 1
  pco="$(git -C "$project_root" rev-parse --git-path hooks/post-checkout 2>/dev/null)" \
    || return 1

  case "$pc" in
    /*) ;;
    *) pc="$project_root/$pc" ;;
  esac
  case "$pco" in
    /*) ;;
    *) pco="$project_root/$pco" ;;
  esac
```

Retain the existing file-presence and Graphify marker checks unchanged.

- [x] **Step 4: Run linked-worktree and `core.hooksPath` tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_serena_zsh_shim.py::test_zsh_shim_graphify_hooks_check_resolves_linked_worktree_common_dir \
  local_dev/tests/test_serena_zsh_shim.py::test_zsh_shim_graphify_hooks_check_respects_core_hooks_path \
  -v
```

Expected: 2 passed.

- [x] **Step 5: Run the complete shim regression suite**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_zsh_shim.py -q
```

Expected: all tests pass.

- [x] **Step 6: Install the corrected stable runtime mirror**

Run:

```bash
make -C local_dev install-shim
```

Expected: the launcher tree is mirrored under
`~/Desktop/dotsync_config/agent_launcher/` and the managed `~/.zshrc` block
is rewritten without terminating running clients.

- [x] **Step 7: Verify the real linked worktree with the installed shim**

Run:

```bash
zsh -lic '
  _dotsync_agent_graphify_hooks_installed \
    /Users/hyun/orca/workspaces/broccoli-server/changja88-report-source-concepts
  print "hooks=$?"
'
```

Expected: `hooks=0`.

- [x] **Step 8: Review and commit the implementation**

Run:

```bash
git diff --check
git diff -- local_dev/serena_mcp_management/serena_zsh_shim.py \
  local_dev/tests/test_serena_zsh_shim.py
git add local_dev/serena_mcp_management/serena_zsh_shim.py \
  local_dev/tests/test_serena_zsh_shim.py \
  local_dev/docs/superpowers/plans/2026-07-29-graphify-linked-worktree-hook-detection.md
git commit -m "fix(local_dev): detect graphify hooks in worktrees"
```
