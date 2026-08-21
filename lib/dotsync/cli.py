"""dotsync CLI — argparse-based command dispatch."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Sequence
from dotsync import __version__, ui, diffinfo
from dotsync.apps import APP_CLASSES, APP_NAMES, build_app, detect_present
from dotsync.backup import new_backup_session, rotate_backups
from dotsync.config import (
    Config,
    ConfigError,
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
from dotsync.sync_service import SyncEvents, SyncService
from dotsync.ui_app import check_ui_installation, run_browser_ui, run_native_ui

# Existing call sites use this name; alias to the registry's source of truth.
SUPPORTED_APPS = APP_NAMES


def _add_sync_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("app", nargs="?", help="app name or omit with --all")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would change and exit without modifying anything",
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dotsync", description="Sync app configs with a folder."
    )
    p.add_argument("--version", action="version", version=f"dotsync {__version__}")
    sub = p.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="initialize config")
    init.add_argument("--dir", help="absolute path to sync folder")
    init.add_argument("--apps", help="comma-separated app names")
    for app_cls in APP_CLASSES:
        app_cls.extra_init_args(init)
    init.add_argument(
        "--yes", action="store_true", help="non-interactive: skip prompts"
    )
    init.add_argument("--quiet", action="store_true", help="skip the welcome banner")
    init.add_argument(
        "--no-hints", action="store_true", help="skip the post-init 'next steps' block"
    )
    init.add_argument(
        "--no-shell-init",
        action="store_true",
        help="don't add `export DOTSYNC_DIR=...` to ~/.zshrc (or ~/.bash_profile)",
    )

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

    backup = sub.add_parser("backup", help="local → folder")
    _add_sync_args(backup)

    apply = sub.add_parser("apply", help="folder → local")
    _add_sync_args(apply)

    ui_parser = sub.add_parser("ui", help="open the local management UI")
    ui_parser.add_argument(
        "--no-open",
        action="store_true",
        help="start the UI server without opening a browser",
    )
    internal_modes = ui_parser.add_mutually_exclusive_group()
    internal_modes.add_argument(
        "--native-host",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    internal_modes.add_argument(
        "--check",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    return p


def _normalize_legacy_command(argv: Sequence[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    normalized = list(argv)
    if not normalized:
        return normalized
    if normalized[0] == "from":
        normalized[0] = "backup"
    elif normalized[0] == "to":
        normalized[0] = "apply"
    return normalized


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
    app_options = _resolve_app_options(
        args, prev_apps=[], new_apps=apps, interactive=interactive
    )
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
    print(
        f"  {ui._wrap(ui.DIM_ANSI, 'Where should dotsync keep your synced configs?')}"
    )
    print(
        f"  {ui._wrap(ui.DIM_ANSI, 'Press Enter to use the default, or paste an absolute path of your own.')}"
    )
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
            args,
            prev_apps=prev_apps,
            new_apps=new_apps,
            interactive=interactive,
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
        ui.done(
            f"{rc_path} updated — open a new shell or `source {rc_path.name}` to apply"
        )
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

    def bold(s: str) -> str:
        return ui._wrap(ui.BOLD, s)

    def primary_bold(s: str) -> str:
        return ui._wrap(ui.PRIMARY, ui._wrap(ui.BOLD, s))

    def dim(s: str) -> str:
        return ui._wrap(ui.DIM_ANSI, s)

    dim_bullet = ui._wrap(ui.DIM_ANSI, "·")

    print()
    ui.divider("next steps")
    print()

    rc_handled = rc_result is not None and rc_result.action in (
        "added",
        "updated",
        "already_set",
    )
    export_str = export_line(folder)

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
    print(f"         {primary_bold('dotsync backup --all')}")
    print()

    # 3. restore on another machine
    print(f"  {bullet} 3. {bold('On another machine — pull configs from the folder')}")
    print()
    print(f"         {primary_bold('dotsync apply --all')}")
    print()

    # Trailing dim hints — quiet pointers to the everyday commands.
    print(
        f"  {dim_bullet}  {dim('Change tracked apps later: ')} {primary_bold('dotsync apps')}"
    )
    print(
        f"  {dim_bullet}  {dim('See current sync state:    ')} {primary_bold('dotsync status')}"
    )
    print()


def cmd_config(args) -> int:
    if args.cfg_cmd == "show":
        cfg = load_config()
        print(f"dir = {cfg.dir}")
        print(f"apps = {cfg.apps}")
        print(f"backup_dir = {cfg.backup_dir}")
        print(f"backup_keep = {cfg.backup_keep}")
        print(f"bettertouchtool_presets = {cfg.bettertouchtool_presets}")
        print(f"app_options = {cfg.app_options}")
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
        _sync_service(cfg).update_apps(tuple(new_apps))
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
        args_for_resolve,
        prev_apps=cfg.apps,
        new_apps=new_apps,
        interactive=True,
    )
    options_changed = bool(new_options) and any(
        cfg.app_options.get(k) != v for k, v in new_options.items()
    )

    if not apps_changed and not options_changed:
        ui.dim("no change")
        return 0

    for k, v in new_options.items():
        cfg.app_options[k] = v
    _sync_service(cfg).update_apps(tuple(new_apps))
    if apps_changed:
        ui.done(f"apps = {new_apps}")
    if options_changed:
        for k, v in new_options.items():
            ui.done(f"{k} options = {v}")
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    status = _sync_service(cfg).status()
    ui.section("status", sub=str(status.sync_dir))
    print()
    for app_status in status.apps:
        s = app_status.status
        print(
            ui.format_status_line(
                app_status.name,
                state=s.state,
                details=s.details,
                direction=s.direction,
            )
        )
        if app_status.plan is None:
            continue
        for change in app_status.plan.changes:
            if change.is_change:
                print("  " + ui.format_plan_change(change))
    return 0


class _CliSyncEvents(SyncEvents):
    def app_started(self, name: str, app, *, index: int, total: int) -> None:
        ui.section(name, index=index, total=total, sub=app.description)

    def backup_created(self, backup_dir: Path) -> None:
        ui.kv("backup", str(backup_dir))
        print()

    def app_succeeded(self, app) -> None:
        app._finish_ok()

    def app_unchanged(self, app) -> None:
        app._finish_unchanged()

    def app_failed(self, app, error: Exception) -> None:
        ui.error(str(error))

    def app_finished(self, app) -> None:
        print()


def _sync_service(
    cfg: Config, *, events: SyncEvents | None = None
) -> SyncService:
    """Construct the shared service with this CLI module's test seams."""
    return SyncService(
        cfg,
        events=events,
        app_factory=build_app,
        backup_session_factory=new_backup_session,
        backup_rotator=rotate_backups,
    )


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
    if args.app not in SUPPORTED_APPS:
        print(
            f"unknown app `{args.app}` (supported: {sorted(SUPPORTED_APPS)})",
            file=sys.stderr,
        )
        return []
    return [args.app]


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


