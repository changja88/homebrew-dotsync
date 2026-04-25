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
    DEFAULT_BTT_PRESET,
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
    init.add_argument("--btt-preset", default=None, help=f"BetterTouchTool preset name (default: {DEFAULT_BTT_PRESET})")
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
    cfg_btt = cfg_sub.add_parser("btt-preset", help="set BetterTouchTool preset name")
    cfg_btt.add_argument("preset")
    cfg_sub.add_parser("show", help="print current config")

    sub.add_parser("apps", help="list supported apps")
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

    # 1. resolve sync folder path (default: ~/Desktop/dotsync_config)
    default_dir = _default_sync_dir()
    if args.yes:
        if args.dir:
            dir_path = Path(args.dir).expanduser().resolve()
        else:
            dir_path = default_dir
    else:
        print()
        print(f"  {ui._wrap(ui.DIM_ANSI, 'Where should dotsync keep your synced configs?')}")
        print(f"  {ui._wrap(ui.DIM_ANSI, 'Press Enter to use the default, or paste an absolute path of your own.')}")
        print()
        dir_str = ui.ask("sync folder (absolute path)", default=str(default_dir))
        dir_path = Path(dir_str).expanduser().resolve() if dir_str else default_dir
    dir_path.mkdir(parents=True, exist_ok=True)

    # 2. if folder already has a dotsync.toml and the user passed no overrides,
    #    just adopt it. This is the new-machine restore flow.
    existing = folder_config_path(dir_path)
    has_overrides = bool(args.apps) or bool(args.btt_preset)
    if existing.exists() and not has_overrides:
        ui.done(f"adopted existing config → {existing}")
        if not args.no_hints:
            _print_init_hints(dir_path)
        return 0

    # 3. resolve apps list (auto-detect if not specified)
    apps = _resolve_apps_for_init(args)
    if apps is None:
        return 2  # error already printed

    bad = [a for a in apps if a not in SUPPORTED_APPS]
    if bad:
        print(f"unknown apps: {bad}", file=sys.stderr)
        return 2

    # 4. resolve BTT preset
    btt_preset = args.btt_preset or DEFAULT_BTT_PRESET
    if not args.yes and "bettertouchtool" in apps:
        entered = ui.ask("BetterTouchTool preset name", default=btt_preset)
        if entered:
            btt_preset = entered

    # 5. save + hints
    save_config(Config(dir=dir_path, apps=apps, bettertouchtool_preset=btt_preset))
    ui.done(f"config saved → {folder_config_path(dir_path)}")
    if not args.no_hints:
        _print_init_hints(dir_path)
    return 0


def _resolve_apps_for_init(args) -> "list[str] | None":
    """Determine which apps to track.

    Precedence: explicit --apps > auto-detected (with confirmation if interactive).
    Returns None on error (after printing to stderr).
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

    # interactive
    print()
    if detected:
        print("Detected on this machine:")
        for name in detected:
            print(f"  {ui._wrap(ui.GREEN, ui.GLYPH_OK)} {name}")
        choice = ui.ask("Track all of these?", default="Y/n/edit").lower()
    else:
        print("No apps were auto-detected on this machine.")
        choice = "edit"

    if choice in ("", "y", "yes"):
        return detected
    if choice in ("n", "no"):
        return []
    if choice in ("edit", "e"):
        apps_str = ui.ask(
            f"apps to track (comma-separated, options: {sorted(SUPPORTED_APPS)})"
        )
        return [a.strip() for a in apps_str.split(",") if a.strip()]
    print(f"unknown choice: {choice}", file=sys.stderr)
    return None


def _print_init_hints(folder: Path) -> None:
    """Friendly post-init guidance, styled with the design system."""
    bullet = ui._wrap(ui.PRIMARY, "▸")
    bold = lambda s: ui._wrap(ui.BOLD, s)
    dim = lambda s: ui._wrap(ui.DIM_ANSI, s)

    print()
    ui.divider("next steps")
    print()

    # 1. shell rc — the most important follow-up
    print(f"  {bullet} {bold('Run dotsync from any directory.')}")
    print(f"    {dim('Add this one line to your shell rc (~/.zshrc, ~/.bashrc, ...):')}")
    print()
    print(f"      {bold(f'export {ENV_VAR}=\"{folder}\"')}")
    print()
    print(f"    {dim('Or just `cd` into the folder before running dotsync — it')}")
    print(f"    {dim('auto-discovers dotsync.toml by walking up.')}")
    print()

    # 2. snapshot
    print(f"  {bullet} {bold('Take a snapshot of your current local configs:')}")
    print()
    print(f"      {bold('dotsync from --all')}")
    print()

    # 3. change tracked apps
    print(f"  {bullet} {bold('Change which apps are tracked, any time:')}")
    print(f"    {dim('(comma-separated list of: claude, ghostty, bettertouchtool, zsh)')}")
    print()
    print(f"      {bold('dotsync config apps zsh,claude')}")
    print()


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

    ui.banner("dotsync from", f"{len(apps)} app{'s' if len(apps) != 1 else ''} · {cfg.dir}")
    print()
    start = time.monotonic()
    ok_count = 0
    err_count = 0
    for i, name in enumerate(apps, 1):
        app = build_app(name, cfg)
        ui.section(name, index=i, total=len(apps), sub=app.description)
        try:
            app.sync_from(cfg.dir)
            ok_count += 1
        except (FileNotFoundError, RuntimeError) as e:
            ui.error(str(e))
            err_count += 1
        print()
    ui.summary(ok=ok_count, error=err_count, duration_ms=int((time.monotonic() - start) * 1000))
    return 0 if err_count == 0 else 6


def cmd_to(args) -> int:
    cfg = load_config()
    apps = _resolve_app_list(args, cfg)
    if not apps:
        return 2
    cfg.dir.mkdir(parents=True, exist_ok=True)

    ui.banner("dotsync to", f"{len(apps)} app{'s' if len(apps) != 1 else ''} · {cfg.dir}")
    print()
    ui.section("preview", sub="what would change on this machine")
    print()
    for name in apps:
        app = build_app(name, cfg)
        s = app.status(cfg.dir)
        print(ui.format_status_line(
            name, state=s.state, details=s.details,
            direction=getattr(s, "direction", ""),
        ))
    print()

    if args.dry_run:
        ui.dim("dry-run: no files will be modified")
        return 0

    if not args.yes:
        answer = ui.ask("Apply these changes to your local machine?", default="y/N").lower()
        if answer not in ("y", "yes"):
            ui.dim("aborted")
            return 0

    session = new_backup_session(cfg.backup_dir)
    ui.kv("backup", str(session))
    print()
    start = time.monotonic()
    ok_count = 0
    err_count = 0
    for i, name in enumerate(apps, 1):
        app = build_app(name, cfg)
        ui.section(name, index=i, total=len(apps), sub=app.description)
        try:
            app.sync_to(cfg.dir, session)
            ok_count += 1
        except (FileNotFoundError, RuntimeError) as e:
            ui.error(str(e))
            err_count += 1
        print()
    rotate_backups(cfg.backup_dir, cfg.backup_keep)
    ui.summary(ok=ok_count, error=err_count, duration_ms=int((time.monotonic() - start) * 1000))
    return 0 if err_count == 0 else 6


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
