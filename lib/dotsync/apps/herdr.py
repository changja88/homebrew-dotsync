"""Herdr sync — user-authored config.toml only."""

from __future__ import annotations

from pathlib import Path

from dotsync.apps.base import App, FilePair


class HerdrApp(App):
    name = "herdr"
    description = "Herdr terminal workspace config (config.toml)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return cls._local_path().exists()

    @classmethod
    def _local_path(cls) -> Path:
        return Path.home() / ".config" / "herdr" / "config.toml"

    def tracked_files(self, target_dir: Path) -> list[FilePair]:
        return [
            FilePair(
                local=self._local_path(),
                stored=target_dir / self.name / "config.toml",
                label="config.toml",
            )
        ]