def _confirm_or_abort(args, plans: "list[AppPlan]", *, direction: str) -> bool:
    if args.dry_run:
        ui.dim("dry-run: no files will be modified")
        return False
    if args.yes:
        return True
    target = "the sync folder" if direction == "from" else "your local machine"
    while True:
        answer = ui.ask(
            f"Apply these changes to {target}?",
            default="y/N/d",
            accent="warn",
        ).lower()
        if answer == "d":
            _print_full_diffs(plans)
            continue
        if answer in ("y", "yes"):
            return True
        ui.dim("aborted")
        return False


def _print_full_diffs(plans: "list[AppPlan]") -> None:
    """d키: 변경 항목마다 구분선 + 전체 diff를 lazy 계산해 출력."""
    for plan in plans:
        for change in plan.changes:
            if change.kind not in ("create", "update", "remove"):
                continue
            print()
            ui.divider(f"{plan.app}/{change.label}")
            ui.diff(_change_diff_text(change))
    print()


def _change_diff_text(change) -> str:
    if not change.diffable:
        return "(semantic change — no file diff)"
    unavailable = "(diff unavailable: no on-disk copy to compare)"
    if change.file_changes:  # tree mirror: 파일별 diff
        if change.source is None or change.dest is None:
            return unavailable
        return _tree_diff_text(change)
    if change.kind == "update":
        if change.source is None or change.dest is None:
            return unavailable
        return diffinfo.unified_diff_text(change.source, change.dest)
    if change.kind == "create":
        if change.source is None:
            return unavailable
        return diffinfo.full_file_lines(change.source, "+")
    if change.dest is None:
        return unavailable
    return diffinfo.full_file_lines(change.dest, "-")


