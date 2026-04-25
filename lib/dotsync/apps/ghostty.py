"""Ghostty sync — single file config.ghostty"""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class GhosttyApp(App):
    name = "ghostty"
    description = "Ghostty terminal config (config.ghostty)"

    def _local_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "com.mitchellh.ghostty"

    def _local(self) -> Path:
        return self._local_dir() / "config.ghostty"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / "config.ghostty"

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"동기화: 로컬 → 폴더 [{self.name}]")
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (config.ghostty 미존재)")
        ui.sub(f"소스: {src}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok("config.ghostty")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"동기화: 폴더 → 로컬 [{self.name}]")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (ghostty/config.ghostty 미존재)")
        local = self._local()
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / "config.ghostty")
            ui.sub(f"백업: {backup_dir / self.name / 'config.ghostty'}")
        shutil.copy2(src, local)
        ui.ok("config.ghostty")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
