"""Claude Code sync — settings, plugins, MCP servers, with plugin auto-restore."""

from __future__ import annotations
import json
import shutil
import subprocess  # noqa: F401 - tests patch this shared module object.
from pathlib import Path
from typing import Any
from dotsync import ui
from dotsync.apps.base import (
    App,
    AppStatus,
    copy_file_safely,
    diff_files,
    ensure_directory,
    ensure_not_symlink,
    ensure_path_within_root,
    write_text_safely,
    _hash,
)
from dotsync.apps.mcp_sanitizer import filter_claude_mcp_servers
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

GLOBAL_RULE_DIRECTORIES = ("commands", "agents", "skills", "output-styles")
PLUGIN_RUNTIME_KEYS = {
    "installPath",
    "version",
    "installedAt",
    "lastUpdated",
    "gitCommitSha",
}

# Manual verification checklist for Claude global rules sync:
# 1. Run `PYTHONPATH=lib python3 -m dotsync status` and confirm the Claude row
#    reports clean or lists CLAUDE.md / global-rule directories naturally.
# 2. Use an isolated DOTSYNC_DIR under /tmp, run `dotsync init --apps claude`
#    with `--yes --no-shell-init`, then `dotsync backup claude`; confirm stored
#    `claude/` contains existing CLAUDE.md and global-rule directories.
# 3. Set HOME to a fake /tmp home and run `dotsync apply claude --yes`; confirm the
#    same global-rule items are restored under the fake ~/.claude/.
# 4. Remove the temporary DOTSYNC_DIR and fake HOME directories.


