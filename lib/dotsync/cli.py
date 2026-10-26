"""dotsync CLI — the commands dotsync.app runs. Each one takes `--json` and
answers with one JSON object on stdout (see cli_json)."""

from __future__ import annotations
import argparse
import contextlib
import json
import signal
import sys
from pathlib import Path
from typing import Sequence
from dotsync import __version__, accounts, cli_json, cli_sessions, locks
from dotsync.apps import APP_CLASSES, APP_NAMES
from dotsync.config import (
    Config,
    ConfigError,
    folder_config_path,
    load_config,
    read_folder_config,
    save_config,
)
from dotsync.shellrc import (
    ShellRcResult,
    export_line,
    rc_candidates,
    update_shell_rc,
)

_JSON_HELP = "print one JSON object (for the dotsync app)"

# What a command run without --json gets: dotsync has no terminal output.
APP_ONLY = "dotsync is run by dotsync.app: its commands answer only with --json"


def _add_sync_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("app", nargs="?", help="app name or omit with --all")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="answer what would change, without modifying anything",
    )
    parser.add_argument("--yes", action="store_true", help="apply the changes")
    parser.add_argument("--json", action="store_true", help=_JSON_HELP)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dotsync", description="Sync app configs with a folder, for dotsync.app."
    )
    p.add_argument("--version", action="version", version=f"dotsync {__version__}")
    sub = p.add_subparsers(dest="cmd")

    cfg = sub.add_parser("config", help="set the sync folder or the tracked apps")
    cfg_sub = cfg.add_subparsers(dest="cfg_cmd", required=True)
    cfg_dir = cfg_sub.add_parser("dir", help="set sync dir")
    cfg_dir.add_argument("path")
    cfg_apps = cfg_sub.add_parser("apps", help="set tracked apps")
    cfg_apps.add_argument("apps", help="comma-separated names")
    for cfg_cmd in (cfg_dir, cfg_apps):
        cfg_cmd.add_argument("--json", action="store_true", help=_JSON_HELP)

    status = sub.add_parser("status", help="report sync state")
    status.add_argument("--json", action="store_true", help=_JSON_HELP)

    push = sub.add_parser("push", help="local app configs → sync folder")
    _add_sync_args(push)

    pull = sub.add_parser("pull", help="sync folder → local app configs")
    _add_sync_args(pull)

    account = sub.add_parser("account", help="save Claude Code logins and switch between them")
    account_sub = account.add_subparsers(dest="account_cmd", required=True)
    account_login = account_sub.add_parser(
        "login", help="save the claude.ai account the browser approves under a name"
    )
    account_login.add_argument("name")
    account_use = account_sub.add_parser("use", help="make Claude use a saved account")
    account_use.add_argument("name")
    account_use.add_argument(
        "--yes", action="store_true", help="drop an unsaved login in use"
    )
    account_remove = account_sub.add_parser("remove", help="log out a saved account and delete it")
    account_remove.add_argument("name")
    account_rename = account_sub.add_parser("rename", help="change the name shown for an account")
    account_rename.add_argument("name")
    account_rename.add_argument("label")
    account_usage = account_sub.add_parser(
        "usage", help="each account's 5-hour and weekly usage (no tokens spent)"
    )
    for account_cmd in (account_login, account_use, account_remove, account_rename, account_usage):
        account_cmd.add_argument("--json", action="store_true", help=_JSON_HELP)

    cli_sessions.add_parser(sub)

    return p


def cmd_config(args) -> int:
    if args.cfg_cmd == "dir":
        return cli_json.emit(lambda: cli_json.config_dir_json(*_set_dir(args.path)))
    return cli_json.emit(lambda: _apps_payload(args.apps))


def _set_dir(path: str) -> "tuple[Path, ShellRcResult]":
    """Point dotsync at the folder at `path` (files are not moved) through the
    marked DOTSYNC_DIR line, so the next command — from a new shell or the
    dotsync app — finds it. A folder that already has a dotsync.toml keeps
    its own apps and options (a broken one is refused); any other gets this
    folder's config, or one that tracks nothing when there is none to read."""
    new_dir = Path(path).expanduser().resolve()
    if folder_config_path(new_dir).exists():
        read_folder_config(new_dir)
    else:
        try:
            cfg = load_config()
        except ConfigError:
            cfg = Config(dir=new_dir, apps=[])
        new_dir.mkdir(parents=True, exist_ok=True)
        cfg.dir = new_dir
        save_config(cfg)
    return new_dir, _point_rc_at(new_dir)


def _point_rc_at(new_dir: Path) -> ShellRcResult:
    """Update the first existing rc file; never create one."""
    for rc_path in rc_candidates():
        if rc_path.exists():
            return update_shell_rc(rc_path, new_dir)
    return ShellRcResult("rc_missing", None, export_line(new_dir))