def _tree_diff_text(change) -> str:
    blocks: "list[str]" = []
    for entry in change.file_changes:
        symbol, _, rel = entry.partition(" ")
        if symbol == "+":
            block = diffinfo.full_file_lines(change.source / rel, "+")
        elif symbol == "−":
            block = diffinfo.full_file_lines(change.dest / rel, "-")
        elif symbol == "~":
            block = diffinfo.unified_diff_text(change.source / rel, change.dest / rel)
        else:
            continue
        blocks.append(f"◦ {rel}\n{block}")
    return "\n\n".join(blocks)


def cmd_from(args) -> int:
    cfg = load_config()
    apps = _resolve_app_list(args, cfg)
    if not apps:
        return 2
    cfg.dir.mkdir(parents=True, exist_ok=True)

    ui.banner(
        "dotsync backup",
        f"{len(apps)} app{'s' if len(apps) != 1 else ''}  →  {cfg.dir}",
    )
    print()
    service = _sync_service(cfg, events=_CliSyncEvents())
    preview = service.preview(direction="backup", apps=tuple(apps))
    plans = list(preview.plans)
    _print_preview(plans, direction="from")
    if not _confirm_or_abort(args, plans, direction="from"):
        return 0
    result = service.execute(preview.digest)
    ui.summary(
        ok=len(result.changed) + len(result.unchanged),
        error=len(result.failed),
        duration_ms=result.duration_ms,
        changed=list(result.changed) or None,
        unchanged=list(result.unchanged) or None,
        failed=list(result.failed) or None,
    )
    _print_app_warnings(
        {name: list(warnings) for name, warnings in result.warnings.items()}
    )
    return 0 if not result.failed else 6


def cmd_to(args) -> int:
    cfg = load_config()
    apps = _resolve_app_list(args, cfg)
    if not apps:
        return 2
    cfg.dir.mkdir(parents=True, exist_ok=True)

    ui.banner(
        "dotsync apply",
        f"{len(apps)} app{'s' if len(apps) != 1 else ''}  ←  {cfg.dir}",
    )
    print()
    service = _sync_service(cfg, events=_CliSyncEvents())
    preview = service.preview(direction="apply", apps=tuple(apps))
    plans = list(preview.plans)
    _print_preview(plans, direction="to")
    if not _confirm_or_abort(args, plans, direction="to"):
        return 0
    result = service.execute(preview.digest)
    ui.summary(
        ok=len(result.changed) + len(result.unchanged),
        error=len(result.failed),
        duration_ms=result.duration_ms,
        changed=list(result.changed) or None,
        unchanged=list(result.unchanged) or None,
        failed=list(result.failed) or None,
    )
    _print_app_warnings(
        {name: list(warnings) for name, warnings in result.warnings.items()}
    )
    return 0 if not result.failed else 6


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    argv = _normalize_legacy_command(argv)
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
        if args.cmd == "backup":
            return cmd_from(args)
        if args.cmd == "apply":
            return cmd_to(args)
        if args.cmd == "ui":
            if args.check:
                check_ui_installation()
                return 0
            if args.native_host:
                try:
                    return run_native_ui()
                except Exception:
                    print("dotsync: native UI failed.", file=sys.stderr)
                    return 7
            try:
                return run_browser_ui(open_browser=not args.no_open)
            except KeyboardInterrupt:
                return 130
            except Exception:
                print("dotsync: browser UI failed.", file=sys.stderr)
                return 7
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
