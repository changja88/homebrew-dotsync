# Herdr Config Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Herdr as a selectable dotsync app whose `backup`, `apply`, previews, and status operate only on `~/.config/herdr/config.toml`.

**Architecture:** Implement `HerdrApp` as a declarative single-file `App` using one `FilePair`; inherit all plan, sync, backup-before-apply, status, and safety behavior from the base class. Register it in `APP_CLASSES`, protect the behavior with path-focused and round-trip tests, and document the opt-in app support in both README languages.

**Tech Stack:** Python 3.12+, stdlib `pathlib`, pytest, existing dotsync `App`/`FilePair` plugin contract.

## Global Constraints

- Synchronize only `~/.config/herdr/config.toml` to `<sync folder>/herdr/config.toml`.
- Exclude every other Herdr file, including session state, plugin state, logs, sockets, and lock files.
- Do not override `sync_from`, `sync_to`, `plan_from`, `plan_to`, or `status`.
- Preserve apply's existing backup-before-overwrite behavior.
- Keep runtime dependencies stdlib-only and target Python 3.12+ on macOS.
- Do not invoke Herdr, make network calls, reload Herdr config, or interpret TOML contents.
- Do not mutate existing users' `dotsync.toml`; Herdr joins `--all` only after the user selects it.
- Do not bump the package version or change `Formula/dotsync.rb`.
- Do not modify anything under `local_dev/`.

## File Structure

- Create `lib/dotsync/apps/herdr.py`: declare the Herdr local/stored file pair and local presence check.
- Create `tests/apps/test_herdr.py`: verify config-only backup/apply/status and presence behavior.
- Modify `lib/dotsync/apps/__init__.py`: register `HerdrApp` in the canonical app registry.
- Modify `tests/apps/test_registry.py`: drive registration and automatic detection through public registry behavior.
- Modify `tests/integration/test_roundtrip.py`: protect both Herdr round-trip directions.
- Modify `README.md`: document Herdr support and scope in English and Korean.

---

### Task 1: Declarative Herdr App, Registry, and Regression Coverage

**Files:**
- Create: `lib/dotsync/apps/herdr.py`
- Create: `tests/apps/test_herdr.py`
- Modify: `lib/dotsync/apps/__init__.py:10-24`
- Modify: `tests/apps/test_registry.py:19-65`
- Modify: `tests/integration/test_roundtrip.py:1-140`

**Interfaces:**
- Consumes: `App`, `FilePair`, `Path.home()`, and the `APP_CLASSES` derived-registry contract.
- Produces: `HerdrApp`, `HerdrApp.is_present_locally() -> bool`, and `HerdrApp.tracked_files(target_dir: Path) -> list[FilePair]`.

- [ ] **Step 1: Write a failing registry test**

Append this test to `tests/apps/test_registry.py` without importing the not-yet-created module:

```python
def test_build_app_returns_herdr_instance(tmp_path):
    cfg = Config(dir=tmp_path, apps=["herdr"])

    app = build_app("herdr", cfg)

    assert type(app).__name__ == "HerdrApp"
    assert app.name == "herdr"
```

- [ ] **Step 2: Run the registry test and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_registry.py::test_build_app_returns_herdr_instance -v
```

Expected: FAIL because `build_app("herdr", cfg)` raises `KeyError: unknown app: herdr`.

- [ ] **Step 3: Add the smallest registered Herdr class**

Create `lib/dotsync/apps/herdr.py` with only the class identity needed by the registry test:

```python
"""Herdr sync — user-authored config.toml only."""

from __future__ import annotations

from dotsync.apps.base import App


class HerdrApp(App):
    name = "herdr"
    description = "Herdr terminal workspace config (config.toml)"
```

Modify `lib/dotsync/apps/__init__.py` so Herdr follows Codex in the canonical order:

```python
from dotsync.apps.herdr import HerdrApp
```

```python
APP_CLASSES: tuple[type[App], ...] = (
    ClaudeApp,
    CodexApp,
    HerdrApp,
    GhosttyApp,
    BetterTouchToolApp,
    ZshApp,
)
```

- [ ] **Step 4: Run the registry test and verify the first GREEN**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_registry.py::test_build_app_returns_herdr_instance -v
```

Expected: PASS. No sync behavior has been implemented yet.

- [ ] **Step 5: Write failing config-only, detection, status, and round-trip tests**

Create `tests/apps/test_herdr.py`:

