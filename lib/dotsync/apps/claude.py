"""Claude Code sync — settings, plugins, MCP servers, with plugin auto-restore."""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files, _hash
from dotsync.plan import AppPlan, Change, plan_file_copy, plan_tree_mirror

GLOBAL_RULE_DIRECTORIES = ("commands", "agents", "skills", "output-styles")

# Manual verification checklist for Claude global rules sync:
# 1. Run `PYTHONPATH=lib python3 -m dotsync status` and confirm the Claude row
#    reports clean or lists CLAUDE.md / global-rule directories naturally.
# 2. Use an isolated DOTSYNC_DIR under /tmp, run `dotsync init --apps claude`
#    with `--yes --no-shell-init`, then `dotsync from claude`; confirm stored
#    `claude/` contains existing CLAUDE.md and global-rule directories.
# 3. Set HOME to a fake /tmp home and run `dotsync to claude --yes`; confirm the
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

    def _diff_tree(
        self, local: Path, stored: Path
    ) -> tuple[set[Path], set[Path], set[Path]]:
        """Return (added_in_stored, removed_in_stored, modified) relative paths."""
        local_files = (
            {f.relative_to(local) for f in local.rglob("*") if f.is_file()}
            if local.exists() else set()
        )
        stored_files = (
            {f.relative_to(stored) for f in stored.rglob("*") if f.is_file()}
            if stored.exists() else set()
        )
        added = stored_files - local_files
        removed = local_files - stored_files
        common = local_files & stored_files
        modified = {rel for rel in common if _hash(local / rel) != _hash(stored / rel)}
        return added, removed, modified

    def _mirror_tree(self, src: Path, dst: Path) -> None:
        """Strict full mirror: make dst's file tree match src."""
        dst.mkdir(parents=True, exist_ok=True)
        src_rels = {f.relative_to(src) for f in src.rglob("*") if f.is_file()}
        dst_rels = {f.relative_to(dst) for f in dst.rglob("*") if f.is_file()}

        for rel in src_rels:
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, target)

        for rel in dst_rels - src_rels:
            (dst / rel).unlink()

        subdirs = sorted(
            (d for d in dst.rglob("*") if d.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for d in subdirs:
            try:
                d.rmdir()
            except OSError:
                pass

    def _sync_from_global_rules(self, target_dir: Path) -> None:
        """Mirror present user-level Claude global rules from local to stored."""
        cdir = self._claude_dir()
        stored = self._stored(target_dir)

        src_md = cdir / "CLAUDE.md"
        if src_md.exists():
            stored.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_md, stored / "CLAUDE.md")
            ui.ok("CLAUDE.md")

        for name in GLOBAL_RULE_DIRECTORIES:
            src_dir = cdir / name
            if src_dir.exists():
                self._mirror_tree(src_dir, stored / name)
                ui.ok(f"{name}/")

    def _sync_to_global_rules(self, target_dir: Path, backup_dir: Path) -> None:
        """Restore present stored user-level Claude global rules to local."""
        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        bdir = backup_dir / self.name

        stored_md = stored / "CLAUDE.md"
        local_md = cdir / "CLAUDE.md"
        if stored_md.exists():
            bdir.mkdir(parents=True, exist_ok=True)
            if local_md.exists():
                shutil.copy2(local_md, bdir / "CLAUDE.md")
            cdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stored_md, local_md)
            ui.ok("CLAUDE.md")

        for name in GLOBAL_RULE_DIRECTORIES:
            stored_dir = stored / name
            local_dir = cdir / name
            if stored_dir.exists():
                if local_dir.exists():
                    shutil.copytree(local_dir, bdir / name, dirs_exist_ok=True)
                self._mirror_tree(stored_dir, local_dir)
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
        if local_md.exists() and stored_md.exists():
            if _hash(local_md) != _hash(stored_md):
                md_changed = True
        elif local_md.exists() ^ stored_md.exists():
            md_changed = True
        if md_changed:
            flat_paths.append("CLAUDE.md")
            summary_parts.append(("CLAUDE.md", 1))

        for name in GLOBAL_RULE_DIRECTORIES:
            added, removed, modified = self._diff_tree(cdir / name, stored / name)
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
        """Merge statuses with missing > dirty > clean priority."""
        if base.state == "missing":
            return base
        if base.state == "clean" and rules.state == "clean":
            return AppStatus(state="clean")
        parts = [s for s in (base.details, rules.details) if s]
        return AppStatus(state="dirty", details=", ".join(parts))

    def _plan_mcp_from(self, stored: Path) -> Change:
        source = self._claude_json()
        dest = stored / "mcp-servers.json"
        if not source.exists():
            return Change("mcp-servers.json", "missing-source", source, dest)
        try:
            data = json.loads(source.read_text()).get("mcpServers", {})
        except json.JSONDecodeError:
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "local ~/.claude.json is invalid",
            )
        planned = json.dumps(data, indent=2, ensure_ascii=False)
        if not dest.exists():
            return Change("mcp-servers.json", "create", source, dest)
        return Change(
            "mcp-servers.json",
            "unchanged" if dest.read_text() == planned else "update",
            source,
            dest,
        )

    def _plan_mcp_to(self, stored: Path) -> Change:
        source = stored / "mcp-servers.json"
        dest = self._claude_json()
        if not source.exists():
            return Change("mcp-servers.json", "missing-source", source, dest)
        try:
            stored_mcp = json.loads(source.read_text())
        except json.JSONDecodeError:
            return Change(
                "mcp-servers.json",
                "unknown",
                source,
                dest,
                "stored mcp-servers.json is invalid",
            )
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
        if not dest.exists():
            return Change("mcp-servers.json", "create", source, dest)
        planned_doc = dict(local_doc)
        planned_doc["mcpServers"] = stored_mcp
        planned = json.dumps(planned_doc, indent=2, ensure_ascii=False)
        return Change(
            "mcp-servers.json",
            "unchanged" if dest.read_text() == planned else "update",
            source,
            dest,
        )

    def _plan_tree_mirror(self, label: str, source: Path, dest: Path) -> Change:
        change = plan_tree_mirror(label, source, dest)
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
        for plugin_name in self._installed_plugin_names(installed):
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
        for plugin_name in self._installed_plugin_names(installed):
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
            plan_file_copy("settings.json", cdir / "settings.json", stored / "settings.json"),
            plan_file_copy(
                "plugins/installed_plugins.json",
                cdir / "plugins" / "installed_plugins.json",
                stored / "plugins" / "installed_plugins.json",
            ),
            plan_file_copy(
                "plugins/known_marketplaces.json",
                cdir / "plugins" / "known_marketplaces.json",
                stored / "plugins" / "known_marketplaces.json",
            ),
            self._plan_mcp_from(stored),
        ]
        changes.extend(self._installed_plugin_config_changes_from(stored))
        if (cdir / "CLAUDE.md").exists():
            changes.append(plan_file_copy("CLAUDE.md", cdir / "CLAUDE.md", stored / "CLAUDE.md"))
        for name in GLOBAL_RULE_DIRECTORIES:
            local_dir = cdir / name
            if local_dir.exists():
                changes.append(self._plan_tree_mirror(f"{name}/", local_dir, stored / name))
        return AppPlan(self.name, "from", changes, self.description)

    def plan_to(self, target_dir: Path) -> AppPlan:
        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        changes = [
            plan_file_copy("settings.json", stored / "settings.json", cdir / "settings.json"),
            plan_file_copy(
                "plugins/installed_plugins.json",
                stored / "plugins" / "installed_plugins.json",
                cdir / "plugins" / "installed_plugins.json",
            ),
            plan_file_copy(
                "plugins/known_marketplaces.json",
                stored / "plugins" / "known_marketplaces.json",
                cdir / "plugins" / "known_marketplaces.json",
            ),
            self._plan_mcp_to(stored),
        ]
        changes.extend(self._installed_plugin_config_changes_to(stored))
        if (stored / "CLAUDE.md").exists():
            changes.append(plan_file_copy("CLAUDE.md", stored / "CLAUDE.md", cdir / "CLAUDE.md"))
        for name in GLOBAL_RULE_DIRECTORIES:
            stored_dir = stored / name
            if stored_dir.exists():
                changes.append(self._plan_tree_mirror(f"{name}/", stored_dir, cdir / name))
        return AppPlan(self.name, "to", changes, self.description)

    def sync_from(self, target_dir: Path) -> None:
        ui.dim(f"source → {self._claude_dir()}")

        cdir = self._claude_dir()
        stored = self._stored(target_dir)
        (stored / "plugins").mkdir(parents=True, exist_ok=True)

        shutil.copy2(cdir / "settings.json", stored / "settings.json")
        ui.ok("settings.json")

        for fname in ("installed_plugins.json", "known_marketplaces.json"):
            shutil.copy2(cdir / "plugins" / fname, stored / "plugins" / fname)
            ui.ok(f"plugins/{fname}")

        cj = json.loads(self._claude_json().read_text())
        (stored / "mcp-servers.json").write_text(
            json.dumps(cj.get("mcpServers", {}), indent=2, ensure_ascii=False)
        )
        ui.ok("mcp-servers.json")

        for plugin_name in self._installed_plugin_names(stored / "plugins" / "installed_plugins.json"):
            src = cdir / "plugins" / plugin_name / "config.json"
            if src.exists():
                dst_dir = stored / "plugins" / plugin_name
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst_dir / "config.json")
                ui.ok(f"plugins/{plugin_name}/config.json")

        self._sync_from_global_rules(target_dir)

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        stored = self._stored(target_dir)
        if not (stored / "settings.json").exists():
            raise FileNotFoundError(f"{stored / 'settings.json'} not found (claude/settings.json missing)")

        cdir = self._claude_dir()
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "plugins").mkdir(parents=True, exist_ok=True)

        bdir = backup_dir / self.name
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "plugins").mkdir(parents=True, exist_ok=True)

        for src, rel in [
            (cdir / "settings.json", "settings.json"),
            (cdir / "plugins" / "installed_plugins.json", "plugins/installed_plugins.json"),
            (cdir / "plugins" / "known_marketplaces.json", "plugins/known_marketplaces.json"),
            (self._claude_json(), ".claude.json"),
        ]:
            if src.exists():
                dst = bdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        ui.dim(f"backup → {bdir}")

        shutil.copy2(stored / "settings.json", cdir / "settings.json")
        ui.ok("settings.json")
        shutil.copy2(stored / "plugins" / "installed_plugins.json",
                     cdir / "plugins" / "installed_plugins.json")
        ui.ok("plugins/installed_plugins.json")
        shutil.copy2(stored / "plugins" / "known_marketplaces.json",
                     cdir / "plugins" / "known_marketplaces.json")
        ui.ok("plugins/known_marketplaces.json")

        claude_json_path = self._claude_json()
        try:
            cj = json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
        except json.JSONDecodeError as e:
            raise RuntimeError(f"~/.claude.json is corrupted: {e}") from e
        try:
            cj["mcpServers"] = json.loads((stored / "mcp-servers.json").read_text())
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{stored / 'mcp-servers.json'} is corrupted: {e}") from e
        claude_json_path.write_text(json.dumps(cj, indent=2, ensure_ascii=False))
        ui.ok("mcp-servers.json → ~/.claude.json")

        for plugin_name in self._installed_plugin_names(stored / "plugins" / "installed_plugins.json"):
            src = stored / "plugins" / plugin_name / "config.json"
            if not src.exists():
                continue
            local_plugin_dir = cdir / "plugins" / plugin_name
            local_plugin_dir.mkdir(parents=True, exist_ok=True)
            local_cfg = local_plugin_dir / "config.json"
            if local_cfg.exists():
                bdst = bdir / "plugins" / plugin_name
                bdst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_cfg, bdst / "config.json")
            shutil.copy2(src, local_cfg)
            ui.ok(f"plugins/{plugin_name}/config.json")

        ui.divider("restore marketplaces · plugins")
        self._restore_plugins(stored)

        self._enforce_disabled(stored / "settings.json")

        self._sync_to_global_rules(target_dir, backup_dir)

        ui.dim("hint: restart Claude Code to pick up new plugins")

    def status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        cdir = self._claude_dir()
        pairs = [
            (cdir / "settings.json", stored / "settings.json"),
            (cdir / "plugins" / "installed_plugins.json", stored / "plugins" / "installed_plugins.json"),
            (cdir / "plugins" / "known_marketplaces.json", stored / "plugins" / "known_marketplaces.json"),
        ]
        base = diff_files(pairs)
        if base.state == "missing":
            return base
        local_cj = self._claude_json()
        stored_mcp = stored / "mcp-servers.json"
        if not local_cj.exists() or not stored_mcp.exists():
            return AppStatus(state="missing", details="mcp-servers.json")
        local_mcp = json.loads(local_cj.read_text()).get("mcpServers", {})
        stored_mcp_data = json.loads(stored_mcp.read_text())
        if local_mcp != stored_mcp_data:
            if base.state == "dirty":
                base = AppStatus(state="dirty", details=f"{base.details}, mcp-servers.json")
            else:
                base = AppStatus(state="dirty", details="mcp-servers.json")
        rules = self._diff_global_rules(target_dir)
        return self._merge_status(base, rules)

    @staticmethod
    def _installed_plugin_names(installed_plugins_path: Path) -> list[str]:
        if not installed_plugins_path.exists():
            return []
        data = json.loads(installed_plugins_path.read_text())
        plugins = data.get("plugins") or {}
        return sorted({k.split("@")[0] for k in plugins.keys()})

    def _restore_plugins(self, stored: Path) -> None:
        marketplaces = json.loads((stored / "plugins" / "known_marketplaces.json").read_text())
        installed_doc = json.loads((stored / "plugins" / "installed_plugins.json").read_text())
        plugins = installed_doc.get("plugins") or {}

        for mp_name, mp_meta in marketplaces.items():
            source = (mp_meta.get("source") or {})
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
        enabled_map = settings.get("enabledPlugins", {}) or {}
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

    def _run_claude_cli(self, args: list[str], desc: str, tolerate_already: bool = True) -> None:
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
