"""dotsync CLI — argparse-based command dispatch."""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path
from typing import Sequence
from dotsync import __version__, ui
from dotsync.apps import APP_NAMES, app_descriptions, build_app, detect_present
from dotsync.backup import new_backup_session, rotate_backups
from dotsync.config import (
    Config,
    ConfigError,
    DEFAULT_BTT_PRESETS,
    ENV_VAR,
    SUPPORTED_APPS,
    folder_config_path,
    load_config,
    save_config,
)
from dotsync.welcome import print_welcome


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dotsync", description="Sync app configs with a folder.")
    p.add_argument("--version", action="version", version=f"dotsync {__version__}")
    sub = p.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="initialize config")
    init.add_argument("--dir", help="absolute path to sync folder")
    init.add_argument("--apps", help="comma-separated app names")
    init.add_argument("--btt-presets", default=None, help=f"BetterTouchTool preset names, comma-separated (default: {','.join(DEFAULT_BTT_PRESETS)})")
    init.add_argument("--yes", action="store_true", help="non-interactive: skip prompts")
    init.add_argument("--quiet", action="store_true", help="skip the welcome banner")
    init.add_argument("--no-hints", action="store_true", help="skip the post-init 'next steps' block")

    sub.add_parser("welcome", help="print the welcome banner")

    cfg = sub.add_parser("config", help="manage config")
    cfg_sub = cfg.add_subparsers(dest="cfg_cmd", required=True)
    cfg_dir = cfg_sub.add_parser("dir", help="set sync dir")
    cfg_dir.add_argument("path")
    cfg_apps = cfg_sub.add_parser("apps", help="set tracked apps")
    cfg_apps.add_argument("apps", help="comma-separated names")
    cfg_btt = cfg_sub.add_parser("btt-presets", help="set BetterTouchTool preset names (comma-separated)")
    cfg_btt.add_argument("presets", help="comma-separated names")
    cfg_sub.add_parser("show", help="print current config")

    sub.add_parser("apps", help="pick which apps to track (same UI as init)")
    sub.add_parser("status", help="report sync state")

    sync_from = sub.add_parser("from", help="local → folder")
    sync_from.add_argument("app", nargs="?", help="app name or omit with --all")
    sync_from.add_argument("--all", action="store_true")

    sync_to = sub.add_parser("to", help="folder → local")
    sync_to.add_argument("app", nargs="?", help="app name or omit with --all")
    sync_to.add_argument("--all", action="store_true")
    sync_to.add_argument("--dry-run", action="store_true",
                         help="print what would change and exit without modifying anything")
    sync_to.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt")

    return p


def cmd_welcome(args) -> int:
    print_welcome()
    return 0


def _default_sync_dir() -> Path:
    return Path.home() / "Desktop" / "dotsync_config"


def cmd_init(args) -> int:
    if not args.quiet:
        print_welcome()

    # Step 1 — Sync folder ----------------------------------------------------
    dir_path = _resolve_sync_folder(args)
    dir_path.mkdir(parents=True, exist_ok=True)

    # New-machine restore: if dotsync.toml already exists and the user passed
    # no overrides, adopt it as-is and skip the rest. This branch is the
    # "I cloned my dotsync_config on a fresh laptop" flow.
    existing = folder_config_path(dir_path)
    has_overrides = bool(args.apps) or bool(args.btt_presets)
    if existing.exists() and not has_overrides:
        ui.done(f"adopted existing config → {existing}")
        if not args.no_hints:
            _print_init_hints(dir_path)
        return 0

    ui.done(f"folder ready → {dir_path}")

    # Step 2 — Pick apps to track --------------------------------------------
    apps = _resolve_apps_for_init(args)
    if apps is None:
        return 2  # error already printed

    bad = [a for a in apps if a not in SUPPORTED_APPS]
    if bad:
        print(f"unknown apps: {bad}", file=sys.stderr)
        return 2

    if apps:
        ui.done(f"tracked: {' · '.join(apps)}")

    # BTT presets are auto-discovered and silently rolled into the config.
    btt_presets = _resolve_btt_presets(args, apps)

    save_config(Config(dir=dir_path, apps=apps, bettertouchtool_presets=btt_presets))
    print()
    ui.done(f"config saved → {folder_config_path(dir_path)}")
    if not args.no_hints:
        _print_init_hints(dir_path)
    return 0