```python
from pathlib import Path

from dotsync.apps.herdr import HerdrApp


def _herdr_dir(home: Path) -> Path:
    return home / ".config" / "herdr"


def test_sync_from_copies_only_config(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text('[theme]\nname = "nord"\n')
    (local_dir / "session.json").write_text('{"workspaces": []}\n')
    (local_dir / "herdr-server.log").write_text("runtime log\n")
    target = tmp_path / "sync"
    target.mkdir()

    HerdrApp().sync_from(target)

    stored_dir = target / "herdr"
    assert (stored_dir / "config.toml").read_text() == (
        '[theme]\nname = "nord"\n'
    )
    assert sorted(path.name for path in stored_dir.iterdir()) == ["config.toml"]


def test_sync_to_backs_up_config_and_preserves_runtime_state(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("OLD\n")
    (local_dir / "session.json").write_text("SESSION\n")
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    HerdrApp().sync_to(target, backup)

    assert (local_dir / "config.toml").read_text() == "NEW\n"
    assert (backup / "herdr" / "config.toml").read_text() == "OLD\n"
    assert (local_dir / "session.json").read_text() == "SESSION\n"


def test_sync_to_creates_local_config_directory(fake_home, tmp_path):
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    HerdrApp().sync_to(target, backup)

    assert (_herdr_dir(fake_home) / "config.toml").read_text() == "NEW\n"


def test_is_present_locally_true_when_config_exists(fake_home):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("onboarding = false\n")

    assert HerdrApp.is_present_locally() is True


def test_is_present_locally_false_when_only_runtime_state_exists(fake_home):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "session.json").write_text("{}\n")

    assert HerdrApp.is_present_locally() is False


def test_status_clean_when_config_matches(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("X\n")
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("X\n")

    assert HerdrApp().status(target).state == "clean"


def test_status_dirty_when_config_differs(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("LOCAL\n")
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("STORED\n")

    assert HerdrApp().status(target).state == "dirty"
```

Extend `test_detect_present_returns_only_locally_installed` in
`tests/apps/test_registry.py` with a real local Herdr config:

```python
    # herdr: present
    (fake_home / ".config" / "herdr").mkdir(parents=True)
    (fake_home / ".config" / "herdr" / "config.toml").write_text(
        "onboarding = false\n"
    )
```

Add this assertion beside the existing app assertions:

```python
    assert "herdr" in detected
```

Import `HerdrApp` and add a helper in `tests/integration/test_roundtrip.py`:

```python
from dotsync.apps.herdr import HerdrApp
```

```python
def _herdr_dir(home: Path) -> Path:
    return home / ".config" / "herdr"
```

Add both round-trip tests:

```python
def test_herdr_from_then_to_does_not_change_local(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    local = local_dir / "config.toml"
    local.write_text('[theme]\nname = "nord"\n')
    target = tmp_path / "sync"
    target.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()

    HerdrApp().sync_from(target)
    HerdrApp().sync_to(target, backup)

    assert local.read_text() == '[theme]\nname = "nord"\n'


def test_herdr_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    stored = target / "herdr" / "config.toml"
    stored.write_text("onboarding = false\n")
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("OLD\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    HerdrApp().sync_to(target, backup)
    HerdrApp().sync_from(target)

    assert stored.read_text() == "onboarding = false\n"
```

- [ ] **Step 6: Run the new behavior tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  tests/apps/test_herdr.py \
  tests/apps/test_registry.py::test_detect_present_returns_only_locally_installed \
  tests/integration/test_roundtrip.py::test_herdr_from_then_to_does_not_change_local \
  tests/integration/test_roundtrip.py::test_herdr_to_then_from_does_not_change_stored \
  -v
```

Expected: the sync tests fail with `NotImplementedError`, presence/detection
tests fail because the inherited detector returns `False`, and status tests
return `unknown` instead of `clean` or `dirty`.

- [ ] **Step 7: Implement the minimal declarative file contract**

Replace the skeleton in `lib/dotsync/apps/herdr.py` with:

```python
"""Herdr sync — user-authored config.toml only."""

from __future__ import annotations

from pathlib import Path

from dotsync.apps.base import App, FilePair


class HerdrApp(App):
    name = "herdr"
    description = "Herdr terminal workspace config (config.toml)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return cls._local_path().exists()

    @classmethod
    def _local_path(cls) -> Path:
        return Path.home() / ".config" / "herdr" / "config.toml"

    def tracked_files(self, target_dir: Path) -> list[FilePair]:
        return [
            FilePair(
                local=self._local_path(),
                stored=target_dir / self.name / "config.toml",
                label="config.toml",
            )
        ]
```

- [ ] **Step 8: Run the focused suite and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest \
  tests/apps/test_herdr.py \
  tests/apps/test_registry.py \
  tests/integration/test_roundtrip.py \
  -v
```

Expected: all focused Herdr, registry, and round-trip tests PASS with no
warnings or unhandled subprocess calls.

- [ ] **Step 9: Commit the app and regression coverage**

