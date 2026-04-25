"""BetterTouchTool sync via osascript export/import."""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App


class BetterTouchToolApp(App):
    name = "bettertouchtool"
    description = "BetterTouchTool preset (.bttpreset, name configurable)"

    def __init__(self, preset: str = "Master_bt"):
        self.preset = preset

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name / "presets" / f"{self.preset}.bttpreset"

    def _osascript(self, script: str) -> None:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
        )
        out = (result.stdout or "").strip()
        if result.returncode != 0 or out != "done":
            raise RuntimeError(
                f"osascript failed (rc={result.returncode}): "
                f"stdout={out!r} stderr={result.stderr.strip()!r}. "
                f"Is BetterTouchTool running?"
            )

    def sync_from(self, target_dir: Path) -> None:
        ui.step(f"sync: local → folder [{self.name}]")
        ui.sub(f"preset: {self.preset}")
        dst = self._stored(target_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        script = (
            f'tell application "BetterTouchTool" to export_preset '
            f'"{self.preset}" outputPath "{dst}" compress false includeSettings true'
        )
        self._osascript(script)
        if not dst.exists():
            raise RuntimeError(f"BTT export file was not created: {dst}")
        ui.ok(f"presets/{self.preset}.bttpreset")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        ui.step(f"sync: folder → local [{self.name}]")
        ui.sub(f"preset: {self.preset}")
        src = self._stored(target_dir)
        if not src.exists():
            raise FileNotFoundError(f"{src} not found (bettertouchtool/presets/.bttpreset missing)")
        # backup current preset by re-exporting from BTT
        backup_target = backup_dir / self.name / f"{self.preset}.bttpreset"
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        export_script = (
            f'tell application "BetterTouchTool" to export_preset '
            f'"{self.preset}" outputPath "{backup_target}" compress false includeSettings true'
        )
        try:
            self._osascript(export_script)
            ui.sub(f"backup: {backup_target}")
        except RuntimeError:
            ui.warn("existing preset backup failed (continuing anyway)")

        import_script = (
            f'tell application "BetterTouchTool" to import_preset "{src}"'
        )
        self._osascript(import_script)
        ui.ok(f"presets/{self.preset}.bttpreset → BTT")
        ui.done("check BetterTouchTool to confirm the preset is active.")
