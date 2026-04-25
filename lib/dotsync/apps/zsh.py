"""zsh sync — single file ~/.zshrc <-> <dir>/zsh/.zshrc"""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class ZshApp(App):
    name = "zsh"
    description = "Zsh shell config (~/.zshrc)"

    def _local(self) -> Path:
        return Path.home() / ".zshrc"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / ".zshrc"

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"sync: local → folder [{self.name}]")
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (.zshrc missing)")
        ui.sub(f"source: {src}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok(".zshrc")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"sync: folder → local [{self.name}]")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (zsh/.zshrc missing)")
        local = self._local()
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / ".zshrc")
            ui.sub(f"backup: {backup_dir / self.name / '.zshrc'}")
        shutil.copy2(src, local)
        ui.ok(".zshrc")
        ui.done("done. open a new shell or run 'source ~/.zshrc'.")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