class ClaudeApp(App):
    name = "claude"
    description = "Claude Code (settings + plugins + MCP servers + global rules)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return (Path.home() / ".claude" / "settings.json").exists()

    def _claude_dir(self) -> Path:
        return Path.home() / ".claude"

    def _claude_json(self) -> Path:
        return Path.home() / ".claude.json"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    def _scan(self, root: Path) -> TreeScan:
        try:
            return scan_tree(root)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    def _diff_tree(
        self, local: Path, stored: Path
    ) -> tuple[set[Path], set[Path], set[Path]]:
        """Return (added_in_stored, removed_in_stored, modified) relative paths."""
        try:
            diff = diff_trees(local, stored)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        return set(diff.removes), set(diff.creates), set(diff.updates)

    def _mirror_tree(
        self,
        src: Path,
        dst: Path,
        *,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> None:
        """Make dst's regular files match src's; symlinked entries stay untouched."""
        ensure_directory(src, str(src), root=source_root)
        ensure_directory(dst, str(dst), root=dest_root)
        dst.mkdir(parents=True, exist_ok=True)
        src_scan = self._scan(src)
        dst_scan = self._scan(dst)
        blocked = blocked_by_symlink(src_scan.files, dst_scan.symlinks)
        kept = blocked_by_symlink(dst_scan.files, src_scan.symlinks)
        for rel in sorted(src_scan.symlinks | dst_scan.symlinks):
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

        subdirs = sorted(
            (d for d in dst.rglob("*") if d.is_dir() and not d.is_symlink()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for d in subdirs:
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

    def _sync_from_global_rules(self, target_dir: Path) -> None:
        """Mirror present user-level Claude global rules from local to stored."""
        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        ensure_directory(stored, "claude/", root=target_dir)

        src_md = cdir / "CLAUDE.md"
        if src_md.is_symlink():
            raise RuntimeError(f"{src_md} is a symlink (CLAUDE.md); refusing to sync symlinks")
        if src_md.exists():
            stored.mkdir(parents=True, exist_ok=True)
            copy_file_safely(
                src_md, stored / "CLAUDE.md", "CLAUDE.md", dest_root=target_dir
            )
            ui.ok("CLAUDE.md")
        else:
            stale_md = stored / "CLAUDE.md"
            if stale_md.exists() or stale_md.is_symlink():
                self._remove_managed_path(stale_md, "CLAUDE.md", root=target_dir)
                ui.ok("CLAUDE.md removed")

        for name in GLOBAL_RULE_DIRECTORIES:
            src_dir = cdir / name
            if src_dir.is_symlink():
                raise RuntimeError(f"{src_dir} is a symlink ({name}/); refusing to sync symlinks")
            if src_dir.exists():
                self._mirror_tree(src_dir, stored / name, dest_root=target_dir)
                ui.ok(f"{name}/")
            else:
                stale_dir = stored / name
                if stale_dir.exists() or stale_dir.is_symlink():
                    self._remove_managed_path(stale_dir, f"{name}/", root=target_dir)
                    ui.ok(f"{name}/ removed")

    def _sync_to_global_rules(self, target_dir: Path, backup_dir: Path) -> None:
        """Restore present stored user-level Claude global rules to local."""
        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        bdir = backup_dir / self.name

        stored_md = stored / "CLAUDE.md"
        local_md = cdir / "CLAUDE.md"
        if stored_md.exists():
            ensure_directory(bdir, "claude backup", root=backup_dir)
            bdir.mkdir(parents=True, exist_ok=True)
            if local_md.exists():
                copy_file_safely(
                    local_md, bdir / "CLAUDE.md", "CLAUDE.md", dest_root=backup_dir
                )
            ensure_directory(cdir, "~/.claude/")
            cdir.mkdir(parents=True, exist_ok=True)
            copy_file_safely(stored_md, local_md, "CLAUDE.md", source_root=target_dir)
            ui.ok("CLAUDE.md")

        for name in GLOBAL_RULE_DIRECTORIES:
            stored_dir = stored / name
            local_dir = cdir / name
            if stored_dir.exists():
                ensure_directory(stored_dir, f"{name}/", root=target_dir)
                if local_dir.exists():
                    self._mirror_tree(local_dir, bdir / name, dest_root=backup_dir)
                self._mirror_tree(stored_dir, local_dir, source_root=target_dir)
                ui.ok(f"{name}/")

    def _diff_global_rules(self, target_dir: Path) -> AppStatus:
        """Compare user-level Claude global rules."""
        cdir = self._claude_dir()
        stored = self._stored(target_dir)

        flat_paths: list[str] = []
        summary_parts: list[tuple[str, int]] = []

        local_md = cdir / "CLAUDE.md"
        stored_md = stored / "CLAUDE.md"
        md_changed = False
        if local_md.is_symlink() or stored_md.is_symlink():
            return AppStatus(state="unknown", details="CLAUDE.md is a symlink")
        if local_md.exists() and stored_md.exists():
            if _hash(local_md) != _hash(stored_md):
                md_changed = True
        elif local_md.exists() ^ stored_md.exists():
            md_changed = True
        if md_changed:
            flat_paths.append("CLAUDE.md")
            summary_parts.append(("CLAUDE.md", 1))

        for name in GLOBAL_RULE_DIRECTORIES:
            try:
                added, removed, modified = self._diff_tree(cdir / name, stored / name)
            except RuntimeError as exc:
                return AppStatus(state="unknown", details=str(exc))
            count = len(added) + len(removed) + len(modified)
            if count > 0:
                for rel in sorted(added | removed | modified):
                    flat_paths.append(f"{name}/{rel}")
                summary_parts.append((f"{name}/", count))

        if not flat_paths:
            return AppStatus(state="clean")

        if len(flat_paths) <= 8:
            details = ", ".join(flat_paths)
        else:
            details = ", ".join(f"{label} ({n} changed)" for label, n in summary_parts)
        return AppStatus(state="dirty", details=details)

    @staticmethod
    def _merge_status(base: AppStatus, rules: AppStatus) -> AppStatus:
        """Merge statuses with missing/unknown > dirty > clean priority."""
        if base.state == "missing":
            return base
        if base.state == "unknown" or rules.state == "unknown":
            parts = [s.details for s in (base, rules) if s.details]
            return AppStatus(state="unknown", details=", ".join(parts))
        if base.state == "clean" and rules.state == "clean":
            return AppStatus(state="clean")
        parts = [s for s in (base.details, rules.details) if s]
        return AppStatus(state="dirty", details=", ".join(parts))

    def _sanitized_mcp_servers(self, servers: dict) -> dict[str, object]:
        return filter_claude_mcp_servers(servers).value

    @staticmethod
    def _load_json_file(path: Path) -> Any:
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{path} is corrupted: {e}") from e

    @staticmethod
    def _require_mapping(value: Any, path: Path, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RuntimeError(f"{path} must contain a JSON object ({label})")
        return value

    @staticmethod
    def _plugin_config_name(plugin_id: str, source: Path) -> str:
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise RuntimeError(f"{source} contains invalid plugin id {plugin_id!r}")
        name = plugin_id.split("@", 1)[0]
        if (
            not name
            or name in {".", ".."}
            or ".." in plugin_id
            or "/" in plugin_id
            or "\\" in plugin_id
            or any(ord(ch) < 32 for ch in plugin_id)
        ):
            raise RuntimeError(f"{source} contains unsafe plugin id {plugin_id!r}")
        return name

    @staticmethod
    def _plugins_from_installed_doc(data: Any, path: Path) -> dict[str, Any]:
        doc = ClaudeApp._require_mapping(data, path, "installed plugins")
        plugins = doc["plugins"] if "plugins" in doc else {}
        if plugins is None:
            plugins = {}
        if not isinstance(plugins, dict):
            raise RuntimeError(f"{path} plugins must contain a JSON object")
        for plugin_id, entries in plugins.items():
            ClaudeApp._plugin_config_name(plugin_id, path)
            if entries is None:
                entry_docs: list[dict[str, Any]] = []
            elif isinstance(entries, list):
                if not all(isinstance(entry, dict) for entry in entries):
                    raise RuntimeError(
                        f"{path} plugin {plugin_id!r} entries must be JSON objects"
                    )
                entry_docs = entries
            elif isinstance(entries, dict):
                entry_docs = [entries]
            else:
                raise RuntimeError(
                    f"{path} plugin {plugin_id!r} entries must be a list or object"
                )
            for entry in entry_docs:
                install_path = entry.get("installPath")
                if install_path is not None and not isinstance(install_path, str):
                    raise RuntimeError(
                        f"{path} plugin {plugin_id!r} installPath must be a string"
                    )
        return plugins

    @staticmethod
    def _marketplaces_from_doc(data: Any, path: Path) -> dict[str, Any]:
        doc = ClaudeApp._require_mapping(data, path, "known marketplaces")
        for name, meta in doc.items():
            if not name.strip():
                raise RuntimeError(f"{path} contains invalid marketplace name {name!r}")
            if not isinstance(meta, dict):
                raise RuntimeError(f"{path} marketplace {name!r} must be a JSON object")
            source = meta.get("source") or {}
            if not isinstance(source, dict):
                raise RuntimeError(
                    f"{path} marketplace {name!r} source must be a JSON object"
                )
            kind = source.get("source")
            if kind is None:
                continue
            if not isinstance(kind, str):
                raise RuntimeError(
                    f"{path} marketplace {name!r} source kind must be a string"
                )
            required_key = {
                "github": "repo",
                "directory": "path",
                "git": "url",
                "local": "path",
            }.get(kind)
            if required_key is not None and not isinstance(
                source.get(required_key), str
            ):
                raise RuntimeError(
                    f"{path} marketplace {name!r} source {required_key!r} must be a string"
                )
        return doc

    @staticmethod
    def _mcp_servers_from_doc(data: Any, path: Path) -> dict[str, Any]:
        return ClaudeApp._require_mapping(data, path, "MCP servers")

    @staticmethod
    def _mcp_servers_from_claude_doc(data: Any, path: Path) -> dict[str, Any]:
        doc = ClaudeApp._require_mapping(data, path, "Claude config")
        servers = doc.get("mcpServers", {})
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            raise RuntimeError(f"{path} mcpServers must contain a JSON object")
        return servers

    @staticmethod
    def _settings_from_doc(data: Any, path: Path) -> dict[str, Any]:
        settings = ClaudeApp._require_mapping(data, path, "settings")
        enabled_map = settings.get("enabledPlugins", {}) or {}
        if not isinstance(enabled_map, dict):
            raise RuntimeError(f"{path} enabledPlugins must contain a JSON object")
        for plugin_id in enabled_map:
            ClaudeApp._plugin_config_name(plugin_id, path)
        return settings

    @staticmethod
    def _normalized_installed_plugins(data: Any, path: Path) -> dict[str, Any]:
        plugins = ClaudeApp._plugins_from_installed_doc(data, path)
        normalized: dict[str, list[dict[str, Any]]] = {}
        for plugin_id, entries in plugins.items():
            if entries is None:
                entry_docs = []
            elif isinstance(entries, list):
                entry_docs = entries
            elif isinstance(entries, dict):
                entry_docs = [entries]
            else:
                entry_docs = []
            normalized_entries = [
                {
                    key: value
                    for key, value in entry.items()
                    if key not in PLUGIN_RUNTIME_KEYS
                }
                for entry in entry_docs
            ]
            normalized[plugin_id] = sorted(
                normalized_entries,
                key=lambda entry: json.dumps(
                    entry, sort_keys=True, ensure_ascii=False
                ),
            )
        return {"plugins": normalized}

    @staticmethod
    def _normalized_known_marketplaces(data: Any, path: Path) -> dict[str, Any]:
        marketplaces = ClaudeApp._marketplaces_from_doc(data, path)
        return {
            name: {"source": meta.get("source") or {}}
            for name, meta in sorted(marketplaces.items())
        }

    def _plan_json_semantic_copy(
        self,
        label: str,
        source: Path,
        dest: Path,
        normalizer,
        *,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> Change:
        safety = plan_file_copy(
            label, source, dest, source_root=source_root, dest_root=dest_root
        )
        if safety.kind != "update":
            return safety
        try:
            source_doc = normalizer(self._load_json_file(source), source)
            dest_doc = normalizer(self._load_json_file(dest), dest)
        except (RuntimeError, json.JSONDecodeError) as exc:
            return Change(label, "unknown", source, dest, str(exc))
        return Change(
            label,
            "unchanged" if source_doc == dest_doc else "update",
            source,
            dest,
        )

    def _validate_sync_from_sources(self) -> dict[str, object]:
        cdir = self._claude_dir()
        required_files = [
            (cdir / "settings.json", "claude/settings.json"),
            (
                cdir / "plugins" / "installed_plugins.json",
                "claude/plugins/installed_plugins.json",
            ),
            (
                cdir / "plugins" / "known_marketplaces.json",
                "claude/plugins/known_marketplaces.json",
            ),
            (self._claude_json(), "~/.claude.json"),
        ]
        for path, label in required_files:
            if not path.is_file():
                raise FileNotFoundError(f"{path} not found ({label} missing)")
            ensure_not_symlink(path, label)

        self._settings_from_doc(
            self._load_json_file(cdir / "settings.json"),
            cdir / "settings.json",
        )
        self._plugins_from_installed_doc(
            self._load_json_file(cdir / "plugins" / "installed_plugins.json"),
            cdir / "plugins" / "installed_plugins.json",
        )
        self._marketplaces_from_doc(
            self._load_json_file(cdir / "plugins" / "known_marketplaces.json"),
            cdir / "plugins" / "known_marketplaces.json",
        )
        mcp_servers = self._mcp_servers_from_claude_doc(
            self._load_json_file(self._claude_json()),
            self._claude_json(),
        )
        return self._sanitized_mcp_servers(mcp_servers)

    def _plan_mcp_from(self, stored: Path, target_dir: Path) -> Change:
        source = self._claude_json()
        dest = stored / "mcp-servers.json"
        safety = plan_file_copy(
            "mcp-servers.json", source, dest, dest_root=target_dir
        )
        if safety.kind == "unknown":
            return safety
        if not source.exists():
            return Change("mcp-servers.json", "missing-source", source, dest)
        try:
            local_doc = json.loads(source.read_text())
        except json.JSONDecodeError:
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json is invalid",
            )
        if not isinstance(local_doc, dict):
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json is invalid",
            )
        mcp_servers = local_doc.get("mcpServers", {})
        if mcp_servers is None:
            mcp_servers = {}
        if not isinstance(mcp_servers, dict):
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json mcpServers is invalid",
            )
        data = self._sanitized_mcp_servers(mcp_servers)
        planned = json.dumps(data, indent=2, ensure_ascii=False)
        if not dest.exists():
            return Change("mcp-servers.json", "create", source, dest, diffable=False)
        try:
            current_doc = json.loads(dest.read_text())
        except json.JSONDecodeError:
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "stored mcp-servers.json is invalid",
            )
        if not isinstance(current_doc, dict):
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "stored mcp-servers.json is invalid",
            )
        current = filter_claude_mcp_servers(current_doc)
        if current.changed:
            return Change(
                "mcp-servers.json",
                "update",
                source,
                dest,
                "remove dynamic Serena MCP URL",
                diffable=False,
            )
        return Change(
            "mcp-servers.json",
            "unchanged"
            if json.dumps(current.value, indent=2, ensure_ascii=False) == planned
            else "update",
            source,
            dest,
            diffable=False,
        )

    def _plan_mcp_to(self, stored: Path, target_dir: Path) -> Change:
        source = stored / "mcp-servers.json"
        dest = self._claude_json()
        safety = plan_file_copy(
            "mcp-servers.json", source, dest, source_root=target_dir
        )
        if safety.kind == "unknown":
            return safety
        if not source.exists():
            return Change("mcp-servers.json", "missing-source", source, dest)
        try:
            stored_doc = json.loads(source.read_text())
        except json.JSONDecodeError:
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "stored mcp-servers.json is invalid",
            )
        if not isinstance(stored_doc, dict):
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "stored mcp-servers.json is invalid",
            )
        stored_mcp = self._sanitized_mcp_servers(stored_doc)
        try:
            local_doc = json.loads(dest.read_text()) if dest.exists() else {}
        except json.JSONDecodeError:
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json is invalid",
            )
        if not isinstance(local_doc, dict):
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json is invalid",
            )
        if not dest.exists():
            return Change("mcp-servers.json", "create", source, dest, diffable=False)
        if "mcpServers" not in local_doc:
            return Change("mcp-servers.json", "update", source, dest, diffable=False)
        if not isinstance(local_doc.get("mcpServers"), dict):
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json mcpServers is invalid",
            )
        planned_doc = dict(local_doc)
        planned_doc["mcpServers"] = stored_mcp
        planned = json.dumps(planned_doc, indent=2, ensure_ascii=False)
        current_mcp = filter_claude_mcp_servers(local_doc.get("mcpServers", {}))
        if current_mcp.changed:
            return Change(
                "mcp-servers.json",
                "update",
                source,
                dest,
                "remove dynamic Serena MCP URL",
                diffable=False,
            )
        current_doc = dict(local_doc)
        current_doc["mcpServers"] = current_mcp.value
        return Change(
            "mcp-servers.json",
            "unchanged"
            if json.dumps(current_doc, indent=2, ensure_ascii=False) == planned
            else "update",
            source,
            dest,
            diffable=False,
        )

    def _plan_tree_mirror(
        self,
        label: str,
        source: Path,
        dest: Path,
        *,
        source_root: Path | None = None,
        dest_root: Path | None = None,
    ) -> Change:
        change = plan_tree_mirror(
            label, source, dest, source_root=source_root, dest_root=dest_root
        )
        if source.exists() and not dest.exists() and change.kind == "unchanged":
            return Change(
                change.label,
                "create",
                change.source,
                change.dest,
                "create directory",
            )
        return change

    def _installed_plugin_config_changes_from(self, stored: Path) -> list[Change]:
        changes: list[Change] = []
        installed = self._claude_dir() / "plugins" / "installed_plugins.json"
        try:
            plugin_names = self._installed_plugin_names(installed)
        except RuntimeError as exc:
            return [Change("plugins config", "unknown", installed, None, str(exc))]
        for plugin_name in plugin_names:
            src = self._claude_dir() / "plugins" / plugin_name / "config.json"
            if src.exists():
                changes.append(
                    plan_file_copy(
                        f"plugins/{plugin_name}/config.json",
                        src,
                        stored / "plugins" / plugin_name / "config.json",
                    )
                )
        return changes

    def _installed_plugin_config_changes_to(self, stored: Path) -> list[Change]:
        changes: list[Change] = []
        installed = stored / "plugins" / "installed_plugins.json"
        try:
            plugin_names = self._installed_plugin_names(installed)
        except RuntimeError as exc:
            return [Change("plugins config", "unknown", installed, None, str(exc))]
        for plugin_name in plugin_names:
            src = stored / "plugins" / plugin_name / "config.json"
            if src.exists():
                changes.append(
                    plan_file_copy(
                        f"plugins/{plugin_name}/config.json",
                        src,
                        self._claude_dir() / "plugins" / plugin_name / "config.json",
                    )
                )
        return changes

    def plan_from(self, target_dir: Path) -> AppPlan:
        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        changes = [
            plan_file_copy(
                "settings.json",
                cdir / "settings.json",
                stored / "settings.json",
                dest_root=target_dir,
            ),
            self._plan_json_semantic_copy(
                "plugins/installed_plugins.json",
                cdir / "plugins" / "installed_plugins.json",
                stored / "plugins" / "installed_plugins.json",
                self._normalized_installed_plugins,
                dest_root=target_dir,
            ),
            self._plan_json_semantic_copy(
                "plugins/known_marketplaces.json",
                cdir / "plugins" / "known_marketplaces.json",
                stored / "plugins" / "known_marketplaces.json",
                self._normalized_known_marketplaces,
                dest_root=target_dir,
            ),
            self._plan_mcp_from(stored, target_dir),
        ]
        changes.extend(self._installed_plugin_config_changes_from(stored))
        local_md = cdir / "CLAUDE.md"
        stored_md = stored / "CLAUDE.md"
        if local_md.is_symlink():
            changes.append(
                Change(
                    "CLAUDE.md",
                    "unknown",
                    local_md,
                    stored_md,
                    f"{local_md} is a symlink",
                )
            )
        elif local_md.exists():
            changes.append(
                plan_file_copy(
                    "CLAUDE.md",
                    local_md,
                    stored_md,
                    dest_root=target_dir,
                )
            )
        elif stored_md.exists() or stored_md.is_symlink():
            changes.append(
                Change(
                    "CLAUDE.md",
                    "remove",
                    None,
                    stored_md,
                    "local file missing",
                )
            )
        for name in GLOBAL_RULE_DIRECTORIES:
            local_dir = cdir / name
            if local_dir.is_symlink():
                changes.append(
                    Change(
                        f"{name}/",
                        "unknown",
                        local_dir,
                        stored / name,
                        f"{local_dir} is a symlink",
                    )
                )
            elif local_dir.exists():
                changes.append(
                    self._plan_tree_mirror(
                        f"{name}/",
                        local_dir,
                        stored / name,
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
        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        changes = [
            plan_file_copy(
                "settings.json",
                stored / "settings.json",
                cdir / "settings.json",
                source_root=target_dir,
            ),
            self._plan_json_semantic_copy(
                "plugins/installed_plugins.json",
                stored / "plugins" / "installed_plugins.json",
                cdir / "plugins" / "installed_plugins.json",
                self._normalized_installed_plugins,
                source_root=target_dir,
            ),
            self._plan_json_semantic_copy(
                "plugins/known_marketplaces.json",
                stored / "plugins" / "known_marketplaces.json",
                cdir / "plugins" / "known_marketplaces.json",
                self._normalized_known_marketplaces,
                source_root=target_dir,
            ),
            self._plan_mcp_to(stored, target_dir),
        ]
        changes.extend(self._installed_plugin_config_changes_to(stored))
        if (stored / "CLAUDE.md").exists():
            changes.append(
                plan_file_copy(
                    "CLAUDE.md",
                    stored / "CLAUDE.md",
                    cdir / "CLAUDE.md",
                    source_root=target_dir,
                )
            )
        for name in GLOBAL_RULE_DIRECTORIES:
            stored_dir = stored / name
            if stored_dir.exists():
                changes.append(
                    self._plan_tree_mirror(
                        f"{name}/",
                        stored_dir,
                        cdir / name,
                        source_root=target_dir,
                    )
                )
        return AppPlan(self.name, "to", changes, self.description)

    def sync_from(self, target_dir: Path) -> None:
        ui.dim(f"source → {self._claude_dir()}")

        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        mcp_servers = self._validate_sync_from_sources()
        ensure_directory(stored, "claude/", root=target_dir)
        ensure_directory(stored / "plugins", "plugins/", root=target_dir)
        (stored / "plugins").mkdir(parents=True, exist_ok=True)

        copy_file_safely(
            cdir / "settings.json",
            stored / "settings.json",
            "settings.json",
            dest_root=target_dir,
        )
        ui.ok("settings.json")

        for fname in ("installed_plugins.json", "known_marketplaces.json"):
            copy_file_safely(
                cdir / "plugins" / fname,
                stored / "plugins" / fname,
                f"plugins/{fname}",
                dest_root=target_dir,
            )
            ui.ok(f"plugins/{fname}")

        write_text_safely(
            stored / "mcp-servers.json",
            json.dumps(mcp_servers, indent=2, ensure_ascii=False),
            "mcp-servers.json",
            dest_root=target_dir,
        )
        ui.ok("mcp-servers.json")

        for plugin_name in self._installed_plugin_names(
            stored / "plugins" / "installed_plugins.json"
        ):
            src = cdir / "plugins" / plugin_name / "config.json"
            if src.exists():
                dst_dir = stored / "plugins" / plugin_name
                ensure_directory(dst_dir, f"plugins/{plugin_name}/", root=target_dir)
                dst_dir.mkdir(parents=True, exist_ok=True)
                copy_file_safely(
                    src,
                    dst_dir / "config.json",
                    f"plugins/{plugin_name}/config.json",
                    dest_root=target_dir,
                )
                ui.ok(f"plugins/{plugin_name}/config.json")

        self._sync_from_global_rules(target_dir)

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        stored = self._stored(target_dir)
        ensure_directory(stored, "claude/", root=target_dir)
        self._validate_stored_global_rules(stored, target_dir)
        stored_mcp = self._validate_sync_to_sources(stored, target_dir)
        self._validate_sync_to_optional_paths(stored, target_dir)

        cdir = self._claude_dir()
        ensure_directory(cdir, "~/.claude/")
        cdir.mkdir(parents=True, exist_ok=True)
        ensure_directory(cdir / "plugins", "~/.claude/plugins/")
        (cdir / "plugins").mkdir(parents=True, exist_ok=True)

        bdir = backup_dir / self.name
        ensure_directory(bdir, "claude backup", root=backup_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        ensure_directory(bdir / "plugins", "plugins/", root=backup_dir)
        (bdir / "plugins").mkdir(parents=True, exist_ok=True)

        for src, rel in [
            (cdir / "settings.json", "settings.json"),
            (
                cdir / "plugins" / "installed_plugins.json",
                "plugins/installed_plugins.json",
            ),
            (
                cdir / "plugins" / "known_marketplaces.json",
                "plugins/known_marketplaces.json",
            ),
            (self._claude_json(), ".claude.json"),
        ]:
            if src.exists():
                dst = bdir / rel
                ensure_path_within_root(dst, backup_dir, rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                copy_file_safely(src, dst, rel, dest_root=backup_dir)
        ui.dim(f"backup → {bdir}")

        copy_file_safely(
            stored / "settings.json",
            cdir / "settings.json",
            "settings.json",
            source_root=target_dir,
        )
        ui.ok("settings.json")
        copy_file_safely(
            stored / "plugins" / "installed_plugins.json",
            cdir / "plugins" / "installed_plugins.json",
            "plugins/installed_plugins.json",
            source_root=target_dir,
        )
        ui.ok("plugins/installed_plugins.json")
        copy_file_safely(
            stored / "plugins" / "known_marketplaces.json",
            cdir / "plugins" / "known_marketplaces.json",
            "plugins/known_marketplaces.json",
            source_root=target_dir,
        )
        ui.ok("plugins/known_marketplaces.json")

        claude_json_path = self._claude_json()
        try:
            cj = (
                json.loads(claude_json_path.read_text())
                if claude_json_path.exists()
                else {}
            )
        except json.JSONDecodeError as e:
            raise RuntimeError(f"~/.claude.json is corrupted: {e}") from e
        cj["mcpServers"] = self._sanitized_mcp_servers(stored_mcp)
        write_text_safely(
            claude_json_path,
            json.dumps(cj, indent=2, ensure_ascii=False),
            "~/.claude.json",
        )
        ui.ok("mcp-servers.json → ~/.claude.json")

        for plugin_name in self._installed_plugin_names(
            stored / "plugins" / "installed_plugins.json"
        ):
            src = stored / "plugins" / plugin_name / "config.json"
            if not src.exists():
                continue
            local_plugin_dir = cdir / "plugins" / plugin_name
            ensure_directory(local_plugin_dir, f"plugins/{plugin_name}/")
            local_plugin_dir.mkdir(parents=True, exist_ok=True)
            local_cfg = local_plugin_dir / "config.json"
            if local_cfg.exists():
                bdst = bdir / "plugins" / plugin_name
                ensure_directory(bdst, f"plugins/{plugin_name}/", root=backup_dir)
                bdst.mkdir(parents=True, exist_ok=True)
                copy_file_safely(
                    local_cfg,
                    bdst / "config.json",
                    f"plugins/{plugin_name}/config.json",
                    dest_root=backup_dir,
                )
            copy_file_safely(
                src,
                local_cfg,
                f"plugins/{plugin_name}/config.json",
                source_root=target_dir,
            )
            ui.ok(f"plugins/{plugin_name}/config.json")

        ui.divider("restore marketplaces · plugins")
        self._restore_plugins(stored)

        self._enforce_disabled(stored / "settings.json")

        self._sync_to_global_rules(target_dir, backup_dir)

        ui.dim("hint: restart Claude Code to pick up new plugins")

    def _validate_stored_global_rules(self, stored: Path, target_dir: Path) -> None:
        stored_md = stored / "CLAUDE.md"
        if stored_md.exists() or stored_md.is_symlink():
            ensure_path_within_root(stored_md, target_dir, "CLAUDE.md")
            ensure_not_symlink(stored_md, "CLAUDE.md")
            if not stored_md.is_file():
                raise RuntimeError(f"{stored_md} is not a file (CLAUDE.md)")
        for name in GLOBAL_RULE_DIRECTORIES:
            stored_dir = stored / name
            if stored_dir.exists() or stored_dir.is_symlink():
                ensure_directory(stored_dir, f"{name}/", root=target_dir)
                self._scan(stored_dir)

    def _validate_sync_to_optional_paths(self, stored: Path, target_dir: Path) -> None:
        cdir = self._claude_dir()
        stored_md = stored / "CLAUDE.md"
        local_md = cdir / "CLAUDE.md"
        if stored_md.exists() and (local_md.exists() or local_md.is_symlink()):
            ensure_not_symlink(local_md, "CLAUDE.md")
            if not local_md.is_file():
                raise RuntimeError(f"{local_md} is not a file (CLAUDE.md)")

        for name in GLOBAL_RULE_DIRECTORIES:
            stored_dir = stored / name
            local_dir = cdir / name
            if stored_dir.exists() and (local_dir.exists() or local_dir.is_symlink()):
                ensure_directory(local_dir, f"{name}/")
                self._scan(local_dir)

        for plugin_name in self._installed_plugin_names(
            stored / "plugins" / "installed_plugins.json"
        ):
            stored_cfg = stored / "plugins" / plugin_name / "config.json"
            if stored_cfg.exists() or stored_cfg.is_symlink():
                ensure_path_within_root(
                    stored_cfg,
                    target_dir,
                    f"plugins/{plugin_name}/config.json",
                )
                ensure_not_symlink(
                    stored_cfg, f"plugins/{plugin_name}/config.json"
                )
                if not stored_cfg.is_file():
                    raise RuntimeError(
                        f"{stored_cfg} is not a file (plugins/{plugin_name}/config.json)"
                    )
                local_cfg = cdir / "plugins" / plugin_name / "config.json"
                if local_cfg.exists() or local_cfg.is_symlink():
                    ensure_not_symlink(
                        local_cfg, f"plugins/{plugin_name}/config.json"
                    )
                    if not local_cfg.is_file():
                        raise RuntimeError(
                            f"{local_cfg} is not a file (plugins/{plugin_name}/config.json)"
                        )

    def _validate_sync_to_sources(self, stored: Path, target_dir: Path) -> Any:
        required_files = [
            (stored / "settings.json", "claude/settings.json"),
            (
                stored / "plugins" / "installed_plugins.json",
                "claude/plugins/installed_plugins.json",
            ),
            (
                stored / "plugins" / "known_marketplaces.json",
                "claude/plugins/known_marketplaces.json",
            ),
            (stored / "mcp-servers.json", "claude/mcp-servers.json"),
        ]
        for path, label in required_files:
            ensure_path_within_root(path, target_dir, label)
            if not path.is_file():
                raise FileNotFoundError(f"{path} not found ({label} missing)")

        self._settings_from_doc(
            self._load_required_stored_json(stored / "settings.json"),
            stored / "settings.json",
        )
        self._plugins_from_installed_doc(
            self._load_required_stored_json(
                stored / "plugins" / "installed_plugins.json"
            ),
            stored / "plugins" / "installed_plugins.json",
        )
        self._marketplaces_from_doc(
            self._load_required_stored_json(
                stored / "plugins" / "known_marketplaces.json"
            ),
            stored / "plugins" / "known_marketplaces.json",
        )
        stored_mcp = self._mcp_servers_from_doc(
            self._load_required_stored_json(stored / "mcp-servers.json"),
            stored / "mcp-servers.json",
        )
        return self._sanitized_mcp_servers(stored_mcp)

    def _load_required_stored_json(self, path: Path) -> Any:
        return self._load_json_file(path)

    def status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        try:
            ensure_directory(stored, "claude/", root=target_dir)
        except RuntimeError as exc:
            return AppStatus(state="unknown", details=str(exc))
        cdir = self._claude_dir()
        pairs = [
            (cdir / "settings.json", stored / "settings.json"),
            (
                cdir / "plugins" / "installed_plugins.json",
                stored / "plugins" / "installed_plugins.json",
            ),
            (
                cdir / "plugins" / "known_marketplaces.json",
                stored / "plugins" / "known_marketplaces.json",
            ),
        ]
        base = diff_files(pairs)
        if base.state == "missing":
            return base
        if base.state == "unknown":
            return base
        local_cj = self._claude_json()
        stored_mcp = stored / "mcp-servers.json"
        if not local_cj.exists() or not stored_mcp.exists():
            return AppStatus(state="missing", details="mcp-servers.json")
        if local_cj.is_symlink() or stored_mcp.is_symlink():
            return AppStatus(state="unknown", details="mcp-servers.json is a symlink")
        local_mcp = self._sanitized_mcp_servers(
            json.loads(local_cj.read_text()).get("mcpServers", {})
        )
        stored_mcp_data = self._sanitized_mcp_servers(
            json.loads(stored_mcp.read_text())
        )
        if local_mcp != stored_mcp_data:
            if base.state == "dirty":
                base = AppStatus(
                    state="dirty", details=f"{base.details}, mcp-servers.json"
                )
            else:
                base = AppStatus(state="dirty", details="mcp-servers.json")
        rules = self._diff_global_rules(target_dir)
        return self._merge_status(base, rules)

    @staticmethod
    def _installed_plugin_names(installed_plugins_path: Path) -> list[str]:
        if not installed_plugins_path.exists():
            return []
        plugins = ClaudeApp._plugins_from_installed_doc(
            ClaudeApp._load_json_file(installed_plugins_path),
            installed_plugins_path,
        )
        return sorted(
            {
                ClaudeApp._plugin_config_name(plugin_id, installed_plugins_path)
                for plugin_id in plugins
            }
        )

    def _restore_plugins(self, stored: Path) -> None:
        known_marketplaces = stored / "plugins" / "known_marketplaces.json"
        installed_plugins = stored / "plugins" / "installed_plugins.json"
        marketplaces = self._marketplaces_from_doc(
            self._load_json_file(known_marketplaces),
            known_marketplaces,
        )
        plugins = self._plugins_from_installed_doc(
            self._load_json_file(installed_plugins),
            installed_plugins,
        )

        for mp_name, mp_meta in marketplaces.items():
            source = mp_meta.get("source") or {}
            spec = self._marketplace_spec(source)
            if not spec:
                ui.warn(f"marketplace `{mp_name}` source unknown — skipping")
                continue
            self._run_claude_cli(
                ["plugin", "marketplace", "add", "--scope", "user", spec],
                desc=f"marketplace add {mp_name}",
            )

        for plugin_id, entries in plugins.items():
            entries = entries if isinstance(entries, list) else []
            if any(Path(e.get("installPath", "")).is_dir() for e in entries):
                ui.sub(f"plugin install {plugin_id} (cache present, skipped)")
                continue
            self._run_claude_cli(
                ["plugin", "install", "--scope", "user", plugin_id],
                desc=f"plugin install {plugin_id}",
            )

    def _enforce_disabled(self, settings_json_path: Path) -> None:
        if not settings_json_path.exists():
            return
        try:
            settings = json.loads(settings_json_path.read_text())
        except json.JSONDecodeError:
            return
        if not isinstance(settings, dict):
            return
        enabled_map = settings.get("enabledPlugins", {}) or {}
        if not isinstance(enabled_map, dict):
            return
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

    def _run_claude_cli(
        self, args: list[str], desc: str, tolerate_already: bool = True
    ) -> None:
        try:
            result = self._run_external(["claude", *args], desc=desc, fail_mode="warn")
        except FileNotFoundError:
            self.warnings.append(f"{desc} skipped: `claude` CLI not installed")
            ui.warn(f"{desc} skipped: `claude` CLI not installed")
            return
        if result.returncode == 0:
            combined = ((result.stdout or "") + (result.stderr or "")).lower()
            if tolerate_already and "already" in combined:
                ui.sub(f"{desc} (already present)")
            else:
                ui.ok(desc)
            return
        stderr = (result.stderr or "").strip()
        if tolerate_already and "already" in stderr.lower():
            ui.sub(f"{desc} (already present)")
            # Drop the auto-appended warning since "already" is success-equivalent.
            if self.warnings and desc in self.warnings[-1]:
                self.warnings.pop()
        else:
            ui.warn(f"{desc} failed: {stderr or 'unknown'}")
