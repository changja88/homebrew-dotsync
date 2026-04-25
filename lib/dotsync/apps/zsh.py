"""zsh sync — single file ~/.zshrc <-> <dir>/zsh/.zshrc"""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files


class ZshApp(App):
    name = "zsh"
    description = "Zsh shell config (~/.zshrc)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return (Path.home() / ".zshrc").exists()

    def _local(self) -> Path:
        return Path.home() / ".zshrc"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / ".zshrc"

    def sync_from(self, target_dir: Path) -> None:
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (.zshrc missing)")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok(".zshrc")
        ui.dim(f"source → {src}")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (zsh/.zshrc missing)")
        local = self._local()
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / ".zshrc")
            ui.dim(f"backup → {backup_dir / self.name / '.zshrc'}")
        shutil.copy2(src, local)
        ui.ok(".zshrc")
        ui.dim("hint: open a new shell or `source ~/.zshrc`")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
