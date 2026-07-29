# Graphify Linked Worktree Hook Detection Design

## Context

The launcher preflight checks whether Graphify's `post-commit` and
`post-checkout` hooks are installed. It currently falls back to
`$project_root/.git/hooks` when `core.hooksPath` is unset.

That fallback works in a normal checkout where `.git` is a directory, but not
in a linked worktree where `.git` is a file. Linked worktrees use hooks from
the repository's common Git directory, so Graphify can report both hooks as
installed while the launcher incorrectly reports `hooks not installed`.

## Goal

Make the launcher report Graphify hooks as installed whenever Git resolves
both hook files and they contain Graphify's managed markers, including in:

- normal Git checkouts;
- linked worktrees;
- repositories using `core.hooksPath`.

## Non-goals

- Installing or modifying Git hooks.
- Changing Graphify's Codex integration setup.
- Changing the four-row Graphify preflight UI.
- Changing behavior outside `local_dev`.

## Design

`_dotsync_agent_graphify_hooks_installed` will ask Git for each hook path with:

```sh
git -C "$project_root" rev-parse --git-path hooks/post-commit
git -C "$project_root" rev-parse --git-path hooks/post-checkout
```

Git owns the repository-layout rules, so this resolves the normal `.git`
directory, the shared common directory used by linked worktrees, and
`core.hooksPath` without duplicating those rules in the zsh shim.

`git rev-parse --git-path` can return an absolute or project-relative path.
The helper will preserve absolute paths and prefix relative paths with
`$project_root`. If either path cannot be resolved, either file is absent, or
either Graphify marker is absent, the helper will retain the existing
not-installed result.

## Testing

Add a regression test that creates a real Git repository and linked worktree,
installs marker-bearing hooks in the common Git hook directory, renders the
shim, and verifies the helper returns success from the linked worktree.

Keep the existing `core.hooksPath` test to prove that delegating path
resolution to Git preserves that supported case. Run the complete zsh shim
test module after the targeted red-green cycle.

## Rollout

After tests pass, run `make -C local_dev install-shim` so the stable runtime
mirror and managed `~/.zshrc` block receive the corrected helper. Verify the
real Orca linked worktree no longer displays the Graphify hook warning.
