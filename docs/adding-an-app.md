# Adding a new app to dotsync

After the extensibility refactor (2026-04-26), adding a simple file-based app
takes 4 steps. Complex apps (external processes, multiple files, app-specific
options) follow the same shape — they just override more hooks.

## 1. Create the module

`lib/dotsync/apps/<yourapp>.py`:

```python
from __future__ import annotations
from pathlib import Path
from dotsync.apps.base import App, FilePair


class YourApp(App):
    name = "yourapp"
    description = "One-line human-readable purpose"

    def tracked_files(self, target_dir: Path) -> list[FilePair]:
        return [FilePair(
            local=Path.home() / ".yourapprc",
            stored=target_dir / self.name / ".yourapprc",
            label=".yourapprc",
        )]
```

That's it for a simple app. The default `sync_from`/`sync_to`/`status` walk
`tracked_files()` automatically.

## 2. Register the class

`lib/dotsync/apps/__init__.py`:

```python
from dotsync.apps.yourapp import YourApp

APP_CLASSES = (
    ClaudeApp,
    GhosttyApp,
    BetterTouchToolApp,
    ZshApp,
    YourApp,  # ← add this line
)
```

`APP_NAMES`, `build_app()`, `config.supported_apps()` and the app list
dotsync.app shows all derive from `APP_CLASSES`. No other site requires
changes.

## 3. Write tests

`tests/apps/test_yourapp.py`:

```python
from pathlib import Path
from dotsync.apps.yourapp import YourApp


def test_sync_from_copies(fake_home, tmp_path):
    (fake_home / ".yourapprc").write_text("hello")
    target = tmp_path / "sync"; target.mkdir()
    YourApp().sync_from(target)
    assert (target / "yourapp" / ".yourapprc").read_text() == "hello"
```

The default sync impls are already covered by `tests/apps/test_base.py`, so you
only need to test the actual file paths.

For round-trip safety, append to `tests/integration/test_roundtrip.py`:

```python
def test_yourapp_from_then_to_does_not_change_local(fake_home, tmp_path):
    (fake_home / ".yourapprc").write_text("X")
    target = tmp_path / "sync"; target.mkdir()
    YourApp().sync_from(target)
    YourApp().sync_to(target)
    assert (fake_home / ".yourapprc").read_text() == "X"
```

## 4. Update README (KR + EN)

In `README.md` add `yourapp` to both the Korean and English supported-apps
lists. Both sections must stay in parity per `CLAUDE.md`.

## When the simple form isn't enough

| Need | Approach |
|---|---|
| App-specific options (preset names, theme) | Override `from_config(cls, cfg)` to read `cfg.app_options[cls.name]`; override `options_when_turned_on()` for the options it gets when dotsync.app turns it on. See `BetterTouchToolApp` for a worked example. |
| External process (CLI/AppleScript) | Use `self._run_external(cmd, desc=..., fail_mode="warn"\|"raise")` from `App`. fail_mode="warn" auto-collects failures into `self.warnings`, which each push/pull result carries. |
| Multi-file or directory tree | Return multiple `FilePair`s. The default impls already iterate them. |
| Custom import flow | Override `sync_to(target_dir)` directly; you can still call `super().sync_to()` for the file-copy half if your custom logic is post-hoc (`ZshApp.sync_to` overrides it whole: it writes the stored `.zshrc` but keeps this Mac's marked `DOTSYNC_DIR` line). |
| Status that compares non-files (live exports) | Override `status()`; reuse `diff_files` for any file portion. See `BetterTouchToolApp.status` for a worked example with `osascript` live diff. |

## Cross-cutting rules (carried over from CLAUDE.md)

- **stdlib only.** No `requests`, no `pydantic`, no `click`. Allowed: `tomllib`, `argparse`, `shutil`, `pathlib`, `subprocess`, `json`, `dataclasses`, `abc`, `hashlib`, `re`, `sqlite3`. The Homebrew formula stays single-`python@3.12`-dep.
- **macOS only.** No Linux branches. macOS-specific paths (`~/Library/Application Support/...`) are fine.
- **No network calls** from dotsync itself. External processes that hit the network (claude plugin install, BTT) are OK if invoked via the user's existing CLI.
- **`push` = local→folder, `pull` = folder→local.** Internally, app
  plugins still implement `sync_from` and `sync_to`. Neither keeps backup
  copies; the preview dotsync.app shows before applying is the safety net.

## Checklist

Before opening a PR:

- [ ] `make test` passes
- [ ] New app's tests live in `tests/apps/test_<name>.py`
- [ ] Round-trip test added to `tests/integration/test_roundtrip.py`
- [ ] README KR + EN both list the app
- [ ] No new top-level dependency (stdlib only — see CLAUDE.md)
