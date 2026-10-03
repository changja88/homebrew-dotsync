"""Codex CLI sync — user-authored settings, instructions, rules, skills, and the
plugins the user installed (recorded in plugins.json, reinstalled on pull)."""

from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Callable
from dotsync import ui
from dotsync.apps.base import (
    App,
    AppStatus,
    _hash,
    copy_file_safely,
    ensure_directory,
    ensure_not_symlink,
    ensure_path_within_root,
    is_npx_skills_link,
    read_skills_ignore,
    write_text_safely,
)
from dotsync.apps.codex_plugins import (
    build_manifest,
    manifest_text,
    merge_plugin_tables,
    parse_manifest,
    plugin_id,
    split_plugin_tables,
)
from dotsync.apps.mcp_sanitizer import sanitize_codex_config, sanitize_codex_config_text
from dotsync.diffinfo import summarize_texts
from dotsync.plan import (
    AppPlan,
    Change,
    TreeScan,
    blocked_by_symlink,
    diff_trees,
    plan_file_copy,
    plan_tree_mirror,
    scan_tree,
)

OPTIONAL_FILES = (
    "AGENTS.md",
    "AGENTS.override.md",
    "hooks.json",
    "requirements.toml",
)
OPTIONAL_DIRECTORIES = ("rules", "skills")
SKILL_IGNORED_TOP_DIRS = (".system",)
MANIFEST = "plugins.json"
_EMPTY_MANIFEST = {"marketplaces": [], "plugins": []}


