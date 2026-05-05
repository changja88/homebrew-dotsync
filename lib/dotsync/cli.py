"""dotsync CLI — argparse-based command dispatch."""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path
from typing import Sequence
from dotsync import __version__, ui
from dotsync.apps import APP_CLASSES, APP_NAMES, app_descriptions, build_app, detect_present
from dotsync.backup import new_backup_session, rotate_backups
from dotsync.config import (
    Config,
    ConfigError,
    ENV_VAR,
    folder_config_path,
    load_config,
    save_config,
)
from dotsync.plan import AppPlan
from dotsync.shellrc import (
    ShellRcResult,
    detect_rc_path,
    export_line,
    update_shell_rc,
)
from dotsync.welcome import print_welcome

# Existing call sites use this name; alias to the registry's source of truth.
SUPPORTED_APPS = APP_NAMES


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dotsync", description="Sync app configs with a folder.")
    p.add_argument("--version", action="version", version=f"dotsync {__version__}")
    sub = p.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="initialize config")
    init.add_argument("--dir", help="absolute path to sync folder")
    init.add_argument("--apps", help="comma-separated app names")
    for app_cls in APP_CLASSES:
        app_cls.extra_init_args(init)
    init.add_argument("--yes", action="store_true", help="non-interactive: skip prompts")
    init.add_argument("--quiet", action="store_true", help="skip the welcome banner")
    init.add_argument("--no-hints", action="store_true", help="skip the post-init 'next steps' block")
    init.add_argument("--no-shell-init", action="store_true",
                      help="don't add `export DOTSYNC_DIR=...` to ~/.zshrc (or ~/.bash_profile)")

    sub.add_parser("welcome", help="print the welcome banner")

    cfg = sub.add_parser("config", help="manage config")
    cfg_sub = cfg.add_subparsers(dest="cfg_cmd", required=True)
    cfg_dir = cfg_sub.add_parser("dir", help="set sync dir")
    cfg_dir.add_argument("path")
    cfg_apps = cfg_sub.add_parser("apps", help="set tracked apps")
    cfg_apps.add_argument("apps", help="comma-separated names")
    for app_cls in APP_CLASSES:
        app_cls.extra_config_subcommands(cfg_sub)
    cfg_sub.add_parser("show", help="print current config")

    sub.add_parser("apps", help="pick which apps to track (same UI as init)")
    sub.add_parser("status", help="report sync state")

    sync_from = sub.add_parser("from", help="local → folder")
    sync_from.add_argument("app", nargs="?", help="app name or omit with --all")
    sync_from.add_argument("--all", action="store_true")
    sync_from.add_argument("--dry-run", action="store_true",
                           help="print what would change and exit without modifying anything")
    sync_from.add_argument("--yes", action="store_true",
                           help="skip the confirmation prompt")

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
        rc_result = _maybe_update_shell_rc(args, dir_path)
        if not args.no_hints:
            _print_init_hints(dir_path, rc_result)
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

    # Each app supplies its own options via its resolve_options hook.
    interactive = not args.yes
    app_options = _resolve_app_options(args, prev_apps=[], new_apps=apps, interactive=interactive)
    if interactive:
        for app_name, opts in app_options.items():
            # Surface auto-discovered options so the user can see what was set.
            opts_summary = ", ".join(
                f"{k} = {v}" if not isinstance(v, list) else f"{k} = {', '.join(v)}"
                for k, v in opts.items()
            )
            ui.done(f"{app_name}: {opts_summary}   (auto-detected)")

    save_config(Config(dir=dir_path, apps=apps, app_options=app_options))
    print()
    ui.done(f"config saved → {folder_config_path(dir_path)}")
    rc_result = _maybe_update_shell_rc(args, dir_path)
    if not args.no_hints:
        _print_init_hints(dir_path, rc_result)
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


def _picker_annotations(detected: set[str]) -> dict[str, str]:
    """Collect picker annotations from every App's picker_annotation hook."""
    result: dict[str, str] = {}
    for app_cls in APP_CLASSES:
        ann = app_cls.picker_annotation(detected=app_cls.name in detected)
        if ann:
            result[app_cls.name] = ann
    return result


