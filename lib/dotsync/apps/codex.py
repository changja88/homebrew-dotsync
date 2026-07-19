"""Codex CLI sync — user-authored settings, instructions, rules, and skills."""

from __future__ import annotations
import json
import shutil
import tomllib
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import (
    App,
    AppStatus,
    _hash,
    copy_file_safely,
    ensure_directory,
    ensure_not_symlink,
    ensure_path_within_root,
)
from dotsync.apps.mcp_sanitizer import sanitize_codex_config, sanitize_codex_config_text
from dotsync.diffinfo import summarize_pair
from dotsync.plan import AppPlan, Change, plan_file_copy, plan_tree_mirror

OPTIONAL_FILES = (
    "AGENTS.md",
    "AGENTS.override.md",
    "hooks.json",
    "requirements.toml",
    "plugins.toml",
)
OPTIONAL_DIRECTORIES = ("rules", "skills")
SKILL_IGNORED_TOP_DIRS = (".system",)


class CodexApp(App):
    name = "codex"
    description = (
        "Codex CLI settings (config + global instructions + user rules/skills)"
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

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    @staticmethod
    def _ignored_top_dirs(name: str) -> tuple[str, ...]:
        return SKILL_IGNORED_TOP_DIRS if name == "skills" else ()

    @staticmethod
    def _is_ignored_rel(rel: Path, ignored_top_dirs: tuple[str, ...]) -> bool:
        return bool(rel.parts and rel.parts[0] in ignored_top_dirs)

    def _tree_files(
        self, root: Path, ignored_top_dirs: tuple[str, ...] = ()
    ) -> set[Path]:
        if not root.exists():
            return set()
        ensure_not_symlink(root, str(root))
        if not root.is_dir():
            raise RuntimeError(f"{root} is not a directory")
        files: set[Path] = set()
        for f in root.rglob("*"):
            rel = f.relative_to(root)
            if self._is_ignored_rel(rel, ignored_top_dirs):
                continue
            ensure_not_symlink(f, str(rel))
            if f.is_file():
                files.add(rel)
        return files

    def _diff_tree(
        self,
        local: Path,
        stored: Path,
        ignored_top_dirs: tuple[str, ...] = (),
    ) -> tuple[set[Path], set[Path], set[Path]]:
        """Return (added_in_stored, removed_in_stored, modified) relative paths."""
        local_files = self._tree_files(local, ignored_top_dirs)
        stored_files = self._tree_files(stored, ignored_top_dirs)
        added = stored_files - local_files
        removed = local_files - stored_files
        common = local_files & stored_files
        modified = {rel for rel in common if _hash(local / rel) != _hash(stored / rel)}
        return added, removed, modified

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
        """Mirror managed files from src to dst, preserving ignored dst trees."""
        ensure_directory(src, str(src), root=source_root)
        ensure_directory(dst, str(dst), root=dest_root)
        dst.mkdir(parents=True, exist_ok=True)
        src_rels = self._tree_files(src, ignored_top_dirs)
        dst_rels = self._tree_files(dst, ignored_top_dirs)

        for rel in src_rels:
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

        for rel in dst_rels - src_rels:
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
            (d for d in dst.rglob("*") if d.is_dir()),
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

    def _backup_file(self, local: Path, backup_dir: Path, label: str) -> None:
        if not local.exists():
            return
        bdst = backup_dir / self.name / label
        ensure_path_within_root(bdst, backup_dir, label)
        bdst.parent.mkdir(parents=True, exist_ok=True)
        copy_file_safely(local, bdst, label, dest_root=backup_dir)
        ui.dim(f"backup → {bdst}")

    def _backup_tree(self, local: Path, backup_dir: Path, label: str) -> None:
        if not local.exists():
            return
        bdst = backup_dir / self.name / label
        self._mirror_tree(local, bdst, dest_root=backup_dir)
        ui.dim(f"backup → {bdst}")

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

    def _read_sanitized_config(self, path: Path) -> str:
        ensure_not_symlink(path, "config.toml")
        return sanitize_codex_config_text(path.read_text())

    def _write_sanitized_config(
        self,
        source: Path,
        dest: Path,
        *,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> None:
        if source_root is not None:
            ensure_path_within_root(source, source_root, "config.toml")
        if dest_root is not None:
            ensure_path_within_root(dest, dest_root, "config.toml")
        ensure_not_symlink(dest, "config.toml")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self._read_sanitized_config(source))

    def _plan_sanitized_config_copy(
        self,
        source: Path,
        dest: Path,
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
        planned = self._read_sanitized_config(source)
        if not dest.exists():
            return Change("config.toml", "create", source, dest)
        current = sanitize_codex_config(dest.read_text())
        if current.changed:
            return Change(
                "config.toml",
                "update",
                source,
                dest,
                "remove dynamic Serena MCP URL",
            )
        kind = "unchanged" if planned == current.text else "update"
        return Change(
            "config.toml",
            kind,
            source,
            dest,
            summarize_pair(source, dest) if kind == "update" else "",
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
            if self._read_sanitized_config(local) != self._read_sanitized_config(dest):
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
                self._tree_files(stored_dir, ignored)
                local_dir_for_name = local_dir / name
                if local_dir_for_name.exists() or local_dir_for_name.is_symlink():
                    ensure_directory(local_dir_for_name, f"{name}/")
                    self._tree_files(local_dir_for_name, ignored)

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

    def _plan_plugin_restore(self, stored: Path) -> Change | None:
        manifest = stored / "plugins.toml"
        if not manifest.exists() and not manifest.is_symlink():
            return None
        try:
            ensure_path_within_root(manifest, stored.parent, "plugins.toml")
            ensure_not_symlink(manifest, "plugins.toml")
        except RuntimeError as exc:
            return Change("plugins restore", "unknown", manifest, None, str(exc))
        try:
            plugins, marketplaces = self._read_plugin_manifest(manifest)
        except ValueError as exc:
            return Change("plugins restore", "unknown", manifest, None, str(exc))
        details = ", ".join(
            [str(plugin) for plugin in plugins]
            + [f"marketplace {mp['name']}" for mp in marketplaces]
        )
        return Change("plugins restore", "unknown", manifest, None, details)

    def _read_plugin_manifest(
        self, manifest: Path
    ) -> tuple[list[str], list[dict[str, object]]]:
        try:
            data = tomllib.loads(manifest.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid plugins.toml: {exc}") from exc

        allowed_top_keys = {"plugins", "marketplaces"}
        unknown_top_keys = set(data) - allowed_top_keys
        if unknown_top_keys:
            keys = ", ".join(sorted(unknown_top_keys))
            raise ValueError(f"invalid plugins.toml: unknown top-level key {keys}")
        if "marketplaces" in data and "plugins" not in data:
            raise ValueError("invalid plugins.toml: expected top-level plugins list")
        if "plugins" not in data:
            raise ValueError("invalid plugins.toml: expected top-level plugins list")
        plugins = data["plugins"] if "plugins" in data else []
        if not isinstance(plugins, list) or not all(
            isinstance(p, str) for p in plugins
        ):
            raise ValueError(
                "invalid plugins.toml: expected top-level plugins list of strings"
            )
        for plugin in plugins:
            if plugin.count("@") != 1 or any(ch.isspace() for ch in plugin):
                raise ValueError(
                    "invalid plugins.toml: expected plugin selectors as plugin@marketplace"
                )
            plugin_name, marketplace_name = plugin.split("@", 1)
            if not plugin_name or not marketplace_name:
                raise ValueError(
                    "invalid plugins.toml: expected plugin selectors as plugin@marketplace"
                )
        marketplaces = data["marketplaces"] if "marketplaces" in data else []
        if not isinstance(marketplaces, list) or not all(
            isinstance(mp, dict) for mp in marketplaces
        ):
            raise ValueError("invalid plugins.toml: expected marketplaces array")
        for marketplace in marketplaces:
            allowed_marketplace_keys = {"name", "source", "ref", "sparse"}
            unknown_marketplace_keys = set(marketplace) - allowed_marketplace_keys
            if unknown_marketplace_keys:
                keys = ", ".join(sorted(unknown_marketplace_keys))
                raise ValueError(
                    f"invalid plugins.toml: unknown marketplace key {keys}"
                )
            name = marketplace.get("name")
            source = marketplace.get("source")
            ref = marketplace.get("ref")
            sparse = marketplace["sparse"] if "sparse" in marketplace else []
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(source, str)
                or not source.strip()
            ):
                raise ValueError(
                    "invalid plugins.toml: marketplace requires name and source strings"
                )
            if ref is not None and not isinstance(ref, str):
                raise ValueError(
                    "invalid plugins.toml: marketplace ref must be a string"
                )
            if not isinstance(sparse, list) or not all(
                isinstance(p, str) and p.strip() for p in sparse
            ):
                raise ValueError(
                    "invalid plugins.toml: marketplace sparse must be a list of non-empty strings"
                )
        return plugins, marketplaces

    def _run_codex_cli(self, args: list[str], desc: str, *, quiet: bool = False):
        try:
            result = self._run_external(["codex", *args], desc=desc, fail_mode="warn")
        except FileNotFoundError:
            self.warnings.append(f"{desc} skipped: `codex` CLI not installed")
            ui.warn(f"{desc} skipped: `codex` CLI not installed")
            return None
        if result.returncode == 0 and not quiet:
            ui.ok(desc)
        else:
            stderr = (result.stderr or "").strip()
            if result.returncode != 0:
                ui.warn(f"{desc} failed: {stderr or 'unknown'}")
        return result

    def _restore_marketplace(self, marketplace: dict[str, object]) -> str | None:
        name = str(marketplace["name"])
        cmd = ["plugin", "marketplace", "add", str(marketplace["source"])]
        ref = marketplace.get("ref")
        if ref:
            cmd.extend(["--ref", str(ref)])
        for sparse in marketplace.get("sparse") or []:
            cmd.extend(["--sparse", str(sparse)])
        cmd.append("--json")
        add_result = self._run_codex_cli(cmd, desc=f"marketplace add {name}")
        if add_result is None or add_result.returncode != 0:
            return None

        result = self._run_codex_cli(
            ["plugin", "marketplace", "list", "--json"],
            desc="marketplace list",
            quiet=True,
        )
        if result is None or result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            self.warnings.append("marketplace list failed: invalid JSON output")
            ui.warn("marketplace list failed: invalid JSON output")
            return None
        if not isinstance(payload, dict):
            self.warnings.append("marketplace list failed: unexpected JSON output")
            ui.warn("marketplace list failed: unexpected JSON output")
            return None
        marketplaces = payload.get("marketplaces", [])
        if not isinstance(marketplaces, list) or not all(
            isinstance(item, dict) for item in marketplaces
        ):
            self.warnings.append("marketplace list failed: unexpected JSON output")
            ui.warn("marketplace list failed: unexpected JSON output")
            return None
        if not any(
            isinstance(item, dict) and item.get("name") == name for item in marketplaces
        ):
            self.warnings.append(f"marketplace {name} not found after add")
            ui.warn(f"marketplace {name} not found after add")
            return None
        upgrade_result = self._run_codex_cli(
            ["plugin", "marketplace", "upgrade", name],
            desc=f"marketplace upgrade {name}",
        )
        if upgrade_result is None or upgrade_result.returncode != 0:
            return None
        return name

    @staticmethod
    def _plugin_marketplace(plugin: str) -> str | None:
        if "@" not in plugin:
            return None
        return plugin.rsplit("@", 1)[1]

    def _installed_plugins(self) -> set[str] | None:
        result = self._run_codex_cli(
            ["plugin", "list", "--json"],
            desc="plugin list",
            quiet=True,
        )
        if result is None or result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            self.warnings.append("plugin list failed: invalid JSON output")
            ui.warn("plugin list failed: invalid JSON output")
            return None
        if not isinstance(payload, dict):
            self.warnings.append("plugin list failed: unexpected JSON output")
            ui.warn("plugin list failed: unexpected JSON output")
            return None
        if "installed" not in payload:
            self.warnings.append("plugin list failed: unexpected JSON output")
            ui.warn("plugin list failed: unexpected JSON output")
            return None
        installed = payload["installed"]
        if not isinstance(installed, list) or not all(
            isinstance(item, dict) for item in installed
        ):
            self.warnings.append("plugin list failed: unexpected JSON output")
            ui.warn("plugin list failed: unexpected JSON output")
            return None
        plugins: set[str] = set()
        for item in installed:
            plugin_id = item.get("pluginId")
            if "pluginId" in item:
                if not isinstance(plugin_id, str):
                    self.warnings.append("plugin list failed: unexpected JSON output")
                    ui.warn("plugin list failed: unexpected JSON output")
                    return None
                plugins.add(plugin_id)
                continue
            name = item.get("name")
            marketplace = item.get("marketplaceName")
            if not isinstance(name, str) or not isinstance(marketplace, str):
                self.warnings.append("plugin list failed: unexpected JSON output")
                ui.warn("plugin list failed: unexpected JSON output")
                return None
            plugins.add(f"{name}@{marketplace}")
        return plugins

    def _restore_plugins(self, stored: Path) -> None:
        manifest = stored / "plugins.toml"
        if not manifest.exists():
            return
        try:
            plugins, marketplaces = self._read_plugin_manifest(manifest)
        except ValueError as exc:
            self.warnings.append(str(exc))
            ui.warn(str(exc))
            return
        declared_marketplaces = {str(mp["name"]) for mp in marketplaces}
        validated_marketplaces = set()
        for marketplace in marketplaces:
            validated = self._restore_marketplace(marketplace)
            if validated is not None:
                validated_marketplaces.add(validated)
        installed_plugins = self._installed_plugins() if plugins else set()
        if installed_plugins is None:
            warning = "plugin install skipped: installed plugin state unavailable"
            self.warnings.append(warning)
            ui.warn(warning)
            return
        for plugin in plugins:
            marketplace = self._plugin_marketplace(plugin)
            if (
                marketplace in declared_marketplaces
                and marketplace not in validated_marketplaces
            ):
                warning = f"plugin add {plugin} skipped: marketplace {marketplace} unavailable"
                self.warnings.append(warning)
                ui.warn(warning)
                continue
            if plugin in installed_plugins:
                ui.sub(f"plugin add {plugin} (already installed)")
                continue
            self._run_codex_cli(
                ["plugin", "add", plugin, "--json"],
                desc=f"plugin add {plugin}",
            )

    def plan_from(self, target_dir: Path) -> AppPlan:
        stored = self._stored(target_dir)
        changes = [
            self._plan_sanitized_config_copy(
                self._config_path(),
                stored / "config.toml",
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
        return AppPlan(self.name, "from", changes, self.description)

    def plan_to(self, target_dir: Path) -> AppPlan:
        stored = self._stored(target_dir)
        local_dir = self._codex_dir()
        changes = [
            self._plan_sanitized_config_copy(
                stored / "config.toml",
                self._config_path(),
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
        self._write_sanitized_config(
            local_config, stored / "config.toml", dest_root=target_dir
        )
        ui.sub("config.toml")

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

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        stored = self._stored(target_dir)
        stored_config = stored / "config.toml"
        self._validate_sync_to_paths(stored, target_dir)

        local_dir = self._codex_dir()
        local_dir.mkdir(parents=True, exist_ok=True)

        local_config = self._config_path()
        self._backup_file(local_config, backup_dir, "config.toml")
        self._write_sanitized_config(
            stored_config, local_config, source_root=target_dir
        )
        ui.sub("config.toml")

        for name in OPTIONAL_FILES:
            stored_file = stored / name
            if not stored_file.exists():
                continue
            local_file = local_dir / name
            self._backup_file(local_file, backup_dir, name)
            copy_file_safely(stored_file, local_file, name, source_root=target_dir)
            ui.sub(name)

        for name in OPTIONAL_DIRECTORIES:
            stored_dir = stored / name
            if not stored_dir.exists():
                continue
            ensure_directory(stored_dir, f"{name}/", root=target_dir)
            local_dir_for_name = local_dir / name
            self._backup_tree(local_dir_for_name, backup_dir, name)
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

        return self._merge_statuses(statuses)