def _resolve_sync_folder(args) -> Path:
    """Run Step 1 of init: prompt for (or accept --dir) the sync folder path."""
    default_dir = _default_sync_dir()
    if args.yes:
        return Path(args.dir).expanduser().resolve() if args.dir else default_dir

    print()
    ui.step("Step 1 — Sync folder")
    print(f"  {ui._wrap(ui.DIM_ANSI, 'Where should dotsync keep your synced configs?')}")
    print(f"  {ui._wrap(ui.DIM_ANSI, 'Press Enter to use the default, or paste an absolute path of your own.')}")
    print()
    dir_str = ui.ask("sync folder (absolute path)", default=str(default_dir))
    return Path(dir_str).expanduser().resolve() if dir_str else default_dir


def _resolve_btt_presets(args, apps: list[str]) -> list[str]:
    """Pick BTT presets to track.

    Precedence: explicit --btt-presets > auto-discovered (interactive only,
    when BTT is in apps) > DEFAULT_BTT_PRESETS. Auto-discovery syncs every
    preset BTT knows about — no per-preset prompt. --yes mode is intentionally
    deterministic and skips discovery entirely.
    """
    if args.btt_presets:
        return [p.strip() for p in args.btt_presets.split(",") if p.strip()]
    if "bettertouchtool" not in apps or args.yes:
        return list(DEFAULT_BTT_PRESETS)
    from .apps.bettertouchtool import BetterTouchToolApp
    discovered = BetterTouchToolApp.discover_preset_names()
    if discovered:
        ui.done(f"BetterTouchTool presets = {', '.join(discovered)}   (auto-detected)")
        return discovered
    return list(DEFAULT_BTT_PRESETS)


def _btt_annotation(detected: set[str]) -> "dict[str, str]":
    """Right-side annotation for the BTT row in the picker.

    Shows the detected preset count when BTT is locally installed so the
    user knows how many presets will be tracked if they keep the row checked.
    """
    if "bettertouchtool" not in detected:
        return {}
    from .apps.bettertouchtool import BetterTouchToolApp
    count = len(BetterTouchToolApp.discover_preset_names())
    if count <= 0:
        return {}
    suffix = "preset" if count == 1 else "presets"
    return {"bettertouchtool": f"{count} {suffix}"}


def _resolve_apps_for_init(args) -> "list[str] | None":
    """Step 2: determine which apps to track.

    Precedence: explicit --apps > picker (interactive). --yes without --apps
    accepts every detected app; non-interactive runs without detected apps
    error out so scripted calls never silently track nothing.
    """
    if args.apps is not None:
        return [a.strip() for a in args.apps.split(",") if a.strip()]

    detected = detect_present()

    if args.yes:
        if not detected:
            print(
                "no apps detected on this machine; pass --apps to specify",
                file=sys.stderr,
            )
            return None
        return detected

    print()
    ui.step("Step 2 — Pick apps to track")
    print()
    from .ui_picker import pick_apps
    result = pick_apps(
        sorted(SUPPORTED_APPS),
        preselected=set(detected),
        detected=set(detected),
        annotations=_btt_annotation(set(detected)),
    )
    if result is None:
        print("cancelled — no apps selected", file=sys.stderr)
        return None
    return result