```bash
git add \
  lib/dotsync/apps/herdr.py \
  lib/dotsync/apps/__init__.py \
  tests/apps/test_herdr.py \
  tests/apps/test_registry.py \
  tests/integration/test_roundtrip.py
git commit -m "feat: sync Herdr config"
```

---

### Task 2: Bilingual User Documentation and Full Verification

**Files:**
- Modify: `README.md:13-65`
- Modify: `README.md:180-220`
- Modify: `README.md:268`
- Modify: `README.md:282-331`
- Modify: `README.md:444-484`
- Modify: `README.md:531`

**Interfaces:**
- Consumes: the registered app name `herdr` and exact local path `~/.config/herdr/config.toml` from Task 1.
- Produces: English and Korean instructions that tell new and existing users how Herdr participates in backup/apply.

- [ ] **Step 1: Update the English README in every user-visible app list**

Make the English purpose sentence name Herdr:

```markdown
dotsync consolidates your macOS app configs (Claude Code, Codex CLI, Herdr, Ghostty, BetterTouchTool, zsh) into **one folder of your choice** and keeps it in two-way sync with the apps.
```

Add Herdr to the picker and tracked summary directly after Codex:

```text
#     [x] codex               installed
#     [x] herdr               installed
#     [x] ghostty             installed
```

```text
# ✔ tracked: claude · codex · herdr · ghostty · bettertouchtool · zsh
```

Add this paragraph near the other app-specific sync notes:

```markdown
**Herdr sync tracks only `~/.config/herdr/config.toml`.** Session and runtime state such as `session*.json`, plugin registry state, logs, sockets, and lock files are intentionally excluded. Existing dotsync users can enable Herdr with `dotsync apps` (or include `herdr` when replacing the list with `dotsync config apps ...`); existing tracked-app selections are never changed automatically.
```

Set the supported-app line to:

```markdown
Supported apps: `claude`, `codex`, `herdr`, `ghostty`, `bettertouchtool`, `zsh`
```

- [ ] **Step 2: Apply the equivalent Korean README changes**

Make the Korean purpose sentence name Herdr:

```markdown
dotsync는 macOS의 앱 설정(Claude Code, Codex CLI, Herdr, Ghostty, BetterTouchTool, zsh)을 **사용자가 지정한 단일 폴더**에 모아서 양방향으로 동기화한다.
```

Add the same `herdr` picker row and tracked summary order used in English.

Add the Korean scope and opt-in paragraph:

```markdown
**Herdr sync는 `~/.config/herdr/config.toml`만 추적한다.** `session*.json`, plugin registry state, log, socket, lock 파일 같은 session/runtime 상태는 의도적으로 제외한다. 기존 dotsync 사용자는 `dotsync apps`에서 Herdr를 켜거나 `dotsync config apps ...`로 목록을 교체할 때 `herdr`를 포함하면 된다. 기존 추적 앱 선택은 자동으로 바꾸지 않는다.
```

Set the Korean supported-app line to:

```markdown
지원 앱: `claude`, `codex`, `herdr`, `ghostty`, `bettertouchtool`, `zsh`
```

- [ ] **Step 3: Review documentation parity without adding prose change-detector tests**

Run:

```bash
grep -n "Herdr\|herdr" README.md
git diff --check
```

Expected: both language sections contain the product summary, picker row,
tracked summary, config-only scope note, opt-in guidance, and supported-app
entry; `git diff --check` prints nothing. Do not add tests that merely grep
README source text.

- [ ] **Step 4: Run full behavioral verification**

Run:

```bash
make test
.venv/bin/python3 -c 'from dotsync.apps import APP_NAMES; assert "herdr" in APP_NAMES'
.venv/bin/python3 -m dotsync --help
```

Expected: the full suite reports zero failures, the registry assertion exits
0, and the CLI help exits 0 without warnings.

- [ ] **Step 5: Review the final diff against scope**

Run:

```bash
git diff --check
git status --short
git diff HEAD~1 --stat
git diff HEAD~1 -- lib/dotsync tests README.md
```

At this point `HEAD` is the Task 1 feature commit, so comparing the working
tree with `HEAD~1` covers that commit plus the uncommitted README edits.
Confirm the combined diff contains only the Herdr module, registry entry,
focused and round-trip tests, and bilingual README changes. Confirm there are
no changes under `local_dev/`, no dependency additions, and no version or
Formula edits.

- [ ] **Step 6: Commit documentation after fresh verification**

```bash
git add README.md
git commit -m "docs: document Herdr config sync"
```

- [ ] **Step 7: Confirm the branch handoff state**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: the worktree is clean and the recent history includes the Herdr
feature commit, bilingual documentation commit, and the two reviewed design
commits.
