"""Ghostty sync — single file config.ghostty"""
from __future__ import annotations
from pathlib import Path
from dotsync.apps.base import App, FilePair


class GhosttyApp(App):
    name = "ghostty"
    description = "Ghostty terminal config (config.ghostty)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return cls._local_path().exists()

    @classmethod
    def _local_path(cls) -> Path:
        return Path.home() / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config.ghostty"

    def tracked_files(self, target_dir: Path) -> list[FilePair]:
        return [FilePair(
            local=self._local_path(),
            stored=target_dir / self.name / "config.ghostty",
            label="config.ghostty",
        )]