def _print_init_hints(folder: Path) -> None:
    """Friendly post-init guidance, styled with the design system."""
    bullet = ui._wrap(ui.PRIMARY, "▸")
    bold = lambda s: ui._wrap(ui.BOLD, s)
    primary_bold = lambda s: ui._wrap(ui.PRIMARY, ui._wrap(ui.BOLD, s))
    dim = lambda s: ui._wrap(ui.DIM_ANSI, s)
    dim_bullet = ui._wrap(ui.DIM_ANSI, "·")

    print()
    ui.divider("next steps")
    print()

    # 1. shell rc — the most important follow-up
    export_line = f'export {ENV_VAR}="{folder}"'
    print(f"  {bullet} 1. {bold('Make dotsync available everywhere')}")
    print(f"       {dim('Add this one line to ~/.zshrc:')}")
    print()
    print(f"         {primary_bold(export_line)}")
    print()

    # 2. first sync
    print(f"  {bullet} 2. {bold('Take a snapshot of your local configs')}")
    print()
    print(f"         {primary_bold('dotsync from --all')}")
    print()

    # 3. restore on another machine
    print(f"  {bullet} 3. {bold('On another machine — pull configs from the folder')}")
    print()
    print(f"         {primary_bold('dotsync to --all')}")
    print()

    # Trailing dim hints — quiet pointers to the everyday commands.
    print(f"  {dim_bullet}  {dim('Change tracked apps later: ')} {primary_bold('dotsync apps')}")
    print(f"  {dim_bullet}  {dim('See current sync state:    ')} {primary_bold('dotsync status')}")
    print()


def cmd_config(args) -> int:
    if args.cfg_cmd == "show":
        cfg = load_config()
        print(f"dir = {cfg.dir}")
        print(f"apps = {cfg.apps}")
        print(f"backup_dir = {cfg.backup_dir}")
        print(f"backup_keep = {cfg.backup_keep}")
        print(f"bettertouchtool_presets = {cfg.bettertouchtool_presets}")
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
    if args.cfg_cmd == "btt-presets":
        cfg = load_config()
        new_presets = [p.strip() for p in args.presets.split(",") if p.strip()]
        if not new_presets:
            print("provide at least one preset name", file=sys.stderr)
            return 2
        cfg.bettertouchtool_presets = new_presets
        save_config(cfg)
        ui.done(f"bettertouchtool_presets = {new_presets}")
        return 0
    return 2


def cmd_apps(args) -> int:
    """Pick which apps dotsync tracks. Same UI as init's Step 2 — the
    picker is self-contained: each row shows install state + (for BTT) the
    detected preset count, and toggling BTT auto-refreshes its preset list.
    """
    from .ui_picker import pick_apps

    cfg = load_config()
    detected = set(detect_present())

    new_apps = pick_apps(
        sorted(SUPPORTED_APPS),
        preselected=set(cfg.apps),
        detected=detected,
        annotations=_btt_annotation(detected),
    )
    if new_apps is None:
        ui.dim("cancelled")
        return 0

    apps_changed = set(new_apps) != set(cfg.apps)
    new_btt_presets = _refresh_btt_presets_after_pick(new_apps, cfg)
    presets_changed = new_btt_presets != cfg.bettertouchtool_presets

    if not apps_changed and not presets_changed:
        ui.dim("no change")
        return 0

    cfg.apps = new_apps
    cfg.bettertouchtool_presets = new_btt_presets
    save_config(cfg)
    if apps_changed:
        ui.done(f"apps = {new_apps}")
    if presets_changed:
        ui.done(f"bettertouchtool_presets = {new_btt_presets}")
    return 0


def _refresh_btt_presets_after_pick(new_apps: list[str], cfg: Config) -> list[str]:
    """When the user toggles BTT in the picker, re-discover its presets.

    Toggling on → adopt every detected preset (matches init's behavior).
    Toggling off → keep the saved list as-is so the user doesn't lose it
    if they re-enable BTT later. No change → keep saved list.
    """
    was_tracked = "bettertouchtool" in cfg.apps
    is_tracked = "bettertouchtool" in new_apps
    if is_tracked and not was_tracked:
        from .apps.bettertouchtool import BetterTouchToolApp
        discovered = BetterTouchToolApp.discover_preset_names()
        if discovered:
            return discovered
    return cfg.bettertouchtool_presets


