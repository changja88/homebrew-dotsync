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
        ui.step(f"동기화: 로컬 → 폴더 [{self.name}]")
        src = self._local()
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (.zshrc 미존재)")
        ui.sub(f"소스: {src}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ui.ok(".zshrc")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"동기화: 폴더 → 로컬 [{self.name}]")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} 없음 (zsh/.zshrc 미존재)")
        local = self._local()
        if local.exists():
            (backup_dir / self.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup_dir / self.name / ".zshrc")
            ui.sub(f"백업: {backup_dir / self.name / '.zshrc'}")
        shutil.copy2(src, local)
        ui.ok(".zshrc")
        ui.done("완료. 새 쉘을 열거나 'source ~/.zshrc' 실행하세요.")

    def status(self, target_dir: Path) -> AppStatus:
        return diff_files([(self._local(), self._stored(target_dir))])