def _resolve_app_options(
    args,
    *,
    prev_apps: list[str],
    new_apps: list[str],
    interactive: bool,
) -> dict[str, dict]:
    """Collect each App's options dict via its resolve_options hook."""
    out: dict[str, dict] = {}
    for app_cls in APP_CLASSES:
        opts = app_cls.resolve_options(
            args, prev_apps=prev_apps, new_apps=new_apps, interactive=interactive,
        )
        if opts is not None:
            out[app_cls.name] = opts
    return out


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
        annotations=_picker_annotations(set(detected)),
    )
    if result is None:
        print("cancelled — no apps selected", file=sys.stderr)
        return None
    return result


def _maybe_update_shell_rc(args, dir_path: Path) -> "ShellRcResult | None":
    """Step 3 (optional): wire `export DOTSYNC_DIR=...` into the user's rc.

    Behavior:
      - `--no-shell-init`            → skip entirely, return None.
      - unknown shell (fish, nu, …) → no rc to safely edit, return None.
      - `--yes`                      → consent is implicit, write directly.
      - interactive                  → prompt with [Y/n] (default Y).
      - rc file doesn't exist        → don't create one, return None.
    """
    if args.no_shell_init:
        return None
    rc_path = detect_rc_path()
    if rc_path is None:
        return None
    if not rc_path.exists():
        # Don't create rc files on the user's behalf; the next-steps block
        # will tell them what to add manually.
        return None

    if not args.yes:
        line = export_line(dir_path)
        ans = ui.ask(
            f"Add `{line}` to {rc_path.name}?",
            default="Y/n",
        ).lower()
        if ans not in ("", "y", "yes"):
            return None

    result = update_shell_rc(rc_path, dir_path)
    if result.action in ("added", "updated"):
        ui.done(f"{rc_path} updated — open a new shell or `source {rc_path.name}` to apply")
    elif result.action == "already_set":
        ui.dim(f"{rc_path.name} already has the export — left as is")
    return result


def _print_init_hints(folder: Path, rc_result: "ShellRcResult | None" = None) -> None:
    """Friendly post-init guidance, styled with the design system.

    When the rc file was just updated (`added` / `updated` / `already_set`),
    the big "Add this one line" block shrinks to a one-liner pointer at the
    rc file. Otherwise (declined, unsupported shell, rc missing) we render
    the full export instructions so the user has a copy-paste target.
    """
    bullet = ui._wrap(ui.PRIMARY, "▸")
    bold = lambda s: ui._wrap(ui.BOLD, s)
    primary_bold = lambda s: ui._wrap(ui.PRIMARY, ui._wrap(ui.BOLD, s))
    dim = lambda s: ui._wrap(ui.DIM_ANSI, s)
    dim_bullet = ui._wrap(ui.DIM_ANSI, "·")

    print()
    ui.divider("next steps")
    print()

    rc_handled = rc_result is not None and rc_result.action in (
        "added", "updated", "already_set",
    )
    export_str = f'export {ENV_VAR}="{folder}"'

    # 1. shell rc — the most important follow-up
    if rc_handled:
        rc_path = rc_result.rc_path
        print(f"  {bullet} 1. {bold('dotsync is wired into your shell')}")
        print(f"       {dim(f'Already in {rc_path.name}: ')}{primary_bold(export_str)}")
        print()
    else:
        print(f"  {bullet} 1. {bold('Make dotsync available everywhere')}")
        print(f"       {dim('Add this one line to ~/.zshrc:')}")
        print()
        print(f"         {primary_bold(export_str)}")
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
    # Delegate any non-core subcommands to the matching app's hook.
    cfg = load_config()
    for app_cls in APP_CLASSES:
        rc = app_cls.handle_config_subcommand(args, cfg)
        if rc is not None:
            return rc
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
        annotations=_picker_annotations(detected),
    )
    if new_apps is None:
        ui.dim("cancelled")
        return 0

    apps_changed = set(new_apps) != set(cfg.apps)
    # Construct synthetic args namespace with no flags — apps re-discover by toggle.
    import argparse
    args_for_resolve = argparse.Namespace(yes=False)
    new_options = _resolve_app_options(
        args_for_resolve, prev_apps=cfg.apps, new_apps=new_apps, interactive=True,
    )
    options_changed = bool(new_options) and any(
        cfg.app_options.get(k) != v for k, v in new_options.items()
    )

    if not apps_changed and not options_changed:
        ui.dim("no change")
        return 0

    cfg.apps = new_apps
    for k, v in new_options.items():
        cfg.app_options[k] = v
    save_config(cfg)
    if apps_changed:
        ui.done(f"apps = {new_apps}")
    if options_changed:
        for k, v in new_options.items():
            ui.done(f"{k} options = {v}")
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