def _apps_payload(names: str) -> dict:
    """`config apps --json` sets which apps are tracked. The dotsync app sends
    them in its table's order, so the ones already tracked keep their order
    in dotsync.toml and new ones go at the end. An app turned on gets its
    options (BetterTouchTool's preset names)."""
    cfg = load_config()
    new_apps = [a.strip() for a in names.split(",") if a.strip()]
    bad = [a for a in new_apps if a not in APP_NAMES]
    if bad:
        raise cli_json.JSONCommandError("unknown_apps", f"unknown apps: {bad}")
    new_apps = [a for a in cfg.apps if a in new_apps] + [a for a in new_apps if a not in cfg.apps]
    for app_cls in APP_CLASSES:
        if app_cls.name in new_apps and app_cls.name not in cfg.apps:
            options = app_cls.options_when_turned_on()
            if options is not None:
                cfg.app_options[app_cls.name] = options
    cfg.apps = new_apps
    save_config(cfg)
    return {"dir": str(cfg.dir), "dir_source": cfg.dir_source, "apps": new_apps}


def _sync_locks(apps: list[str], *, writes: bool) -> contextlib.ExitStack:
    """The locks a sync command over `apps` holds while it plans (writes=False)
    or applies (writes=True), shared with the other dotsync commands:
    accounts.locked() when it may write ~/.claude.json (applying a push or
    pull of `claude`, like `dotsync account use`), and the Codex lock, shared,
    whenever it runs the codex CLI."""
    stack = contextlib.ExitStack()
    try:
        if writes and "claude" in apps:
            stack.enter_context(accounts.locked())
        if "codex" in apps:
            stack.enter_context(locks.tool_lock("codex", shared=True))
    except BaseException:
        stack.close()
        raise
    return stack


def cmd_status(args) -> int:
    return cli_json.emit(_status_payload)


def _status_payload() -> dict:
    cfg = load_config()
    with _sync_locks(cfg.apps, writes=False):
        return cli_json.status_json(cfg)


def _sync_payload(args, direction: str) -> dict:
    if not (args.dry_run or args.yes):
        raise cli_json.JSONCommandError(
            "needs_yes_or_dry_run", "pass --dry-run to preview or --yes to apply"
        )
    cfg = load_config()
    apps = cli_json.sync_apps(cfg, args.app, args.all)
    # A preview only reads, like status: it must not wait behind `account usage`.
    with _sync_locks(apps, writes=not args.dry_run):
        if args.dry_run:
            return cli_json.preview_json(cfg, apps, direction)
        return cli_json.apply_json(cfg, apps, direction)


def cmd_from(args) -> int:
    return cli_json.emit(lambda: _sync_payload(args, "from"))


def cmd_to(args) -> int:
    return cli_json.emit(lambda: _sync_payload(args, "to"))


def cmd_account(args) -> int:
    """Every account command changes logins or runs Claude, so it holds the
    accounts lock."""
    if args.account_cmd == "login":
        # The dotsync app cancels a login with SIGTERM; handle it like
        # Ctrl-C so the login stops and cleans up instead of dying mid-way.
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        with accounts.locked():
            result = _account_result(args)
    except accounts.AccountError as e:
        error = {"code": e.code, "message": str(e)}
        if isinstance(e, accounts.UnsavedLoginError):
            error["email"] = e.email
        print(json.dumps({"error": error}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:  # SIGTERM during `login`
        print(json.dumps({"error": {"code": "cancelled", "message": "the command was cancelled"}}))
        return 1
    except Exception as e:  # the dotsync app reads every answer as JSON
        print(json.dumps({"error": {"code": "failed", "message": str(e)}}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _account_result(args) -> dict:
    if args.account_cmd == "login":
        accounts.login(args.name)
        return accounts.account_info(args.name)
    if args.account_cmd == "use":
        accounts.use(args.name, allow_unsaved_overwrite=args.yes)
        return accounts.account_info(args.name)
    if args.account_cmd == "remove":
        accounts.remove(args.name)
        return {"removed": args.name}
    if args.account_cmd == "rename":
        accounts.rename(args.name, args.label)
        return accounts.account_info(args.name)
    if args.account_cmd == "usage":
        return accounts.usage()
    raise AssertionError(f"unknown account command {args.account_cmd}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not getattr(args, "json", False):
        print(APP_ONLY, file=sys.stderr)
        return 2
    if args.cmd == "config":
        return cmd_config(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "push":
        return cmd_from(args)
    if args.cmd == "pull":
        return cmd_to(args)
    if args.cmd == "account":
        return cmd_account(args)
    return cli_sessions.run(args)


if __name__ == "__main__":
    sys.exit(main())
