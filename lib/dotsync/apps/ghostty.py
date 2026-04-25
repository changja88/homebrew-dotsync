"""Ghostty sync — single file config.ghostty"""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class GhosttyApp(App):
    name = "ghostty"
    description = "Ghostty terminal config (config.ghostty)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return (Path.home() / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config.ghostty").exists()

    def _local_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "com.mitchellh.ghostty"

    def _local(self) -> Path:
        return self._local_dir() / "config.ghostty"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / "config.ghostty"

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"sync: local → folder [{self.name}]")
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (config.ghostty missing)")
        ui.sub(f"source: {src}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok("config.ghostty")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"sync: folder → local [{self.name}]")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (ghostty/config.ghostty missing)")
        local = self._local()
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / "config.ghostty")
            ui.sub(f"backup: {backup_dir / self.name / 'config.ghostty'}")
        shutil.copy2(src, local)
        ui.ok("config.ghostty")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