def _print_app_warnings(warnings_by_app: dict[str, list[str]]) -> None:
    """Render any collected non-fatal warnings under a 'warnings' divider.
    Called after the sync summary so partial failures aren't hidden."""
    if not warnings_by_app:
        return
    print()
    ui.divider("warnings")
    for name, warns in warnings_by_app.items():
        for w in warns:
            ui.warn(f"{name}: {w}")


def _resolve_app_list(args, cfg: Config) -> list[str]:
    if args.all:
        return list(cfg.apps)
    if not args.app:
        print("provide app name or --all", file=sys.stderr)
        return []
    return [args.app]


def _build_plans(apps: list[str], cfg: Config, direction: str) -> list[AppPlan]:
    plans = []
    for name in apps:
        app = build_app(name, cfg)
        if direction == "from":
            plans.append(app.plan_from(cfg.dir))
        else:
            plans.append(app.plan_to(cfg.dir))
    return plans


def _print_preview(plans: list[AppPlan], *, direction: str) -> None:
    sub = (
        "what would change in the sync folder"
        if direction == "from"
        else "what would change on this machine"
    )
    ui.section("preview", sub=sub)
    print()
    for plan in plans:
        ui.section(plan.app, sub=plan.description)
        if not plan.changes:
            ui.dim("unknown")
        else:
            for change in plan.changes:
                ui.plan_change(change)
        print()


def _confirm_or_abort(args, *, direction: str) -> bool:
    if args.dry_run:
        ui.dim("dry-run: no files will be modified")
        return False
    if args.yes:
        return True
    target = "the sync folder" if direction == "from" else "your local machine"
    answer = ui.ask(
        f"Apply these changes to {target}?",
        default="y/N",
        accent="warn",
    ).lower()
    if answer not in ("y", "yes"):
        ui.dim("aborted")
        return False
    return True


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
    plans = _build_plans(apps, cfg, "from")
    _print_preview(plans, direction="from")
    if not _confirm_or_abort(args, direction="from"):
        return 0

    unchanged_by_plan = {
        plan.app: bool(plan.changes) and not plan.has_changes
        for plan in plans
    }
    start = time.monotonic()
    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    warnings_by_app: dict[str, list[str]] = {}
    for i, name in enumerate(apps, 1):
        app = build_app(name, cfg)
        ui.section(name, index=i, total=len(apps), sub=app.description)
        if unchanged_by_plan.get(name, False):
            app._finish_unchanged()
            unchanged.append(name)
            print()
            continue
        try:
            app.sync_from(cfg.dir)
            app._finish_ok()
            changed.append(name)
        except (FileNotFoundError, RuntimeError) as e:
            ui.error(str(e))
            failed.append(name)
        if app.warnings:
            warnings_by_app[name] = list(app.warnings)
        print()
    ui.summary(
        ok=len(changed) + len(unchanged), error=len(failed),
        duration_ms=int((time.monotonic() - start) * 1000),
        changed=changed or None,
        unchanged=unchanged or None,
        failed=failed or None,
    )
    _print_app_warnings(warnings_by_app)
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
    plans = _build_plans(apps, cfg, "to")
    _print_preview(plans, direction="to")
    if not _confirm_or_abort(args, direction="to"):
        return 0
    unchanged_by_plan = {
        plan.app: bool(plan.changes) and not plan.has_changes
        for plan in plans
    }

    session = new_backup_session(cfg.backup_dir)
    ui.kv("backup", str(session))
    print()
    start = time.monotonic()
    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    warnings_by_app: dict[str, list[str]] = {}
    for i, name in enumerate(apps, 1):
        app = build_app(name, cfg)
        ui.section(name, index=i, total=len(apps), sub=app.description)
        if unchanged_by_plan.get(name, False):
            app._finish_unchanged()
            unchanged.append(name)
            print()
            continue
        try:
            app.sync_to(cfg.dir, session)
            app._finish_ok()
            changed.append(name)
        except (FileNotFoundError, RuntimeError) as e:
            ui.error(str(e))
            failed.append(name)
        if app.warnings:
            warnings_by_app[name] = list(app.warnings)
        print()
    rotate_backups(cfg.backup_dir, cfg.backup_keep)
    ui.summary(
        ok=len(changed) + len(unchanged), error=len(failed),
        duration_ms=int((time.monotonic() - start) * 1000),
        changed=changed or None,
        unchanged=unchanged or None,
        failed=failed or None,
    )
    _print_app_warnings(warnings_by_app)
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
