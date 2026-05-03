"""Codex CLI sync — user-authored settings only."""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class CodexApp(App):
    name = "codex"
    description = "Codex CLI settings (config.toml + AGENTS.md)"

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
    def _agents_path(cls) -> Path:
        return cls._codex_dir() / "AGENTS.md"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    def sync_from(self, target_dir: Path) -> None:
        stored = self._stored(target_dir)
        local_config = self._config_path()
        if not local_config.exists():
            raise FileNotFoundError(f"{local_config} not found (config.toml missing)")

        stored.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_config, stored / "config.toml")
        ui.sub("config.toml")

        local_agents = self._agents_path()
        if local_agents.exists():
            shutil.copy2(local_agents, stored / "AGENTS.md")
            ui.sub("AGENTS.md")
        else:
            stored_agents = stored / "AGENTS.md"
            if stored_agents.exists():
                stored_agents.unlink()
                ui.dim("removed stale AGENTS.md")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        stored = self._stored(target_dir)
        stored_config = stored / "config.toml"
        if not stored_config.exists():
            raise FileNotFoundError(f"{stored_config} not found (codex/config.toml missing)")

        local_dir = self._codex_dir()
        local_dir.mkdir(parents=True, exist_ok=True)

        local_config = self._config_path()
        if local_config.exists():
            bdst = backup_dir / self.name / "config.toml"
            bdst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_config, bdst)
            ui.dim(f"backup → {bdst}")
        shutil.copy2(stored_config, local_config)
        ui.sub("config.toml")

        stored_agents = stored / "AGENTS.md"
        if not stored_agents.exists():
            return

        local_agents = self._agents_path()
        if local_agents.exists():
            bdst = backup_dir / self.name / "AGENTS.md"
            bdst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_agents, bdst)
            ui.dim(f"backup → {bdst}")
        shutil.copy2(stored_agents, local_agents)
        ui.sub("AGENTS.md")

    def status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        pairs = [
            (self._config_path(), stored / "config.toml"),
        ]
        local_agents = self._agents_path()
        stored_agents = stored / "AGENTS.md"
        if local_agents.exists() or stored_agents.exists():
            pairs.append((local_agents, stored_agents))
        return diff_files(pairs)
