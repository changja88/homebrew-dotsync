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
    folder_config_path,
    load_config,
    pointer_path,
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

    sub.add_parser("apps", help="list supported apps")
    sub.add_parser("status", help="report sync state")

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
        dir_str = input("sync folder (absolute path): ").strip()
        dir_path = Path(dir_str).expanduser().resolve()
        apps_str = input(f"apps to track (comma-separated, options: {sorted(SUPPORTED_APPS)}): ").strip()
        apps = [a.strip() for a in apps_str.split(",") if a.strip()]
        btt_preset = args.btt_preset or DEFAULT_BTT_PRESET
        if "bettertouchtool" in apps:
            entered = input(f"BetterTouchTool preset name [{btt_preset}]: ").strip()
            if entered:
                btt_preset = entered

    bad = [a for a in apps if a not in SUPPORTED_APPS]
    if bad:
        print(f"unknown apps: {bad}", file=sys.stderr)
        return 2

    dir_path.mkdir(parents=True, exist_ok=True)
    save_config(Config(dir=dir_path, apps=apps, bettertouchtool_preset=btt_preset))
    ui.done(f"config saved → {folder_config_path(dir_path)}")
    ui.sub(f"pointer  → {pointer_path()}")
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
