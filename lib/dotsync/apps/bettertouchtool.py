"""BetterTouchTool sync via osascript export/import.

Multiple presets can be tracked at once — `presets` is a list of preset
names. sync_from / sync_to / status iterate every preset; the per-preset
file layout is ``<sync>/bettertouchtool/presets/<name>.bttpreset``.
"""
from __future__ import annotations
import sqlite3
import subprocess
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, _hash


class BetterTouchToolApp(App):
    name = "bettertouchtool"
    description = "BetterTouchTool presets (.bttpreset, names configurable)"

    APP_PATH = Path("/Applications/BetterTouchTool.app")
    DATA_DIR = Path.home() / "Library" / "Application Support" / "BetterTouchTool"

    @classmethod
    def discover_preset_names(cls) -> list[str]:
        """Best-effort enumeration of preset names from BTT's own SQLite store.
        Returns a sorted list. Returns [] on any failure (BTT not installed,
        DB missing, schema drift, db locked, etc.) so callers can fall back
        gracefully."""
        try:
            data_dir = Path(cls.DATA_DIR)
            if not data_dir.is_dir():
                return []
            candidates = [
                p for p in data_dir.glob("btt_data_store.version_*")
                if not p.name.endswith("-shm") and not p.name.endswith("-wal")
            ]
            if not candidates:
                return []
            db_path = max(candidates, key=lambda p: p.stat().st_mtime)
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                rows = conn.execute(
                    "SELECT ZNAME3 FROM ZBTTBASEENTITY "
                    "WHERE Z_ENT = (SELECT Z_ENT FROM Z_PRIMARYKEY "
                    "               WHERE Z_NAME = 'Preset') "
                    "  AND ZNAME3 IS NOT NULL AND ZNAME3 != ''"
                ).fetchall()
            return sorted({row[0] for row in rows})
        except Exception:
            return []

    @classmethod
    def is_present_locally(cls) -> bool:
        return Path(cls.APP_PATH).exists()

    def __init__(self, presets: list[str] | None = None):
        self.presets: list[str] = list(presets) if presets else ["Master_bt"]

    def _stored(self, target_dir: Path, preset: str) -> Path:
        return target_dir / self.name / "presets" / f"{preset}.bttpreset"

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
        for preset in self.presets:
            ui.dim(f"preset: {preset}")
            dst = self._stored(target_dir, preset)
            dst.parent.mkdir(parents=True, exist_ok=True)
            script = (
                f'tell application "BetterTouchTool" to export_preset '
                f'"{preset}" outputPath "{dst}" compress false includeSettings true'
            )
            self._osascript(script)
            if not dst.exists():
                raise RuntimeError(f"BTT export file was not created: {dst}")
            ui.sub(f"presets/{preset}.bttpreset")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        # First pass: verify every preset has a stored .bttpreset before
        # importing any of them — fail-fast keeps the local BTT state intact
        # if the sync folder is missing files.
        missing = [
            p for p in self.presets
            if not self._stored(target_dir, p).exists()
        ]
        if missing:
            first_missing = self._stored(target_dir, missing[0])
            raise FileNotFoundError(
                f"{first_missing} not found "
                f"(bettertouchtool/presets/.bttpreset missing for: {', '.join(missing)})"
            )

        for preset in self.presets:
            ui.dim(f"preset: {preset}")
            src = self._stored(target_dir, preset)
            backup_target = backup_dir / self.name / f"{preset}.bttpreset"
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            export_script = (
                f'tell application "BetterTouchTool" to export_preset '
                f'"{preset}" outputPath "{backup_target}" compress false includeSettings true'
            )
            try:
                self._osascript(export_script)
                ui.dim(f"backup → {backup_target}")
            except RuntimeError:
                ui.warn(f"existing preset backup failed for {preset} (continuing anyway)")

            import_script = (
                f'tell application "BetterTouchTool" to import_preset "{src}"'
            )
            self._osascript(import_script)
            ui.sub(f"presets/{preset}.bttpreset → BTT")
        ui.dim("hint: check BetterTouchTool to confirm the presets are active")

    def status(self, target_dir: Path) -> AppStatus:
        import tempfile
        # 1) any preset whose stored file is missing → state=missing
        missing = [
            p for p in self.presets
            if not self._stored(target_dir, p).exists()
        ]
        if missing:
            return AppStatus(
                state="missing",
                details=", ".join(f"{p}.bttpreset" for p in missing),
            )

        # 2) export each preset live and compare against stored bytes
        differs: list[str] = []
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for preset in self.presets:
                stored = self._stored(target_dir, preset)
                live = tmp / f"{preset}.live.bttpreset"
                export_script = (
                    f'tell application "BetterTouchTool" to export_preset '
                    f'"{preset}" outputPath "{live}" compress false includeSettings true'
                )
                try:
                    self._osascript(export_script)
                except RuntimeError:
                    return AppStatus(
                        state="unknown",
                        details="BTT not running — cannot diff live preset",
                    )
                if not live.exists():
                    return AppStatus(
                        state="unknown",
                        details="BTT export produced no file",
                    )
                if _hash(live) != _hash(stored):
                    differs.append(f"{preset}.bttpreset")

        if differs:
            return AppStatus(state="dirty", details=", ".join(differs))
        return AppStatus(state="clean")
