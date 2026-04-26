"""zsh sync — single file ~/.zshrc <-> <dir>/zsh/.zshrc"""
from __future__ import annotations
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, FilePair


class ZshApp(App):
    name = "zsh"
    description = "Zsh shell config (~/.zshrc)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return (Path.home() / ".zshrc").exists()

    def tracked_files(self, target_dir: Path) -> list[FilePair]:
        return [FilePair(
            local=Path.home() / ".zshrc",
            stored=target_dir / self.name / ".zshrc",
            label=".zshrc",
        )]

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        super().sync_to(target_dir, backup_dir)
        ui.dim("hint: open a new shell or `source ~/.zshrc`")