class CodexApp(App):
    name = "codex"
    description = (
        "Codex CLI settings (config + global instructions + user rules/skills)"
    )

    def __init__(
        self, skills_ignore: tuple[str, ...] = (), skills_app_tracked: bool = False
    ) -> None:
        super().__init__()
        self.skills_ignore = tuple(skills_ignore)
        # The skills app records `npx skills` links, so skipping them is expected.
        self.skills_app_tracked = skills_app_tracked

    @classmethod
    def from_config(cls, cfg) -> "CodexApp":
        return cls(
            skills_ignore=read_skills_ignore(cfg, cls.name),
            skills_app_tracked="skills" in cfg.apps,
        )

    @classmethod
    def is_present_locally(cls) -> bool:
        return cls._config_path().exists()

    @classmethod
    def _codex_dir(cls) -> Path:
        return Path.home() / ".codex"

    @classmethod
    def _config_path(cls) -> Path:
        return cls._codex_dir() / "config.toml"

    @classmethod
    def _codex_roots(cls) -> tuple[Path, ...]:
        """Directories Codex keeps its own bundled marketplaces under."""
        return (cls._codex_dir(), Path.home() / ".cache" / "codex-runtimes")

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        ui.warn(message)

    def _ignored_top_dirs(self, name: str) -> tuple[str, ...]:
        if name != "skills":
            return ()
        return SKILL_IGNORED_TOP_DIRS + self.skills_ignore

    @staticmethod
    def _is_ignored_rel(rel: Path, ignored_top_dirs: tuple[str, ...]) -> bool:
        return bool(rel.parts and rel.parts[0] in ignored_top_dirs)

    def _scan(self, root: Path, ignored_top_dirs: tuple[str, ...] = ()) -> TreeScan:
        try:
            return scan_tree(root, ignored_top_dirs)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    def _diff_tree(
        self,
        local: Path,
        stored: Path,
        ignored_top_dirs: tuple[str, ...] = (),
    ) -> tuple[set[Path], set[Path], set[Path]]:
        """Return (added_in_stored, removed_in_stored, modified) relative paths."""
        try:
            diff = diff_trees(local, stored, ignored_top_dirs)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        return set(diff.removes), set(diff.creates), set(diff.updates)

    def _mirror_tree(
        self,
        src: Path,
        dst: Path,
        ignored_top_dirs: tuple[str, ...] = (),
        *,
        purge_ignored_dst: bool = False,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> None:
        """Mirror managed files from src to dst; ignored trees and symlinks stay."""
        ensure_directory(src, str(src), root=source_root)
        ensure_directory(dst, str(dst), root=dest_root)
        dst.mkdir(parents=True, exist_ok=True)
        src_scan = self._scan(src, ignored_top_dirs)
        dst_scan = self._scan(dst, ignored_top_dirs)
        blocked = blocked_by_symlink(src_scan.files, dst_scan.symlinks)
        kept = blocked_by_symlink(dst_scan.files, src_scan.symlinks)
        for rel in sorted(src_scan.symlinks | dst_scan.symlinks):
            if self.skills_app_tracked and (
                is_npx_skills_link(src / rel) or is_npx_skills_link(dst / rel)
            ):
                continue
            self._note_skipped_symlink(f"{src.name}/{rel.as_posix()}")

        for rel in sorted(src_scan.files - set(blocked)):
            target = dst / rel
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_file_safely(
                src / rel,
                target,
                str(rel),
                source_root=source_root,
                dest_root=dest_root,
            )

        for rel in dst_scan.files - src_scan.files - set(kept):
            target = dst / rel
            if target.exists() or target.is_symlink():
                target.unlink()

        if purge_ignored_dst:
            for name in ignored_top_dirs:
                ignored = dst / name
                if ignored.is_symlink():
                    ignored.unlink()
                elif ignored.is_dir():
                    shutil.rmtree(ignored)
                elif ignored.exists():
                    ignored.unlink()

        subdirs = sorted(
            (d for d in dst.rglob("*") if d.is_dir() and not d.is_symlink()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for d in subdirs:
            rel = d.relative_to(dst)
            if self._is_ignored_rel(rel, ignored_top_dirs):
                continue
            try:
                d.rmdir()
            except OSError:
                pass

    def _remove_managed_path(
        self, path: Path, label: str, *, root: Path | None = None
    ) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink():
            if root is not None:
                ensure_path_within_root(path.parent, root, label)
            path.unlink()
        elif path.is_dir():
            if root is not None:
                ensure_path_within_root(path, root, label)
            shutil.rmtree(path)
        else:
            if root is not None:
                ensure_path_within_root(path, root, label)
            path.unlink()

    @staticmethod
    def _merge_statuses(statuses: list[AppStatus]) -> AppStatus:
        if any(s.state == "missing" for s in statuses):
            missing = [
                s.details for s in statuses if s.state == "missing" and s.details
            ]
            return AppStatus(state="missing", details=", ".join(missing))
        dirty = [s for s in statuses if s.state == "dirty"]
        if dirty:
            details = ", ".join(s.details for s in dirty if s.details)
            return AppStatus(state="dirty", details=details)
        return AppStatus(state="clean")

    def _plan_tree_mirror(
        self,
        label: str,
        source: Path,
        dest: Path,
        ignored_top_dirs: tuple[str, ...] = (),
        *,
        purge_ignored_dst: bool = False,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> Change:
        change = plan_tree_mirror(
            label,
            source,
            dest,
            ignored_top_dirs,
            source_root=source_root,
            dest_root=dest_root,
        )
        details = [change.details] if change.details else []
        kind = change.kind

        if source.exists() and not dest.exists() and kind == "unchanged":
            kind = "create"
            details.append("create directory")

        purged = [
            name
            for name in ignored_top_dirs
            if purge_ignored_dst
            and ((dest / name).exists() or (dest / name).is_symlink())
        ]
        if purged:
            if kind == "unchanged":
                kind = "update"
            details.append(f"purge ignored {', '.join(purged)}")

        original_details = [change.details] if change.details else []
        if kind == change.kind and details == original_details:
            return change
        return Change(
            label=change.label,
            kind=kind,
            source=change.source,
            dest=change.dest,
            details=", ".join(details),
        )

    def _read_portable_config(self, path: Path) -> str:
        """config.toml as stored: no dynamic Serena URL and no marketplace or
        plugin tables, which Codex rewrites itself (see codex_plugins)."""
        ensure_not_symlink(path, "config.toml")
        settings, _ = split_plugin_tables(sanitize_codex_config_text(path.read_text()))
        return settings

    def _applied_config(self, stored_config: Path) -> str:
        """Stored settings plus the marketplace/plugin tables Codex wrote locally.

        When the settings already match and there is no stale Serena URL to
        drop, the local file is kept byte for byte, so a pull does not reorder
        tables Codex placed mid-file."""
        local = self._config_path()
        local_text = local.read_text() if local.exists() else ""
        stored_settings = self._read_portable_config(stored_config)
        if (
            local.exists()
            and not sanitize_codex_config(local_text).changed
            and split_plugin_tables(local_text)[0] == stored_settings
        ):
            return local_text
        return merge_plugin_tables(stored_settings, local_text)

    def _plan_config(
        self,
        source: Path,
        dest: Path,
        planned: Callable[[], str],
        *,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> Change:
        safety = plan_file_copy(
            "config.toml",
            source,
            dest,
            source_root=source_root,
            dest_root=dest_root,
        )
        if safety.kind == "unknown":
            return safety
        if not source.exists():
            return Change("config.toml", "missing-source", source, dest)
        text = planned()
        if not dest.exists():
            return Change("config.toml", "create", source, dest, diffable=False)
        current = dest.read_text()
        if text == current:
            return Change("config.toml", "unchanged", source, dest)
        return Change(
            "config.toml",
            "update",
            source,
            dest,
            summarize_texts(current, text, ".toml"),
            diffable=False,
        )

    def _config_status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        try:
            ensure_directory(stored, "codex/", root=target_dir)
        except RuntimeError as exc:
            return AppStatus(state="unknown", details=str(exc))
        local = self._config_path()
        dest = stored / "config.toml"
        if not local.exists() or not dest.exists():
            return AppStatus(state="missing", details="config.toml")
        try:
            if self._read_portable_config(local) != self._read_portable_config(dest):
                return AppStatus(state="dirty", details="config.toml")
        except RuntimeError as exc:
            return AppStatus(state="unknown", details=str(exc))
        return AppStatus(state="clean")

    def _validate_sync_to_paths(self, stored: Path, target_dir: Path) -> None:
        ensure_directory(stored, "codex/", root=target_dir)
        stored_config = stored / "config.toml"
        ensure_path_within_root(stored_config, target_dir, "config.toml")
        ensure_not_symlink(stored_config, "config.toml")
        if not stored_config.is_file():
            raise FileNotFoundError(
                f"{stored_config} not found (codex/config.toml missing)"
            )

        local_dir = self._codex_dir()
        for name in OPTIONAL_FILES:
            stored_file = stored / name
            if stored_file.exists() or stored_file.is_symlink():
                ensure_path_within_root(stored_file, target_dir, name)
                ensure_not_symlink(stored_file, name)
                if not stored_file.is_file():
                    raise RuntimeError(f"{stored_file} is not a file ({name})")
                local_file = local_dir / name
                if local_file.exists() or local_file.is_symlink():
                    ensure_not_symlink(local_file, name)
                    if not local_file.is_file():
                        raise RuntimeError(f"{local_file} is not a file ({name})")

        for name in OPTIONAL_DIRECTORIES:
            stored_dir = stored / name
            if stored_dir.exists() or stored_dir.is_symlink():
                ignored = self._ignored_top_dirs(name)
                ensure_directory(stored_dir, f"{name}/", root=target_dir)
                self._scan(stored_dir, ignored)
                local_dir_for_name = local_dir / name
                if local_dir_for_name.exists() or local_dir_for_name.is_symlink():
                    ensure_directory(local_dir_for_name, f"{name}/")
                    self._scan(local_dir_for_name, ignored)

    def _optional_file_status(self, stored: Path) -> AppStatus | None:
        optional_file_changes: list[str] = []
        for name in OPTIONAL_FILES:
            local_file = self._codex_dir() / name
            stored_file = stored / name
            if local_file.is_symlink() or stored_file.is_symlink():
                return AppStatus(state="unknown", details=f"{name} is a symlink")
            if local_file.exists() and stored_file.exists():
                if _hash(local_file) != _hash(stored_file):
                    optional_file_changes.append(name)
            elif local_file.exists() or stored_file.exists():
                optional_file_changes.append(name)
        if optional_file_changes:
            return AppStatus(state="dirty", details=", ".join(optional_file_changes))
        return None

    def _run_codex_cli(self, args: list[str], desc: str, *, quiet: bool = False):
        try:
            result = self._run_external(["codex", *args], desc=desc, fail_mode="warn")
        except FileNotFoundError:
            self._warn(f"{desc} skipped: `codex` CLI not installed")
            return None
        if result.returncode == 0 and not quiet:
            ui.ok(desc)
        elif result.returncode != 0:
            ui.warn(f"{desc} failed: {(result.stderr or '').strip() or 'unknown'}")
        return result

    def _codex_json(self, args: list[str], desc: str) -> dict | None:
        result = self._run_codex_cli(args, desc, quiet=True)
        if result is None or result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            self._warn(f"{desc} failed: unexpected JSON output")
            return None
        return payload

    def _installed_entries(self) -> list[dict] | None:
        payload = self._codex_json(["plugin", "list", "--json"], "plugin list")
        if payload is None:
            return None
        installed = payload.get("installed")
        if not isinstance(installed, list) or not all(
            isinstance(item, dict) for item in installed
        ):
            self._warn("plugin list failed: unexpected JSON output")
            return None
        return installed

    def _marketplace_names(self) -> set[str] | None:
        payload = self._codex_json(
            ["plugin", "marketplace", "list", "--json"], "marketplace list"
        )
        if payload is None:
            return None
        listed = payload.get("marketplaces")
        if not isinstance(listed, list):
            self._warn("marketplace list failed: unexpected JSON output")
            return None
        return {item.get("name") for item in listed if isinstance(item, dict)}

    def _local_manifest(self) -> dict | None:
        """What plugins.json should say for this machine, or None (warned)."""
        installed = self._installed_entries()
        if installed is None:
            return None
        try:
            return build_manifest(
                installed, self._config_path().read_text(), self._codex_roots()
            )
        except ValueError as exc:
            self._warn(str(exc))
            return None

    def _read_stored_manifest(self, path: Path) -> dict:
        ensure_not_symlink(path, MANIFEST)
        return parse_manifest(path.read_text())

    def _missing_from(self, manifest: dict) -> tuple[list[dict], list[str]] | None:
        """(marketplaces, plugins) in the manifest that this machine lacks."""
        names = self._marketplace_names()
        installed = self._installed_entries()
        if names is None or installed is None:
            return None
        try:
            present = {plugin_id(item) for item in installed}
        except ValueError as exc:
            self._warn(str(exc))
            return None
        return (
            [mp for mp in manifest["marketplaces"] if mp["name"] not in names],
            [plugin for plugin in manifest["plugins"] if plugin not in present],
        )

    @staticmethod
    def _manifest_diff(old: dict, new: dict) -> str:
        def labels(manifest: dict) -> set[str]:
            return {f"marketplace {mp['name']}" for mp in manifest["marketplaces"]} | set(
                manifest["plugins"]
            )

        before, after = labels(old), labels(new)
        changes = [f"+{label}" for label in sorted(after - before)]
        changes += [f"-{label}" for label in sorted(before - after)]
        return ", ".join(changes) or "marketplace details changed"

    def _plan_manifest_from(self, stored: Path, target_dir: Path) -> Change:
        dest = stored / MANIFEST
        manifest = self._local_manifest()
        if manifest is None:
            return Change(
                MANIFEST, "unknown", None, dest,
                "could not read installed Codex plugins", diffable=False,
            )
        if not dest.exists() and not dest.is_symlink():
            return Change(
                MANIFEST, "create", None, dest,
                self._manifest_diff(_EMPTY_MANIFEST, manifest), diffable=False,
            )
        try:
            ensure_path_within_root(dest, target_dir, MANIFEST)
            stored_manifest = self._read_stored_manifest(dest)
        except (RuntimeError, ValueError):
            stored_manifest = None
        if stored_manifest == manifest:
            return Change(MANIFEST, "unchanged", None, dest, diffable=False)
        return Change(
            MANIFEST, "update", None, dest,
            self._manifest_diff(stored_manifest or _EMPTY_MANIFEST, manifest),
            diffable=False,
        )

    def _plan_plugin_restore(self, stored: Path) -> Change | None:
        path = stored / MANIFEST
        if not path.exists() and not path.is_symlink():
            return None
        try:
            manifest = self._read_stored_manifest(path)
        except (RuntimeError, ValueError) as exc:
            return Change(MANIFEST, "unknown", path, None, str(exc), diffable=False)
        missing = self._missing_from(manifest)
        if missing is None:
            return Change(
                MANIFEST, "unknown", path, None,
                "could not read installed Codex plugins", diffable=False,
            )
        marketplaces, plugins = missing
        items = [f"marketplace {mp['name']}" for mp in marketplaces] + plugins
        if not items:
            return Change(MANIFEST, "unchanged", path, None, diffable=False)
        return Change(
            MANIFEST, "update", path, None, "install " + ", ".join(items), diffable=False
        )

    def _manifest_status(self, stored: Path) -> AppStatus:
        manifest = self._local_manifest()
        if manifest is None:
            return AppStatus(
                state="unknown", details="plugins.json: could not read installed Codex plugins"
            )
        path = stored / MANIFEST
        if not path.exists() and not path.is_symlink():
            stored_manifest = _EMPTY_MANIFEST
        else:
            try:
                stored_manifest = self._read_stored_manifest(path)
            except (RuntimeError, ValueError):
                stored_manifest = None
        if stored_manifest == manifest:
            return AppStatus(state="clean")
        return AppStatus(state="dirty", details=MANIFEST)

    def _add_marketplace(self, marketplace: dict) -> bool:
        name = marketplace["name"]
        cmd = ["plugin", "marketplace", "add", marketplace["source"]]
        if marketplace.get("ref"):
            cmd += ["--ref", marketplace["ref"]]
        for sparse in marketplace.get("sparse", []):
            cmd += ["--sparse", sparse]
        result = self._run_codex_cli(cmd + ["--json"], desc=f"marketplace add {name}")
        if result is None or result.returncode != 0:
            return False
        names = self._marketplace_names()
        if names is None or name not in names:
            self._warn(f"marketplace {name} not found after add")
            return False
        return True

    def _restore_plugins(self, stored: Path) -> None:
        """Add recorded marketplaces and install recorded plugins this machine
        lacks; what is already there is left alone, so pull is repeatable."""
        path = stored / MANIFEST
        if not path.exists() and not path.is_symlink():
            return
        try:
            manifest = self._read_stored_manifest(path)
        except (RuntimeError, ValueError) as exc:
            self._warn(f"plugins restore skipped: {exc}")
            return
        if manifest == _EMPTY_MANIFEST:
            return
        missing = self._missing_from(manifest)
        if missing is None:
            self._warn("plugins restore skipped: could not read installed Codex plugins")
            return
        marketplaces, plugins = missing
        failed = {mp["name"] for mp in marketplaces if not self._add_marketplace(mp)}
        for plugin in manifest["plugins"]:
            if plugin not in plugins:
                ui.sub(f"plugin add {plugin} (already installed)")
        for plugin in plugins:
            marketplace = plugin.split("@", 1)[1]
            if marketplace in failed:
                self._warn(
                    f"plugin add {plugin} skipped: marketplace {marketplace} unavailable"
                )
                continue
            self._run_codex_cli(["plugin", "add", plugin, "--json"], desc=f"plugin add {plugin}")

    def plan_from(self, target_dir: Path) -> AppPlan:
        stored = self._stored(target_dir)
        local_config = self._config_path()
        changes = [
            self._plan_config(
                local_config,
                stored / "config.toml",
                lambda: self._read_portable_config(local_config),
                dest_root=target_dir,
            )
        ]
        for name in OPTIONAL_FILES:
            local_file = self._codex_dir() / name
            if local_file.exists():
                changes.append(
                    plan_file_copy(
                        name, local_file, stored / name, dest_root=target_dir
                    )
                )
            elif (stored / name).exists() or (stored / name).is_symlink():
                changes.append(
                    Change(
                        name,
                        "remove",
                        None,
                        stored / name,
                        "local file missing",
                    )
                )
        for name in OPTIONAL_DIRECTORIES:
            local_dir = self._codex_dir() / name
            if local_dir.exists():
                ignored = self._ignored_top_dirs(name)
                changes.append(
                    self._plan_tree_mirror(
                        f"{name}/",
                        local_dir,
                        stored / name,
                        ignored,
                        purge_ignored_dst=bool(ignored),
                        dest_root=target_dir,
                    )
                )
            elif (stored / name).exists() or (stored / name).is_symlink():
                changes.append(
                    Change(
                        f"{name}/",
                        "remove",
                        None,
                        stored / name,
                        "local directory missing",
                    )
                )
        if local_config.exists():
            changes.append(self._plan_manifest_from(stored, target_dir))
        return AppPlan(self.name, "from", changes, self.description)

    def plan_to(self, target_dir: Path) -> AppPlan:
        stored = self._stored(target_dir)
        local_dir = self._codex_dir()
        stored_config = stored / "config.toml"
        changes = [
            self._plan_config(
                stored_config,
                self._config_path(),
                lambda: self._applied_config(stored_config),
                source_root=target_dir,
            )
        ]
        for name in OPTIONAL_FILES:
            stored_file = stored / name
            if stored_file.exists():
                changes.append(
                    plan_file_copy(
                        name, stored_file, local_dir / name, source_root=target_dir
                    )
                )
        for name in OPTIONAL_DIRECTORIES:
            stored_dir = stored / name
            if stored_dir.exists():
                changes.append(
                    self._plan_tree_mirror(
                        f"{name}/",
                        stored_dir,
                        local_dir / name,
                        self._ignored_top_dirs(name),
                        source_root=target_dir,
                    )
                )
        plugin_restore = self._plan_plugin_restore(stored)
        if plugin_restore is not None:
            changes.append(plugin_restore)
        return AppPlan(self.name, "to", changes, self.description)

    def sync_from(self, target_dir: Path) -> None:
        stored = self._stored(target_dir)
        local_config = self._config_path()
        if not local_config.exists():
            raise FileNotFoundError(f"{local_config} not found (config.toml missing)")

        ensure_directory(stored, "codex/", root=target_dir)
        stored.mkdir(parents=True, exist_ok=True)
        write_text_safely(
            stored / "config.toml",
            self._read_portable_config(local_config),
            "config.toml",
            dest_root=target_dir,
        )
        ui.sub("config.toml")

        manifest = self._local_manifest()
        if manifest is None:
            self._warn(f"{MANIFEST} not updated: could not read installed Codex plugins")
        else:
            write_text_safely(
                stored / MANIFEST, manifest_text(manifest), MANIFEST, dest_root=target_dir
            )
            ui.sub(MANIFEST)

        for name in OPTIONAL_FILES:
            local_file = self._codex_dir() / name
            if local_file.exists():
                copy_file_safely(local_file, stored / name, name, dest_root=target_dir)
                ui.sub(name)
            else:
                stale_file = stored / name
                if stale_file.exists() or stale_file.is_symlink():
                    self._remove_managed_path(stale_file, name, root=target_dir)
                    ui.sub(f"{name} removed")

        for name in OPTIONAL_DIRECTORIES:
            local_dir = self._codex_dir() / name
            if local_dir.exists():
                ignored = self._ignored_top_dirs(name)
                self._mirror_tree(
                    local_dir,
                    stored / name,
                    ignored,
                    purge_ignored_dst=bool(ignored),
                    dest_root=target_dir,
                )
                ui.sub(f"{name}/")
            else:
                stale_dir = stored / name
                if stale_dir.exists() or stale_dir.is_symlink():
                    self._remove_managed_path(stale_dir, f"{name}/", root=target_dir)
                    ui.sub(f"{name}/ removed")

    def sync_to(self, target_dir: Path) -> None:
        stored = self._stored(target_dir)
        stored_config = stored / "config.toml"
        self._validate_sync_to_paths(stored, target_dir)

        local_dir = self._codex_dir()
        local_dir.mkdir(parents=True, exist_ok=True)

        local_config = self._config_path()
        applied = self._applied_config(stored_config)
        write_text_safely(local_config, applied, "config.toml")
        ui.sub("config.toml")

        for name in OPTIONAL_FILES:
            stored_file = stored / name
            if not stored_file.exists():
                continue
            local_file = local_dir / name
            copy_file_safely(stored_file, local_file, name, source_root=target_dir)
            ui.sub(name)

        for name in OPTIONAL_DIRECTORIES:
            stored_dir = stored / name
            if not stored_dir.exists():
                continue
            ensure_directory(stored_dir, f"{name}/", root=target_dir)
            local_dir_for_name = local_dir / name
            self._mirror_tree(
                stored_dir,
                local_dir_for_name,
                self._ignored_top_dirs(name),
                source_root=target_dir,
            )
            ui.sub(f"{name}/")

        self._restore_plugins(stored)

    def status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        base = self._config_status(target_dir)
        if base.state == "missing":
            return base
        if base.state == "unknown":
            return base
        statuses = [base]

        optional_files = self._optional_file_status(stored)
        if optional_files is not None:
            if optional_files.state == "unknown":
                return optional_files
            statuses.append(optional_files)

        flat_paths: list[str] = []
        summary_parts: list[tuple[str, int]] = []
        for name in OPTIONAL_DIRECTORIES:
            local_dir = self._codex_dir() / name
            stored_dir = stored / name
            try:
                added, removed, modified = self._diff_tree(
                    local_dir,
                    stored_dir,
                    self._ignored_top_dirs(name),
                )
            except RuntimeError as exc:
                return AppStatus(state="unknown", details=str(exc))
            count = len(added) + len(removed) + len(modified)
            if count > 0:
                for rel in sorted(added | removed | modified):
                    flat_paths.append(f"{name}/{rel}")
                summary_parts.append((f"{name}/", count))

        if flat_paths:
            details = (
                ", ".join(flat_paths)
                if len(flat_paths) <= 8
                else ", ".join(f"{label} ({n} changed)" for label, n in summary_parts)
            )
            statuses.append(AppStatus(state="dirty", details=details))

        manifest = self._manifest_status(stored)
        if manifest.state == "unknown":
            return manifest
        statuses.append(manifest)
        return self._merge_statuses(statuses)
