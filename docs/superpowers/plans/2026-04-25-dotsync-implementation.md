# dotsync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dotsync` Python CLI distributed via Homebrew tap, syncing macOS app configs (claude, ghostty, bettertouchtool, zsh) bidirectionally with a user-specified folder.

**Architecture:** Single Python package (`lib/dotsync/`) with App abstract class + 4 concrete app modules. Stdlib-only runtime. CLI via argparse. Config in `~/.config/dotsync/config.toml`. Backups in `~/.local/share/dotsync/backups/<timestamp>/`. Distributed via own Homebrew tap repo (`changja88/homebrew-dotsync`).

**Tech Stack:** Python 3.12+ (stdlib only — `tomllib`, `argparse`, `shutil`, `pathlib`, `subprocess`, `json`, `dataclasses`, `abc`), pytest (dev only), Homebrew Ruby formula.

**Paths:** All paths in this plan are relative to the new project root `homebrew-dotsync/` (not the current dotfiles repo). After plan/spec are moved into the new folder, paths resolve correctly.

---

## Reference: Source logic to port

Existing logic in current `dotfiles` repo to translate into Python:

- `make/claude.mk` → `apps/claude.py`
- `make/ghostty.mk` → `apps/ghostty.py`
- `make/bettertouchtool.mk` → `apps/bettertouchtool.py`
- `make/zsh.mk` → `apps/zsh.py`
- `make/claude-plugins-restore.py` → fold into `apps/claude.py`'s `sync_to` post-step

Keep them open in another window during porting.

---

## Task 1: Repo scaffold + dev tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `README.md` (skeleton)
- Create: `lib/dotsync/__init__.py`
- Create: `lib/dotsync/apps/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/apps/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 0: Initialize git repo (if not yet)**

```bash
git init
git branch -M main
```

Skip if `.git/` already exists.

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p lib/dotsync/apps tests/apps Formula bin docs/superpowers/{specs,plans}
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "dotsync"
version = "0.1.0"
description = "Sync app configs with a local folder"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.12"
authors = [{ name = "changja88" }]

[project.scripts]
dotsync = "dotsync.cli:main"

[tool.setuptools.packages.find]
where = ["lib"]

[tool.pytest.ini_options]
pythonpath = ["lib"]
testpaths = ["tests"]
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.venv/
venv/
.idea/
.vscode/
.DS_Store
build/
dist/
```

- [ ] **Step 4: Write `LICENSE` (MIT)**

```
MIT License

Copyright (c) 2026 changja88

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 5: Write `README.md` skeleton**

```markdown
# dotsync

Sync macOS app configs (Claude Code, Ghostty, BetterTouchTool, zsh) bidirectionally with a folder of your choice.

## Install

```bash
brew install changja88/dotsync/dotsync
```

## Quickstart

```bash
dotsync init
dotsync from --all          # local apps → folder
dotsync to --all            # folder → local apps
```

See `dotsync --help` for full command reference.

## Documentation

Detailed usage: see [`docs/`](docs/).
```

- [ ] **Step 6: Write `lib/dotsync/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 7: Write `lib/dotsync/apps/__init__.py`**

```python
"""Concrete app sync modules. Registry built lazily by `dotsync.apps.registry`."""
```

- [ ] **Step 8: Write `tests/__init__.py` and `tests/apps/__init__.py`**

Both empty files:

```bash
touch tests/__init__.py tests/apps/__init__.py
```

- [ ] **Step 9: Write `tests/conftest.py`**

```python
import os
from pathlib import Path
import pytest


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Override $HOME to a temp dir for filesystem-isolated tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path
```

- [ ] **Step 10: Verify scaffold**

Run:

```bash
python3 -c "import sys; sys.path.insert(0, 'lib'); import dotsync; print(dotsync.__version__)"
```

Expected: `0.1.0`

- [ ] **Step 11: Initial commit**

```bash
git add -A
git commit -m "chore: initial repo scaffold"
```

---

## Task 2: ui.py — colored output

**Files:**
- Create: `lib/dotsync/ui.py`
- Create: `tests/test_ui.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_ui.py
from dotsync import ui


def test_step_outputs_cyan_arrow():
    out = ui.format_step("동기화 시작")
    assert "▶" in out
    assert "동기화 시작" in out


def test_ok_outputs_green_check():
    out = ui.format_ok("settings.json")
    assert "✓" in out
    assert "settings.json" in out


def test_warn_outputs_yellow():
    out = ui.format_warn("BTT 미실행")
    assert "BTT 미실행" in out


def test_error_outputs_red_x():
    out = ui.format_error("실패")
    assert "✗" in out
    assert "실패" in out


def test_done_outputs_green_check():
    out = ui.format_done("완료")
    assert "✔" in out


def test_no_color_disables_ansi(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = ui.format_step("test")
    assert "\033[" not in out
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
pytest tests/test_ui.py -v
```

Expected: ImportError or all FAIL (module not yet written).

- [ ] **Step 3: Implement `lib/dotsync/ui.py`**

```python
"""ANSI-colored output helpers. Honors NO_COLOR env var (https://no-color.org)."""
import os
import sys

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False


def _wrap(color: str, text: str) -> str:
    if _color_enabled():
        return f"{color}{text}{RESET}"
    return text


def format_step(msg: str) -> str:
    return f"{_wrap(CYAN, '▶')} {msg}"


def format_sub(msg: str) -> str:
    return f"  {_wrap(YELLOW, '↳')} {msg}"


def format_ok(msg: str) -> str:
    return f"  {_wrap(GREEN, '✓')} {msg}"


def format_warn(msg: str) -> str:
    return f"  {_wrap(YELLOW, '⚠')} {msg}"


def format_error(msg: str) -> str:
    return f"  {_wrap(RED, '✗')} {msg}"


def format_done(msg: str) -> str:
    return f"{_wrap(GREEN, '✔')} {msg}"


def step(msg: str) -> None:
    print(format_step(msg))


def sub(msg: str) -> None:
    print(format_sub(msg))


def ok(msg: str) -> None:
    print(format_ok(msg))


def warn(msg: str) -> None:
    print(format_warn(msg))


def error(msg: str) -> None:
    print(format_error(msg), file=sys.stderr)


def done(msg: str) -> None:
    print(format_done(msg))
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/test_ui.py -v
```

Expected: 6 passed.

Note: `_color_enabled()` returns False under pytest because stdout is not a tty. The "NO_COLOR" test verifies the env-var path; the other tests pass because color is disabled and ANSI escapes are absent.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/ui.py tests/test_ui.py
git commit -m "feat(ui): colored output helpers honoring NO_COLOR"
```

---

## Task 3: config.py — TOML config reader/writer

**Files:**
- Create: `lib/dotsync/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
from pathlib import Path
import pytest
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    save_config,
    config_path,
    DEFAULT_BACKUP_DIR,
    DEFAULT_BACKUP_KEEP,
    DEFAULT_BTT_PRESET,
)


def test_config_path_uses_xdg_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "dotsync" / "config.toml"