def cmd_status(args) -> int:
    cfg = load_config()
    ui.section("status", sub=str(cfg.dir))
    print()
    for name in cfg.apps:
        app = build_app(name, cfg)
        s = app.status(cfg.dir)
        print(ui.format_status_line(
            name,
            state=s.state,
            details=s.details,
            direction=getattr(s, "direction", ""),
        ))
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

    ui.banner(
        "dotsync from",
        f"{len(apps)} app{'s' if len(apps) != 1 else ''}  →  {cfg.dir}",
    )
    print()
    start = time.monotonic()
    synced: list[str] = []
    failed: list[str] = []
    for i, name in enumerate(apps, 1):
        app = build_app(name, cfg)
        ui.section(name, index=i, total=len(apps), sub=app.description)
        try:
            app.sync_from(cfg.dir)
            app._finish_ok()
            synced.append(name)
        except (FileNotFoundError, RuntimeError) as e:
            ui.error(str(e))
            failed.append(name)
        print()
    ui.summary(
        ok=len(synced), error=len(failed),
        duration_ms=int((time.monotonic() - start) * 1000),
        synced=synced or None,
        failed=failed or None,
    )
    return 0 if not failed else 6


def cmd_to(args) -> int:
    cfg = load_config()
    apps = _resolve_app_list(args, cfg)
    if not apps:
        return 2
    cfg.dir.mkdir(parents=True, exist_ok=True)

    ui.banner(
        "dotsync to",
        f"{len(apps)} app{'s' if len(apps) != 1 else ''}  ←  {cfg.dir}",
    )
    print()
    ui.section("preview", sub="what would change on this machine")
    print()
    statuses: dict[str, str] = {}
    for name in apps:
        app = build_app(name, cfg)
        s = app.status(cfg.dir)
        statuses[name] = s.state
        print(ui.format_status_line(
            name, state=s.state, details=s.details,
            direction=getattr(s, "direction", ""),
        ))
    print()

    if args.dry_run:
        ui.dim("dry-run: no files will be modified")
        return 0

    if not args.yes:
        answer = ui.ask(
            "Apply these changes to your local machine?",
            default="y/N",
            accent="warn",
        ).lower()
        if answer not in ("y", "yes"):
            ui.dim("aborted")
            return 0

    session = new_backup_session(cfg.backup_dir)
    ui.kv("backup", str(session))
    print()
    start = time.monotonic()
    applied: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    for i, name in enumerate(apps, 1):
        app = build_app(name, cfg)
        ui.section(name, index=i, total=len(apps), sub=app.description)
        # Already in sync? Skip the sync call entirely so we don't run
        # osascript/copy for no reason — and tell the user nothing moved.
        if statuses.get(name) == "clean":
            app._finish_unchanged()
            unchanged.append(name)
            print()
            continue
        try:
            app.sync_to(cfg.dir, session)
            app._finish_ok()
            applied.append(name)
        except (FileNotFoundError, RuntimeError) as e:
            ui.error(str(e))
            failed.append(name)
        print()
    rotate_backups(cfg.backup_dir, cfg.backup_keep)
    ui.summary(
        ok=len(applied) + len(unchanged), error=len(failed),
        duration_ms=int((time.monotonic() - start) * 1000),
        applied=applied or None,
        unchanged=unchanged or None,
        failed=failed or None,
    )
    return 0 if not failed else 6


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd is None:
            print_welcome()
            return 0
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "welcome":
            return cmd_welcome(args)
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
        ui.error(str(e))
        return 3
    except FileNotFoundError as e:
        ui.error(str(e))
        return 4
    except RuntimeError as e:
        ui.error(str(e))
        return 5


if __name__ == "__main__":
    sys.exit(main())
