"""Claude Code sync — settings, plugins, MCP servers, with plugin auto-restore."""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class ClaudeApp(App):
    name = "claude"
    description = "Claude Code (settings + plugins + MCP servers)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return (Path.home() / ".claude" / "settings.json").exists()

    def _claude_dir(self) -> Path:
        return Path.home() / ".claude"

    def _claude_json(self) -> Path:
        return Path.home() / ".claude.json"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

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
        cj = json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
        cj["mcpServers"] = json.loads((stored / "mcp-servers.json").read_text())
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
        if base.state in ("missing", "dirty"):
            return base
        local_cj = self._claude_json()
        stored_mcp = stored / "mcp-servers.json"
        if not local_cj.exists() or not stored_mcp.exists():
            return AppStatus(state="missing", details="mcp-servers.json")
        local_mcp = json.loads(local_cj.read_text()).get("mcpServers", {})
        stored_mcp_data = json.loads(stored_mcp.read_text())
        if local_mcp != stored_mcp_data:
            return AppStatus(state="dirty", details="mcp-servers.json")
        return base

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

    @staticmethod
    def _run_claude_cli(args: list[str], desc: str, tolerate_already: bool = True) -> None:
        result = subprocess.run(["claude", *args], capture_output=True, text=True)
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
        else:
            ui.warn(f"{desc} failed: {stderr or 'unknown'}")