def test_config_path_falls_back_to_home_config(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == fake_home / ".config" / "dotsync" / "config.toml"


def test_load_missing_config_raises(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    with pytest.raises(ConfigError, match="dotsync init"):
        load_config()


def test_save_then_load_roundtrip(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = Config(dir=fake_home / "my-configs", apps=["claude", "zsh"])
    save_config(cfg)
    loaded = load_config()
    assert loaded.dir == fake_home / "my-configs"
    assert loaded.apps == ["claude", "zsh"]
    assert loaded.backup_dir == Path(DEFAULT_BACKUP_DIR).expanduser()
    assert loaded.backup_keep == DEFAULT_BACKUP_KEEP
    assert loaded.bettertouchtool_preset == DEFAULT_BTT_PRESET


def test_bettertouchtool_preset_roundtrip(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = Config(
        dir=fake_home / "x",
        apps=["bettertouchtool"],
        bettertouchtool_preset="MyCustomPreset",
    )
    save_config(cfg)
    loaded = load_config()
    assert loaded.bettertouchtool_preset == "MyCustomPreset"


def test_load_rejects_relative_dir(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg_file = fake_home / ".config" / "dotsync" / "config.toml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text('dir = "relative/path"\napps = []\n')
    with pytest.raises(ConfigError, match="absolute"):
        load_config()


def test_load_rejects_unknown_app(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg_file = fake_home / ".config" / "dotsync" / "config.toml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(f'dir = "{fake_home}/x"\napps = ["nonsense"]\n')
    with pytest.raises(ConfigError, match="unknown app"):
        load_config()


def test_save_creates_parent_dir(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = Config(dir=fake_home / "x", apps=["zsh"])
    save_config(cfg)
    assert (fake_home / ".config" / "dotsync" / "config.toml").exists()
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/test_config.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/config.py`**

```python
"""dotsync config file management at ~/.config/dotsync/config.toml."""
from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

SUPPORTED_APPS = {"claude", "ghostty", "bettertouchtool", "zsh"}
DEFAULT_BACKUP_DIR = "~/.local/share/dotsync/backups"
DEFAULT_BACKUP_KEEP = 10
DEFAULT_BTT_PRESET = "Master_bt"


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


@dataclass
class Config:
    dir: Path
    apps: List[str]
    backup_dir: Path = field(default_factory=lambda: Path(DEFAULT_BACKUP_DIR).expanduser())
    backup_keep: int = DEFAULT_BACKUP_KEEP
    bettertouchtool_preset: str = DEFAULT_BTT_PRESET


def config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "dotsync" / "config.toml"


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        raise ConfigError(
            f"config not found at {path}. Run `dotsync init` first."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)

    raw_dir = data.get("dir")
    if not raw_dir:
        raise ConfigError(f"`dir` missing in {path}")
    dir_path = Path(raw_dir)
    if not dir_path.is_absolute():
        raise ConfigError(f"`dir` must be an absolute path, got: {raw_dir}")

    apps = data.get("apps") or []
    if not isinstance(apps, list):
        raise ConfigError(f"`apps` must be a list, got: {type(apps).__name__}")
    for app in apps:
        if app not in SUPPORTED_APPS:
            raise ConfigError(f"unknown app `{app}` in config (supported: {sorted(SUPPORTED_APPS)})")

    options = data.get("options", {}) or {}
    backup_dir_raw = options.get("backup_dir", DEFAULT_BACKUP_DIR)
    backup_dir = Path(backup_dir_raw).expanduser()
    backup_keep = int(options.get("backup_keep", DEFAULT_BACKUP_KEEP))
    btt_preset = str(options.get("bettertouchtool_preset", DEFAULT_BTT_PRESET))

    return Config(
        dir=dir_path,
        apps=apps,
        backup_dir=backup_dir,
        backup_keep=backup_keep,
        bettertouchtool_preset=btt_preset,
    )


def save_config(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'dir = "{cfg.dir}"',
        "apps = [" + ", ".join(f'"{a}"' for a in cfg.apps) + "]",
        "",
        "[options]",
        f'backup_dir = "{cfg.backup_dir}"',
        f"backup_keep = {cfg.backup_keep}",
        f'bettertouchtool_preset = "{cfg.bettertouchtool_preset}"',
        "",
    ]
    path.write_text("\n".join(lines))
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/test_config.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/config.py tests/test_config.py
git commit -m "feat(config): TOML config load/save with validation"
```

---

## Task 4: backup.py — timestamped backups + rotation

**Files:**
- Create: `lib/dotsync/backup.py`
- Create: `tests/test_backup.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_backup.py
from pathlib import Path
import time
from dotsync.backup import new_backup_session, rotate_backups


def test_new_backup_session_creates_unique_dir(tmp_path):
    s1 = new_backup_session(tmp_path)
    time.sleep(1.01)
    s2 = new_backup_session(tmp_path)
    assert s1.exists()
    assert s2.exists()
    assert s1 != s2
    assert s1.parent == tmp_path
    # timestamp format YYYYMMDD_HHMMSS
    assert len(s1.name) == 15
    assert s1.name[8] == "_"


def test_new_backup_session_creates_parent(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    s = new_backup_session(deep)
    assert s.parent == deep
    assert s.exists()


def test_rotate_keeps_n_newest(tmp_path):
    # create 5 backup dirs with monotonic names
    for name in ["20260101_000000", "20260102_000000", "20260103_000000",
                 "20260104_000000", "20260105_000000"]:
        (tmp_path / name).mkdir()
    rotate_backups(tmp_path, keep=3)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["20260103_000000", "20260104_000000", "20260105_000000"]


def test_rotate_zero_keep_keeps_all(tmp_path):
    for name in ["20260101_000000", "20260102_000000"]:
        (tmp_path / name).mkdir()
    rotate_backups(tmp_path, keep=0)
    assert len(list(tmp_path.iterdir())) == 2


def test_rotate_ignores_nonbackup_dirs(tmp_path):
    (tmp_path / "20260101_000000").mkdir()
    (tmp_path / "20260102_000000").mkdir()
    (tmp_path / "not-a-backup").mkdir()
    (tmp_path / "20260103_000000").mkdir()
    rotate_backups(tmp_path, keep=1)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert "not-a-backup" in remaining
    assert "20260103_000000" in remaining
    assert "20260101_000000" not in remaining
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/test_backup.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/backup.py`**

```python
"""Backup directory management. Per-session timestamped subdirs with rotation."""
from __future__ import annotations
import re
import shutil
from datetime import datetime
from pathlib import Path

_BACKUP_NAME_RE = re.compile(r"^\d{8}_\d{6}$")


def new_backup_session(root: Path) -> Path:
    """Create and return a fresh timestamped backup directory under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session = root / ts
    session.mkdir(exist_ok=False)
    return session


def rotate_backups(root: Path, keep: int) -> None:
    """Delete oldest backup dirs (by name = timestamp), keeping `keep` newest. keep=0 disables."""
    if keep <= 0 or not root.exists():
        return
    sessions = sorted(
        (p for p in root.iterdir() if p.is_dir() and _BACKUP_NAME_RE.match(p.name)),
        key=lambda p: p.name,
    )
    excess = len(sessions) - keep
    if excess <= 0:
        return
    for old in sessions[:excess]:
        shutil.rmtree(old, ignore_errors=True)
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/test_backup.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/backup.py tests/test_backup.py
git commit -m "feat(backup): timestamped session dirs with rotation"
```

---

## Task 5: apps/base.py — App ABC + AppStatus

**Files:**
- Create: `lib/dotsync/apps/base.py`
- Create: `tests/apps/test_base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/apps/test_base.py
import pytest
from dotsync.apps.base import App, AppStatus, diff_files


def test_app_is_abstract():
    with pytest.raises(TypeError):
        App()


def test_concrete_subclass_works(tmp_path):
    class FakeApp(App):
        name = "fake"
        description = "fake app"

        def sync_from(self, target_dir):
            (target_dir / self.name).mkdir(parents=True, exist_ok=True)
            (target_dir / self.name / "f.txt").write_text("hi")

        def sync_to(self, target_dir, backup_dir):
            pass

    app = FakeApp()
    app.sync_from(tmp_path)
    assert (tmp_path / "fake" / "f.txt").read_text() == "hi"


def test_status_default_is_unknown(tmp_path):
    class MinimalApp(App):
        name = "minimal"
        description = ""

        def sync_from(self, target_dir): pass
        def sync_to(self, target_dir, backup_dir): pass

    s = MinimalApp().status(tmp_path)
    assert s.state == "unknown"


def test_appstatus_states():
    assert AppStatus(state="clean").state == "clean"
    assert AppStatus(state="dirty", details="x").details == "x"


def test_diff_files_clean_when_all_match(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("X")
    b = tmp_path / "b.txt"; b.write_text("X")
    s = diff_files([(a, b)])
    assert s.state == "clean"


def test_diff_files_dirty_when_content_differs(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("OLD")
    b = tmp_path / "b.txt"; b.write_text("NEW")
    s = diff_files([(a, b)])
    assert s.state == "dirty"
    assert "a.txt" in s.details


def test_diff_files_missing_when_either_side_absent(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("X")
    b = tmp_path / "missing.txt"   # not created
    s = diff_files([(a, b)])
    assert s.state == "missing"
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/apps/test_base.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/apps/base.py`**

```python
"""Abstract base for app sync modules."""
from __future__ import annotations
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Tuple

StatusState = Literal["clean", "dirty", "missing", "unknown"]


@dataclass
class AppStatus:
    state: StatusState
    details: str = ""


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def diff_files(pairs: Iterable[Tuple[Path, Path]]) -> AppStatus:
    """Compare (local, stored) file pairs by sha256.

    Returns:
      missing — at least one side absent
      dirty   — every file present but at least one pair differs
      clean   — every pair byte-identical
    """
    pairs = list(pairs)
    if not pairs:
        return AppStatus(state="unknown")
    missing: list[str] = []
    differs: list[str] = []
    for local, stored in pairs:
        if not local.exists() or not stored.exists():
            missing.append(local.name)
            continue
        if _hash(local) != _hash(stored):
            differs.append(local.name)
    if missing:
        return AppStatus(state="missing", details=", ".join(missing))
    if differs:
        return AppStatus(state="dirty", details=", ".join(differs))
    return AppStatus(state="clean")


class App(ABC):
    """One concrete subclass per supported app."""

    name: str = ""           # short id, must match config and dir/<name>/
    description: str = ""    # human-readable

    @abstractmethod
    def sync_from(self, target_dir: Path) -> None:
        """Local app config → target_dir/<self.name>/"""

    @abstractmethod
    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        """target_dir/<self.name>/ → local app config, after backing up local to backup_dir/<self.name>/"""

    def status(self, target_dir: Path) -> AppStatus:
        """Optional: report local-vs-target state. Default: unknown.

        Concrete apps should override and return diff_files(...) over their tracked files.
        """
        return AppStatus(state="unknown")
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/apps/test_base.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/base.py tests/apps/test_base.py
git commit -m "feat(apps): App abstract base with AppStatus"
```

---

## Task 6: apps/zsh.py — simplest concrete app

**Files:**
- Create: `lib/dotsync/apps/zsh.py`
- Create: `tests/apps/test_zsh.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/apps/test_zsh.py
from pathlib import Path
import pytest
from dotsync.apps.zsh import ZshApp


def test_sync_from_copies_zshrc_to_target(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("export FOO=1\n")
    target = tmp_path / "configs"
    target.mkdir()

    ZshApp().sync_from(target)

    assert (target / "zsh" / ".zshrc").read_text() == "export FOO=1\n"


def test_sync_from_missing_zshrc_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with pytest.raises(FileNotFoundError, match=".zshrc"):
        ZshApp().sync_from(target)


def test_sync_to_backs_up_then_overwrites(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("OLD\n")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    ZshApp().sync_to(target, backup)

    assert (fake_home / ".zshrc").read_text() == "NEW\n"
    assert (backup / "zsh" / ".zshrc").read_text() == "OLD\n"


def test_sync_to_missing_target_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="zsh/.zshrc"):
        ZshApp().sync_to(target, backup)


def test_status_clean_when_files_match(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("X")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("X")
    assert ZshApp().status(target).state == "clean"


def test_status_dirty_when_content_differs(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("OLD")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("NEW")
    assert ZshApp().status(target).state == "dirty"


def test_status_missing_when_either_absent(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    assert ZshApp().status(target).state == "missing"
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/apps/test_zsh.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/apps/zsh.py`**

```python
"""zsh sync — single file ~/.zshrc <-> <dir>/zsh/.zshrc"""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class ZshApp(App):
    name = "zsh"
    description = "Zsh shell config (~/.zshrc)"

    def _local(self) -> Path:
        return Path.home() / ".zshrc"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / ".zshrc"

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"동기화: 로컬 → 폴더 [{self.name}]")
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (.zshrc 미존재)")
        ui.sub(f"소스: {src}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok(".zshrc")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"동기화: 폴더 → 로컬 [{self.name}]")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (zsh/.zshrc 미존재)")
        local = self._local()
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / ".zshrc")
            ui.sub(f"백업: {backup_dir / self.name / '.zshrc'}")
        shutil.copy2(src, local)
        ui.ok(".zshrc")
        ui.done("완료. 새 쉘을 열거나 'source ~/.zshrc' 실행하세요.")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/apps/test_zsh.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/zsh.py tests/apps/test_zsh.py
git commit -m "feat(apps/zsh): bidirectional ~/.zshrc sync"
```

---

## Task 7: apps/ghostty.py

**Files:**
- Create: `lib/dotsync/apps/ghostty.py`
- Create: `tests/apps/test_ghostty.py`

Reference: `make/ghostty.mk`. Path: `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty`.

- [ ] **Step 1: Write failing tests**

```python
# tests/apps/test_ghostty.py
from pathlib import Path
import pytest
from dotsync.apps.ghostty import GhosttyApp


def _ghostty_dir(home: Path) -> Path:
    return home / "Library" / "Application Support" / "com.mitchellh.ghostty"


def test_sync_from_copies_config(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home)
    gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("font-size = 14\n")
    target = tmp_path / "configs"
    target.mkdir()

    GhosttyApp().sync_from(target)

    assert (target / "ghostty" / "config.ghostty").read_text() == "font-size = 14\n"


def test_sync_from_missing_local_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with pytest.raises(FileNotFoundError, match="config.ghostty"):
        GhosttyApp().sync_from(target)


def test_sync_to_backs_up_and_writes(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home)
    gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("OLD\n")
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    GhosttyApp().sync_to(target, backup)

    assert (gdir / "config.ghostty").read_text() == "NEW\n"
    assert (backup / "ghostty" / "config.ghostty").read_text() == "OLD\n"


def test_sync_to_creates_local_dir_if_missing(fake_home, tmp_path):
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("X\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    GhosttyApp().sync_to(target, backup)

    assert (_ghostty_dir(fake_home) / "config.ghostty").read_text() == "X\n"


def test_status_clean(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home); gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("X")
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("X")
    assert GhosttyApp().status(target).state == "clean"


def test_status_dirty(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home); gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("OLD")
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("NEW")
    assert GhosttyApp().status(target).state == "dirty"
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/apps/test_ghostty.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/apps/ghostty.py`**

```python
"""Ghostty sync — single file config.ghostty"""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class GhosttyApp(App):
    name = "ghostty"
    description = "Ghostty terminal config (config.ghostty)"

    def _local_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "com.mitchellh.ghostty"

    def _local(self) -> Path:
        return self._local_dir() / "config.ghostty"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / "config.ghostty"

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"동기화: 로컬 → 폴더 [{self.name}]")
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (config.ghostty 미존재)")
        ui.sub(f"소스: {src}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok("config.ghostty")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"동기화: 폴더 → 로컬 [{self.name}]")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (ghostty/config.ghostty 미존재)")
        local = self._local()
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / "config.ghostty")
            ui.sub(f"백업: {backup_dir / self.name / 'config.ghostty'}")
        shutil.copy2(src, local)
        ui.ok("config.ghostty")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/apps/test_ghostty.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/ghostty.py tests/apps/test_ghostty.py
git commit -m "feat(apps/ghostty): bidirectional config.ghostty sync"
```

---

## Task 8: apps/bettertouchtool.py

**Files:**
- Create: `lib/dotsync/apps/bettertouchtool.py`
- Create: `tests/apps/test_bettertouchtool.py`

Reference: `make/bettertouchtool.mk`. Uses osascript to export/import .bttpreset.

- [ ] **Step 1: Write failing tests**

```python
# tests/apps/test_bettertouchtool.py
from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync.apps.bettertouchtool import BetterTouchToolApp


def _osascript_done(*args, **kwargs):
    class R:
        returncode = 0
        stdout = "done"
        stderr = ""
    # also create the output file to simulate export
    cmd = args[0] if args else kwargs.get("args")
    # find outputPath in cmd; cmd is a list like ["osascript", "-e", "...outputPath \"<p>\"..."]
    for token in cmd:
        if "outputPath" in token:
            import re
            m = re.search(r'outputPath "([^"]+)"', token)
            if m:
                Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                Path(m.group(1)).write_text("<bttpreset/>")
    return R()


def _osascript_done_no_export(*args, **kwargs):
    class R:
        returncode = 0
        stdout = "done"
        stderr = ""
    return R()


def test_sync_from_invokes_osascript_export(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done) as run:
        BetterTouchToolApp(preset="Master_bt").sync_from(target)
    assert run.called
    assert (target / "bettertouchtool" / "presets" / "Master_bt.bttpreset").exists()


def test_sync_from_uses_custom_preset_name(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done):
        BetterTouchToolApp(preset="MyPreset").sync_from(target)
    assert (target / "bettertouchtool" / "presets" / "MyPreset.bttpreset").exists()


def test_sync_from_failure_raises(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    class Fail:
        returncode = 1
        stdout = ""
        stderr = "BTT not running"
    with patch("dotsync.apps.bettertouchtool.subprocess.run", return_value=Fail()):
        with pytest.raises(RuntimeError, match="osascript"):
            BetterTouchToolApp(preset="Master_bt").sync_from(target)


def test_sync_to_imports_preset(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done_no_export) as run:
        BetterTouchToolApp(preset="Master_bt").sync_to(target, backup)

    # verify import_preset was invoked at least once
    calls = [c.args[0] for c in run.call_args_list]
    assert any("import_preset" in " ".join(c) for c in calls)


def test_sync_to_missing_preset_raises(tmp_path):
    target = tmp_path / "configs"
    (target / "bettertouchtool" / "presets").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="bttpreset"):
        BetterTouchToolApp(preset="Master_bt").sync_to(target, backup)
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/apps/test_bettertouchtool.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/apps/bettertouchtool.py`**

```python
"""BetterTouchTool sync via osascript export/import."""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App


class BetterTouchToolApp(App):
    name = "bettertouchtool"
    description = "BetterTouchTool preset (.bttpreset, name configurable)"

    def __init__(self, preset: str = "Master_bt"):
        self.preset = preset

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / "presets" / f"{self.preset}.bttpreset"

    def _osascript(self, script: str) -> None:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
        )
        out = (result.stdout or "").strip()
        if result.returncode != 0 or out != "done":
            raise RuntimeError(
                f"osascript failed (rc={result.returncode}): "
                f"stdout={out!r} stderr={result.stderr.strip()!r}. "
                f"Is BetterTouchTool running?"
            )

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"동기화: 로컬 → 폴더 [{self.name}]")
        ui.sub(f"preset: {self.preset}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        script = (
            f'tell application "BetterTouchTool" to export_preset '
            f'"{self.preset}" outputPath "{dst}" compress false includeSettings true'
        )
        self._osascript(script)
        if not dst.exists():
            raise RuntimeError(f"BTT export 파일이 생성되지 않음: {dst}")
        ui.ok(f"presets/{self.preset}.bttpreset")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"동기화: 폴더 → 로컬 [{self.name}]")
        ui.sub(f"preset: {self.preset}")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (bettertouchtool/presets/.bttpreset 미존재)")
        # backup current preset by re-exporting from BTT
        backup_target = backup_dir / self.name / f"{self.preset}.bttpreset"
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        export_script = (
            f'tell application "BetterTouchTool" to export_preset '
            f'"{self.preset}" outputPath "{backup_target}" compress false includeSettings true'
        )
        try:
            self._osascript(export_script)
            ui.sub(f"백업: {backup_target}")
        except RuntimeError:
            ui.warn("기존 preset 백업 실패 (무시하고 진행)")

        import_script = (
            f'tell application "BetterTouchTool" to import_preset "{src}"'
        )
        self._osascript(import_script)
        ui.ok(f"presets/{self.preset}.bttpreset → BTT")
        ui.done("BTT에서 preset이 활성화되었는지 확인하세요.")
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/apps/test_bettertouchtool.py -v
```

Expected: 5 passed.

Note: `status()` is intentionally **not overridden** for BTT — comparing presets would require an osascript export round-trip, which is too expensive for a status command. `dotsync status bettertouchtool` will report `unknown`.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/bettertouchtool.py tests/apps/test_bettertouchtool.py
git commit -m "feat(apps/bettertouchtool): preset sync via osascript (preset configurable)"
```

---

## Task 9: apps/claude.py — most complex, with plugin restore

**Files:**
- Create: `lib/dotsync/apps/claude.py`
- Create: `tests/apps/test_claude.py`

Reference: `make/claude.mk` and `make/claude-plugins-restore.py`. Targets:
- `~/.claude/settings.json` ↔ `<dir>/claude/settings.json`
- `~/.claude/plugins/installed_plugins.json` ↔ `<dir>/claude/plugins/installed_plugins.json`
- `~/.claude/plugins/known_marketplaces.json` ↔ `<dir>/claude/plugins/known_marketplaces.json`
- `~/.claude.json` `mcpServers` field ↔ `<dir>/claude/mcp-servers.json` (as standalone file)
- For each plugin in `installed_plugins.json`, also `~/.claude/plugins/<name>/config.json` ↔ `<dir>/claude/plugins/<name>/config.json`
- `sync_to` post-step: invoke `claude plugin marketplace add --scope user` and `claude plugin install --scope user` for missing entries (subprocess), then `claude plugin disable --scope user` for any plugin marked false in `settings.json["enabledPlugins"]`.

**Real on-disk shape (verified against current ~/.claude data):**

`installed_plugins.json`:
```json
{
  "version": 2,
  "plugins": {
    "<plugin>@<marketplace>": [
      {"scope": "user", "installPath": "/Users/.../cache/<mp>/<plugin>/<ver>", "version": "...", ...}
    ]
  }
}
```
Note: each plugin id maps to a **list of install entries**, not a dict. `installPath` is checked to detect missing-from-cache plugins.

`known_marketplaces.json`:
```json
{
  "<marketplace_name>": {
    "source": {"source": "github", "repo": "owner/repo"},
    "installLocation": "...",
    "lastUpdated": "..."
  }
}
```
Note: top-level keys are marketplace names directly — no `{"marketplaces": {...}}` wrapping. `source.source` is `github` or `directory` (not `git`/`local`).

`settings.json`:
```json
{
  "enabledPlugins": {"<plugin>@<marketplace>": true | false},
  ...
}
```
Plugins marked `false` here must be re-disabled after `plugin install` (which defaults to enabled=true).

- [ ] **Step 1: Write failing tests**

```python
# tests/apps/test_claude.py
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync.apps.claude import ClaudeApp


def _plugin_entry(install_path: str, version: str = "1.0.0") -> dict:
    return {
        "scope": "user",
        "installPath": install_path,
        "version": version,
    }


def _make_local(home: Path, plugins: dict | None = None, marketplaces: dict | None = None,
                mcp: dict | None = None, settings: dict | None = None,
                plugin_configs: dict | None = None):
    cdir = home / ".claude"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "settings.json").write_text(json.dumps(settings or {"theme": "dark"}))
    pdir = cdir / "plugins"
    pdir.mkdir(exist_ok=True)
    # plugins is the WHOLE doc (with version + plugins keys), or None for empty
    (pdir / "installed_plugins.json").write_text(json.dumps(
        plugins if plugins is not None else {"version": 2, "plugins": {}}
    ))
    # marketplaces is the WHOLE doc (top-level = marketplace names), or None for empty
    (pdir / "known_marketplaces.json").write_text(json.dumps(marketplaces or {}))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": mcp or {}}))
    for name, cfg in (plugin_configs or {}).items():
        (pdir / name).mkdir(parents=True, exist_ok=True)
        (pdir / name / "config.json").write_text(json.dumps(cfg))


def test_sync_from_copies_all_files(fake_home, tmp_path):
    _make_local(
        fake_home,
        plugins={"version": 2, "plugins": {
            "superpowers@official": [_plugin_entry("/p/sp/1.0.0")]
        }},
        marketplaces={"official": {"source": {"source": "github", "repo": "anthropics/sp"}}},
        mcp={"playwright": {"command": "npx"}},
        settings={"theme": "dark"},
        plugin_configs={"superpowers": {"foo": "bar"}},
    )
    target = tmp_path / "configs"
    target.mkdir()

    ClaudeApp().sync_from(target)

    cdir = target / "claude"
    assert json.loads((cdir / "settings.json").read_text())["theme"] == "dark"
    assert json.loads((cdir / "mcp-servers.json").read_text()) == {"playwright": {"command": "npx"}}
    ip = json.loads((cdir / "plugins" / "installed_plugins.json").read_text())
    assert ip["plugins"]["superpowers@official"][0]["installPath"] == "/p/sp/1.0.0"
    km = json.loads((cdir / "plugins" / "known_marketplaces.json").read_text())
    assert km["official"]["source"]["repo"] == "anthropics/sp"
    assert json.loads((cdir / "plugins" / "superpowers" / "config.json").read_text()) == {"foo": "bar"}


def test_sync_to_restores_files_and_merges_mcp(fake_home, tmp_path):
    # local has pre-existing settings + mcp
    _make_local(fake_home, mcp={"existing": {"command": "old"}}, settings={"theme": "old"})
    # target has new settings
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"theme": "new"}))
    (cdir / "mcp-servers.json").write_text(json.dumps({"new-mcp": {"command": "x"}}))
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    assert json.loads((fake_home / ".claude" / "settings.json").read_text())["theme"] == "new"
    # ~/.claude.json mcpServers replaced with target's content; other top-level keys preserved
    cj = json.loads((fake_home / ".claude.json").read_text())
    assert cj["mcpServers"] == {"new-mcp": {"command": "x"}}
    # backup of local settings.json
    assert json.loads((backup / "claude" / "settings.json").read_text())["theme"] == "old"


def test_sync_to_invokes_plugin_restore_with_scope_user(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    # plugin entry references a NON-EXISTENT installPath → triggers install
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"superpowers@official": [_plugin_entry("/nonexistent/path")]}
    }))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "official": {"source": {"source": "github", "repo": "anthropics/sp"}}
    }))
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert any("marketplace add --scope user anthropics/sp" in c for c in cmds)
    assert any("plugin install --scope user superpowers@official" in c for c in cmds)


def test_sync_to_skips_install_when_installpath_exists(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    # installPath exists on disk → install must be skipped
    existing = tmp_path / "cached_plugin"
    existing.mkdir()
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"sp@official": [_plugin_entry(str(existing))]}
    }))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert not any("plugin install" in c for c in cmds)


def test_sync_to_disables_plugins_marked_false(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"a@mp": True, "b@mp": False, "c@mp": False}
    }))
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    disable_cmds = [c for c in cmds if "plugin disable" in c]
    assert any("b@mp" in c for c in disable_cmds)
    assert any("c@mp" in c for c in disable_cmds)
    assert not any("a@mp" in c for c in disable_cmds)


def test_sync_to_directory_marketplace_uses_path(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "local-mp": {"source": {"source": "directory", "path": "/Users/x/local-marketplace"}}
    }))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert any("marketplace add --scope user /Users/x/local-marketplace" in c for c in cmds)


def test_sync_to_missing_target_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="claude/settings.json"):
        ClaudeApp().sync_to(target, backup)


def test_status_clean(fake_home, tmp_path):
    _make_local(fake_home, settings={"x": 1}, mcp={"a": 1})
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"x": 1}))
    (cdir / "mcp-servers.json").write_text(json.dumps({"a": 1}))
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))

    s = ClaudeApp().status(target)
    assert s.state == "clean"


def test_status_dirty_when_settings_differ(fake_home, tmp_path):
    _make_local(fake_home, settings={"x": "OLD"})
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"x": "NEW"}))
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))

    s = ClaudeApp().status(target)
    assert s.state == "dirty"
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/apps/test_claude.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/apps/claude.py`**

```python
"""Claude Code sync — settings, plugins, MCP servers, with plugin auto-restore."""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class ClaudeApp(App):
    name = "claude"
    description = "Claude Code (settings + plugins + MCP servers)"

    def _claude_dir(self) -> Path:
        return Path.home() / ".claude"

    def _claude_json(self) -> Path:
        return Path.home() / ".claude.json"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"동기화: 로컬 → 폴더 [{self.name}]")
        ui.sub(f"소스: {self._claude_dir()}")

        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        (stored / "plugins").mkdir(parents=True, exist_ok=True)

        # settings.json
        shutil.copy2(cdir / "settings.json", stored / "settings.json")
        ui.ok("settings.json")

        # plugin metadata
        for fname in ("installed_plugins.json", "known_marketplaces.json"):
            shutil.copy2(cdir / "plugins" / fname, stored / "plugins" / fname)
            ui.ok(f"plugins/{fname}")

        # mcp servers from ~/.claude.json
        cj = json.loads(self._claude_json().read_text())
        (stored / "mcp-servers.json").write_text(
            json.dumps(cj.get("mcpServers", {}), indent=2, ensure_ascii=False)
        )
        ui.ok("mcp-servers.json")

        # per-plugin config.json
        for plugin_name in self._installed_plugin_names(stored / "plugins" / "installed_plugins.json"):
            src = cdir / "plugins" / plugin_name / "config.json"
            if src.exists():
                dst_dir = stored / "plugins" / plugin_name
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst_dir / "config.json")
                ui.ok(f"plugins/{plugin_name}/config.json")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"동기화: 폴더 → 로컬 [{self.name}]")
        stored = self._stored(target_dir)
        if not (stored / "settings.json").exists():
            raise FileNotFoundError(f"{stored / 'settings.json'} 없음 (claude/settings.json 미존재)")

        cdir = self._claude_dir()
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "plugins").mkdir(parents=True, exist_ok=True)

        bdir = backup_dir / self.name
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "plugins").mkdir(parents=True, exist_ok=True)

        # backup local
        for src, rel in [
            (cdir / "settings.json", "settings.json"),
            (cdir / "plugins" / "installed_plugins.json", "plugins/installed_plugins.json"),
            (cdir / "plugins" / "known_marketplaces.json", "plugins/known_marketplaces.json"),
            (self._claude_json(), ".claude.json"),
        ]:
            if src.exists():
                dst = bdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        ui.sub(f"백업: {bdir}")

        # apply settings/plugin metadata
        shutil.copy2(stored / "settings.json", cdir / "settings.json")
        ui.ok("settings.json")
        shutil.copy2(stored / "plugins" / "installed_plugins.json",
                     cdir / "plugins" / "installed_plugins.json")
        ui.ok("plugins/installed_plugins.json")
        shutil.copy2(stored / "plugins" / "known_marketplaces.json",
                     cdir / "plugins" / "known_marketplaces.json")
        ui.ok("plugins/known_marketplaces.json")

        # merge mcp-servers.json into ~/.claude.json
        claude_json_path = self._claude_json()
        cj = json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
        cj["mcpServers"] = json.loads((stored / "mcp-servers.json").read_text())
        claude_json_path.write_text(json.dumps(cj, indent=2, ensure_ascii=False))
        ui.ok("mcp-servers.json → ~/.claude.json")

        # per-plugin config.json
        for plugin_name in self._installed_plugin_names(stored / "plugins" / "installed_plugins.json"):
            src = stored / "plugins" / plugin_name / "config.json"
            if not src.exists():
                continue
            local_plugin_dir = cdir / "plugins" / plugin_name
            local_plugin_dir.mkdir(parents=True, exist_ok=True)
            local_cfg = local_plugin_dir / "config.json"
            if local_cfg.exists():
                bdst = bdir / "plugins" / plugin_name
                bdst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_cfg, bdst / "config.json")
            shutil.copy2(src, local_cfg)
            ui.ok(f"plugins/{plugin_name}/config.json")

        # restore marketplaces + plugins
        ui.step("marketplace · 플러그인 복원")
        self._restore_plugins(stored)

        # re-disable plugins that should stay disabled
        self._enforce_disabled(stored / "settings.json")

        ui.done("완료. Claude Code를 재시작하세요.")

    def status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        cdir = self._claude_dir()
        # build ephemeral mcp-servers.json snapshot from local ~/.claude.json for diff
        # We don't write to disk during status; instead, hash the JSON content directly.
        pairs = [
            (cdir / "settings.json", stored / "settings.json"),
            (cdir / "plugins" / "installed_plugins.json", stored / "plugins" / "installed_plugins.json"),
            (cdir / "plugins" / "known_marketplaces.json", stored / "plugins" / "known_marketplaces.json"),
        ]
        base = diff_files(pairs)
        if base.state in ("missing", "dirty"):
            return base
        # check mcp-servers manually (extracted from ~/.claude.json)
        local_cj = self._claude_json()
        stored_mcp = stored / "mcp-servers.json"
        if not local_cj.exists() or not stored_mcp.exists():
            return AppStatus(state="missing", details="mcp-servers.json")
        local_mcp = json.loads(local_cj.read_text()).get("mcpServers", {})
        stored_mcp_data = json.loads(stored_mcp.read_text())
        if local_mcp != stored_mcp_data:
            return AppStatus(state="dirty", details="mcp-servers.json")
        return base

    @staticmethod
    def _installed_plugin_names(installed_plugins_path: Path) -> list[str]:
        if not installed_plugins_path.exists():
            return []
        data = json.loads(installed_plugins_path.read_text())
        plugins = data.get("plugins") or {}
        # keys look like "name@marketplace"; we want unique short names
        return sorted({k.split("@")[0] for k in plugins.keys()})

    def _restore_plugins(self, stored: Path) -> None:
        """Invoke `claude plugin marketplace add` / `plugin install` for missing entries.

        Marketplace doc is a flat dict {name: {source: {...}, ...}}.
        Plugin doc is {plugins: {id: [entries...]}}; install only when no entry has an existing installPath.
        """
        marketplaces = json.loads((stored / "plugins" / "known_marketplaces.json").read_text())
        installed_doc = json.loads((stored / "plugins" / "installed_plugins.json").read_text())
        plugins = installed_doc.get("plugins") or {}

        for mp_name, mp_meta in marketplaces.items():
            source = (mp_meta.get("source") or {})
            spec = self._marketplace_spec(source)
            if not spec:
                ui.warn(f"marketplace `{mp_name}` 출처 정보 없음 — 건너뜀")
                continue
            self._run_claude_cli(
                ["plugin", "marketplace", "add", "--scope", "user", spec],
                desc=f"marketplace add {mp_name}",
            )

        for plugin_id, entries in plugins.items():
            # entries is a list of install records; install only when none of the install paths exist
            entries = entries if isinstance(entries, list) else []
            if any(Path(e.get("installPath", "")).is_dir() for e in entries):
                ui.sub(f"plugin install {plugin_id} (cache 존재, 스킵)")
                continue
            self._run_claude_cli(
                ["plugin", "install", "--scope", "user", plugin_id],
                desc=f"plugin install {plugin_id}",
            )

    def _enforce_disabled(self, settings_json_path: Path) -> None:
        """Re-disable plugins marked false in settings.json (claude plugin install enables by default)."""
        if not settings_json_path.exists():
            return
        try:
            settings = json.loads(settings_json_path.read_text())
        except json.JSONDecodeError:
            return
        enabled_map = settings.get("enabledPlugins", {}) or {}
        for plugin_id, enabled in enabled_map.items():
            if enabled:
                continue
            self._run_claude_cli(
                ["plugin", "disable", "--scope", "user", plugin_id],
                desc=f"plugin disable {plugin_id}",
                tolerate_already=True,
            )

    @staticmethod
    def _marketplace_spec(source: dict[str, Any]) -> str | None:
        kind = source.get("source")
        if kind == "github":
            return source.get("repo")
        if kind == "directory":
            return source.get("path")
        if kind == "git":
            return source.get("url")
        if kind == "local":
            return source.get("path")
        return None

    @staticmethod
    def _run_claude_cli(args: list[str], desc: str, tolerate_already: bool = True) -> None:
        result = subprocess.run(["claude", *args], capture_output=True, text=True)
        if result.returncode == 0:
            combined = ((result.stdout or "") + (result.stderr or "")).lower()
            if tolerate_already and "already" in combined:
                ui.sub(f"{desc} (이미 등록됨)")
            else:
                ui.ok(desc)
            return
        stderr = (result.stderr or "").strip()
        if tolerate_already and "already" in stderr.lower():
            ui.sub(f"{desc} (이미 등록됨)")
        else:
            ui.warn(f"{desc} 실패: {stderr or 'unknown'}")
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/apps/test_claude.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/claude.py tests/apps/test_claude.py
git commit -m "feat(apps/claude): settings + plugins + MCP sync with auto-restore"
```

---

## Task 10: apps/__init__.py registry

**Files:**
- Modify: `lib/dotsync/apps/__init__.py`
- Create: `tests/apps/test_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/apps/test_registry.py
import pytest
from dotsync.apps import APP_NAMES, build_app
from dotsync.config import Config


def test_app_names_are_supported_set():
    assert APP_NAMES == frozenset({"claude", "ghostty", "bettertouchtool", "zsh"})


def test_build_app_returns_instance(tmp_path):
    cfg = Config(dir=tmp_path, apps=["zsh"])
    app = build_app("zsh", cfg)
    assert app.name == "zsh"


def test_build_app_unknown_raises(tmp_path):
    cfg = Config(dir=tmp_path, apps=[])
    with pytest.raises(KeyError):
        build_app("nonsense", cfg)


def test_build_app_bettertouchtool_uses_config_preset(tmp_path):
    cfg = Config(dir=tmp_path, apps=["bettertouchtool"], bettertouchtool_preset="MyPreset")
    app = build_app("bettertouchtool", cfg)
    assert app.preset == "MyPreset"


def test_supported_apps_matches_registry():
    """SUPPORTED_APPS in config.py and APP_NAMES here must stay in sync."""
    from dotsync.config import SUPPORTED_APPS
    assert SUPPORTED_APPS == set(APP_NAMES)
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/apps/test_registry.py -v
```

Expected: ImportError on APP_NAMES / build_app.

- [ ] **Step 3: Update `lib/dotsync/apps/__init__.py`**

```python
"""Concrete app sync modules + factory.

We use a factory (build_app) instead of a static REGISTRY because BetterTouchToolApp
needs config-driven construction (preset name).
"""
from __future__ import annotations
from dotsync.apps.base import App
from dotsync.apps.claude import ClaudeApp
from dotsync.apps.ghostty import GhosttyApp
from dotsync.apps.bettertouchtool import BetterTouchToolApp
from dotsync.apps.zsh import ZshApp

APP_NAMES = frozenset({"claude", "ghostty", "bettertouchtool", "zsh"})

# Lightweight metadata-only instances for `dotsync apps` listing (description, name).
# Do NOT use these for sync — use build_app() to get a config-aware instance.
_DESCRIPTIONS = {
    "claude": ClaudeApp().description,
    "ghostty": GhosttyApp().description,
    "bettertouchtool": BetterTouchToolApp().description,
    "zsh": ZshApp().description,
}


def app_descriptions() -> dict[str, str]:
    return dict(_DESCRIPTIONS)


def build_app(name: str, cfg) -> App:
    """Construct a configured App instance for `name`.

    `cfg` is a dotsync.config.Config.
    """
    if name not in APP_NAMES:
        raise KeyError(f"unknown app: {name}. Supported: {sorted(APP_NAMES)}")
    if name == "claude":
        return ClaudeApp()
    if name == "ghostty":
        return GhosttyApp()
    if name == "bettertouchtool":
        return BetterTouchToolApp(preset=cfg.bettertouchtool_preset)
    if name == "zsh":
        return ZshApp()
    raise KeyError(name)  # unreachable
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
pytest tests/apps/test_registry.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/__init__.py tests/apps/test_registry.py
git commit -m "feat(apps): config-aware app factory (build_app)"
```

---

## Task 11: cli.py — argparse dispatch

**Files:**
- Create: `lib/dotsync/cli.py`
- Create: `lib/dotsync/__main__.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli.py
from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync.cli import main
from dotsync.config import Config, save_config


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "0.1.0" in out


def test_init_writes_config_noninteractive(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "myconfigs"
    rc = main(["init", "--dir", str(target), "--apps", "zsh,ghostty", "--yes"])
    assert rc == 0
    cfg_file = fake_home / ".config" / "dotsync" / "config.toml"
    assert cfg_file.exists()
    assert "zsh" in cfg_file.read_text()
    assert target.exists()


def test_init_with_btt_preset_flag(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "myconfigs"
    rc = main([
        "init", "--dir", str(target),
        "--apps", "bettertouchtool",
        "--btt-preset", "MyPreset",
        "--yes",
    ])
    assert rc == 0
    cfg_text = (fake_home / ".config" / "dotsync" / "config.toml").read_text()
    assert "MyPreset" in cfg_text


def test_config_show(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    rc = main(["config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(target) in out
    assert "zsh" in out


def test_from_single_app_calls_sync_from(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    (fake_home / ".zshrc").write_text("X")

    rc = main(["from", "zsh"])
    assert rc == 0
    assert (target / "zsh" / ".zshrc").read_text() == "X"


def test_to_all_iterates_registered_apps(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"], backup_dir=tmp_path / "bk"))

    rc = main(["to", "--all"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "Z"


def test_no_config_shows_init_hint(fake_home, monkeypatch, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    rc = main(["from", "--all"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "dotsync init" in err


def test_status_reports_diff(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("STORED")
    (fake_home / ".zshrc").write_text("LOCAL")
    save_config(Config(dir=target, apps=["zsh"]))

    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "zsh" in out
    assert "dirty" in out


def test_runtime_error_caught_with_friendly_exit(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"], backup_dir=tmp_path / "bk"))

    with patch("dotsync.apps.zsh.shutil.copy2", side_effect=RuntimeError("disk full")):
        rc = main(["to", "zsh"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "disk full" in err
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
pytest tests/test_cli.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `lib/dotsync/cli.py`**

```python
"""dotsync CLI — argparse-based command dispatch."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Sequence
from dotsync import __version__, ui
from dotsync.apps import APP_NAMES, app_descriptions, build_app
from dotsync.backup import new_backup_session, rotate_backups
from dotsync.config import (
    Config,
    ConfigError,
    DEFAULT_BTT_PRESET,
    SUPPORTED_APPS,
    load_config,
    save_config,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dotsync", description="Sync app configs with a folder.")
    p.add_argument("--version", action="version", version=f"dotsync {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="initialize config")
    init.add_argument("--dir", help="absolute path to sync folder")
    init.add_argument("--apps", help="comma-separated app names")
    init.add_argument("--btt-preset", default=None, help=f"BetterTouchTool preset name (default: {DEFAULT_BTT_PRESET})")
    init.add_argument("--yes", action="store_true", help="non-interactive: skip prompts")

    cfg = sub.add_parser("config", help="manage config")
    cfg_sub = cfg.add_subparsers(dest="cfg_cmd", required=True)
    cfg_dir = cfg_sub.add_parser("dir", help="set sync dir")
    cfg_dir.add_argument("path")
    cfg_apps = cfg_sub.add_parser("apps", help="set tracked apps")
    cfg_apps.add_argument("apps", help="comma-separated names")
    cfg_btt = cfg_sub.add_parser("btt-preset", help="set BetterTouchTool preset name")
    cfg_btt.add_argument("preset")
    cfg_sub.add_parser("show", help="print current config")

    sub_apps = sub.add_parser("apps", help="list supported apps")

    sub_status = sub.add_parser("status", help="report sync state")

    sync_from = sub.add_parser("from", help="local → folder")
    sync_from.add_argument("app", nargs="?", help="app name or omit with --all")
    sync_from.add_argument("--all", action="store_true")

    sync_to = sub.add_parser("to", help="folder → local")
    sync_to.add_argument("app", nargs="?", help="app name or omit with --all")
    sync_to.add_argument("--all", action="store_true")

    return p


def cmd_init(args) -> int:
    if args.yes:
        if not args.dir:
            print("--dir required with --yes", file=sys.stderr)
            return 2
        dir_path = Path(args.dir).expanduser().resolve()
        apps = [a.strip() for a in (args.apps or "").split(",") if a.strip()]
        btt_preset = args.btt_preset or DEFAULT_BTT_PRESET
    else:
        dir_str = input("sync 폴더 절대 경로: ").strip()
        dir_path = Path(dir_str).expanduser().resolve()
        apps_str = input(f"추적할 앱 (comma-separated, 후보: {sorted(SUPPORTED_APPS)}): ").strip()
        apps = [a.strip() for a in apps_str.split(",") if a.strip()]
        btt_preset = args.btt_preset or DEFAULT_BTT_PRESET
        if "bettertouchtool" in apps:
            entered = input(f"BetterTouchTool preset 이름 [{btt_preset}]: ").strip()
            if entered:
                btt_preset = entered

    bad = [a for a in apps if a not in SUPPORTED_APPS]
    if bad:
        print(f"unknown apps: {bad}", file=sys.stderr)
        return 2

    dir_path.mkdir(parents=True, exist_ok=True)
    save_config(Config(dir=dir_path, apps=apps, bettertouchtool_preset=btt_preset))
    ui.done(f"config 저장 → {Path.home()}/.config/dotsync/config.toml")
    return 0


def cmd_config(args) -> int:
    if args.cfg_cmd == "show":
        cfg = load_config()
        print(f"dir = {cfg.dir}")
        print(f"apps = {cfg.apps}")
        print(f"backup_dir = {cfg.backup_dir}")
        print(f"backup_keep = {cfg.backup_keep}")
        print(f"bettertouchtool_preset = {cfg.bettertouchtool_preset}")
        return 0
    if args.cfg_cmd == "dir":
        cfg = load_config()
        new_dir = Path(args.path).expanduser().resolve()
        new_dir.mkdir(parents=True, exist_ok=True)
        cfg.dir = new_dir
        save_config(cfg)
        ui.done(f"dir = {new_dir}")
        return 0
    if args.cfg_cmd == "apps":
        cfg = load_config()
        new_apps = [a.strip() for a in args.apps.split(",") if a.strip()]
        bad = [a for a in new_apps if a not in SUPPORTED_APPS]
        if bad:
            print(f"unknown apps: {bad}", file=sys.stderr)
            return 2
        cfg.apps = new_apps
        save_config(cfg)
        ui.done(f"apps = {new_apps}")
        return 0
    if args.cfg_cmd == "btt-preset":
        cfg = load_config()
        cfg.bettertouchtool_preset = args.preset
        save_config(cfg)
        ui.done(f"bettertouchtool_preset = {args.preset}")
        return 0
    return 2


def cmd_apps(args) -> int:
    for name, desc in app_descriptions().items():
        print(f"  {name:18s} {desc}")
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    for name in cfg.apps:
        app = build_app(name, cfg)
        s = app.status(cfg.dir)
        print(f"  {name:18s} {s.state}{(' — ' + s.details) if s.details else ''}")
    return 0


def _resolve_app_list(args, cfg: Config) -> list[str]:
    if args.all:
        return list(cfg.apps)
    if not args.app:
        print("provide app name or --all", file=sys.stderr)
        return []
    return [args.app]


def cmd_from(args) -> int:
    cfg = load_config()
    apps = _resolve_app_list(args, cfg)
    if not apps:
        return 2
    cfg.dir.mkdir(parents=True, exist_ok=True)
    for name in apps:
        build_app(name, cfg).sync_from(cfg.dir)
    return 0


def cmd_to(args) -> int:
    cfg = load_config()
    apps = _resolve_app_list(args, cfg)
    if not apps:
        return 2
    cfg.dir.mkdir(parents=True, exist_ok=True)
    session = new_backup_session(cfg.backup_dir)
    for name in apps:
        build_app(name, cfg).sync_to(cfg.dir, session)
    rotate_backups(cfg.backup_dir, cfg.backup_keep)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "config":
            return cmd_config(args)
        if args.cmd == "apps":
            return cmd_apps(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "from":
            return cmd_from(args)
        if args.cmd == "to":
            return cmd_to(args)
        parser.print_help()
        return 2
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 3
    except FileNotFoundError as e:
        ui.error(str(e))
        return 4
    except RuntimeError as e:
        ui.error(str(e))
        return 5


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Implement `lib/dotsync/__main__.py`**

```python
import sys
from dotsync.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests, expect PASS**

```bash
pytest tests/test_cli.py -v
```

Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add lib/dotsync/cli.py lib/dotsync/__main__.py tests/test_cli.py
git commit -m "feat(cli): argparse-based command dispatch"
```

---

## Task 12: bin/dotsync entry point

**Files:**
- Create: `bin/dotsync`

- [ ] **Step 1: Write `bin/dotsync`**

```python
#!/usr/bin/env python3
"""dotsync entry point — invoked by Homebrew-installed wrapper."""
import sys
from dotsync.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make executable**

```bash
chmod +x bin/dotsync
```

- [ ] **Step 3: Smoke test**

```bash
PYTHONPATH=lib python3 bin/dotsync --version
```

Expected: `dotsync 0.1.0`

- [ ] **Step 4: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add bin/dotsync
git commit -m "feat: bin/dotsync entry script"
```

---

## Task 13: Formula/dotsync.rb (Homebrew formula skeleton)

**Files:**
- Create: `Formula/dotsync.rb`

The actual `sha256` and `url` will be filled when you cut a v0.1.0 GitHub release. For now, write the formula with placeholders that will be replaced in Task 15.

- [ ] **Step 1: Write `Formula/dotsync.rb`**

```ruby
class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"
  depends_on "python@3.12"

  def install
    libexec.install "lib/dotsync"
    # Install the entry script and pin its shebang to python@3.12 so users
    # don't accidentally run dotsync under an older system python3.
    bin.install "bin/dotsync"
    py = Formula["python@3.12"].opt_bin/"python3.12"
    inreplace bin/"dotsync", %r{^#!.*python.*$}, "#!#{py}"
    bin.env_script_all_files(libexec/"bin", PYTHONPATH: libexec)
  end

  test do
    assert_match "dotsync 0.1.0", shell_output("#{bin}/dotsync --version")
  end
end
```

Note: the `sha256` placeholder will fail `brew install`; this is intentional until release. The formula structure is committed first, sha256 is patched after the GitHub release tarball exists (Task 15). The `inreplace` step pins the runtime to `python@3.12` regardless of what `/usr/bin/env python3` resolves to on the user's system.

- [ ] **Step 2: Commit**

```bash
git add Formula/dotsync.rb
git commit -m "feat: Homebrew formula skeleton (sha256 placeholder)"
```

---

## Task 14: README expansion

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace `README.md` with full content**

```markdown
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
dotsync init --dir ~/my-configs --apps claude,ghostty,bettertouchtool,zsh --yes
dotsync to --all
```

## Commands

| Command | Purpose |
|---|---|
| `dotsync init` | interactive setup; writes `~/.config/dotsync/config.toml` |
| `dotsync config dir <path>` | change sync folder |
| `dotsync config apps <a,b,c>` | change tracked apps |
| `dotsync config show` | print current config |
| `dotsync apps` | list supported apps |
| `dotsync status` | report sync state per app |
| `dotsync from <app>` / `dotsync from --all` | local → folder |
| `dotsync to <app>` / `dotsync to --all` | folder → local (with backup) |

## Supported apps (v0.1)

| App | What's synced |
|---|---|
| `claude` | `~/.claude/settings.json`, plugins (installed + marketplaces + per-plugin config), MCP servers from `~/.claude.json`. `dotsync to claude` auto-restores missing plugins via `claude plugin install`. |
| `ghostty` | `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty` |
| `bettertouchtool` | `Master_bt.bttpreset` via osascript export/import |
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
│   └── presets/Master_bt.bttpreset
└── zsh/
    └── .zshrc
```

## Backups

Every `dotsync to ...` writes a timestamped snapshot of the local files it's about to overwrite to:

```
~/.local/share/dotsync/backups/<YYYYMMDD_HHMMSS>/<app>/...
```

`backup_keep = 10` (default) keeps the 10 most recent sessions; older ones are pruned.

## Migration from Make-based dotfiles

If you currently have a `dotfiles` repo with `make claude-sync-from` etc., your `settings/` directory layout is already compatible. Just point dotsync at it:

```bash
dotsync init --dir ~/Desktop/dotfiles --apps claude,ghostty,bettertouchtool,zsh --yes
dotsync from --all   # refresh from current local state
```

You can then remove the `Makefile` and `make/*.mk` files at your leisure.

## Privacy / Security

- dotsync makes **no network calls** of its own. It only reads/writes local files and shells out to `claude` (for plugin restore) and `osascript` (for BTT).
- Your sync folder may contain personal data — be deliberate about pushing it to a public git remote.
- The Homebrew formula fetches a signed (sha256) tarball from this repo's GitHub release.

## License

MIT
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: full README"
```

---

## Task 15: Cut v0.1.0 release + patch formula sha256

**Files:**
- Modify: `Formula/dotsync.rb`

- [ ] **Step 1: Push main branch**

```bash
git push -u origin main
```

- [ ] **Step 2: Tag and push tag**

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

- [ ] **Step 3: Create GitHub release for v0.1.0**

Use `gh` CLI:

```bash
gh release create v0.1.0 --title "v0.1.0" --notes "Initial release. Supports claude, ghostty, bettertouchtool, zsh."
```

- [ ] **Step 4: Compute sha256 of release tarball**

```bash
curl -sL https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.0.tar.gz | shasum -a 256
```

Save the hex output (first 64 chars).

- [ ] **Step 5: Update `Formula/dotsync.rb` with real sha256**

Replace the placeholder `0000...0000` with the value from Step 4.

- [ ] **Step 6: Commit and push**

```bash
git add Formula/dotsync.rb
git commit -m "chore: real sha256 for v0.1.0"
git push
```

- [ ] **Step 7: End-to-end install test**

```bash
brew install changja88/dotsync/dotsync
dotsync --version
```

Expected: `dotsync 0.1.0`

- [ ] **Step 8: End-to-end functional test**

```bash
dotsync init --dir /tmp/dotsync-e2e --apps zsh --yes
dotsync from zsh
ls /tmp/dotsync-e2e/zsh/.zshrc
```

Expected: `/tmp/dotsync-e2e/zsh/.zshrc` exists.

Cleanup:

```bash
rm -rf /tmp/dotsync-e2e
brew uninstall dotsync   # if you don't want to keep it; otherwise leave it
```

---

## Self-Review Notes

**Spec coverage check:**

- ✅ Python 3.12+ stdlib only — Tasks 1, 3, 4 (no third-party imports in runtime)
- ✅ 4 apps — Tasks 6 (zsh), 7 (ghostty), 8 (bettertouchtool), 9 (claude)
- ✅ Config at `~/.config/dotsync/config.toml` — Task 3
- ✅ Backup at `~/.local/share/dotsync/backups/<ts>/` — Task 4 + integrated in Task 11
- ✅ CLI with init, config, from, to, status, apps — Task 11
- ✅ Homebrew Formula — Task 13, 15
- ✅ App abstract class — Task 5
- ✅ Claude plugin restore — folded into Task 9
- ✅ macOS BTT osascript — Task 8
- ✅ README + migration guide — Task 14
- ✅ Release flow — Task 15

**Type/method consistency:** `sync_from(target_dir)`, `sync_to(target_dir, backup_dir)`, `App.name`, `App.description`, `AppStatus(state, details)`, `diff_files(pairs)` consistent across Tasks 5–11.

**Factory consistency:** `build_app(name, cfg)` is the single entry to construct configured apps; CLI calls it for every subcommand. `APP_NAMES` (in `dotsync.apps`) and `SUPPORTED_APPS` (in `dotsync.config`) are kept in sync via the test in Task 10.

**Verified against real on-disk shapes:**
- `installed_plugins.json`: `{"version": 2, "plugins": {"<id>@<mp>": [{"installPath": ..., ...}]}}` (list values).
- `known_marketplaces.json`: top-level dict of marketplace names → `{"source": {"source": "github"|"directory", ...}}` (no wrapping).
- `settings.json["enabledPlugins"]`: drives `_enforce_disabled` in Task 9.
- All `claude` CLI calls use `--scope user` to match `make/claude-plugins-restore.py`.

**Placeholder scan:** All code blocks complete. The only intentional placeholder is `sha256 "0000..."` in Task 13, replaced in Task 15.
