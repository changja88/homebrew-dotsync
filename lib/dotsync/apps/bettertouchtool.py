"""BetterTouchTool sync via osascript export/import.

Multiple presets can be tracked at once — `presets` is a list of preset
names. sync_from / sync_to / status iterate every preset; the per-preset
file layout is ``<sync>/bettertouchtool/presets/<name>.bttpreset``.
"""

from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import (
    App,
    AppStatus,
    ensure_not_symlink,
    ensure_path_within_root,
)
from dotsync.plan import AppPlan, Change

# BTT's `export_preset` AppleScript returns "done" the moment the command is
# accepted, but the actual file write happens asynchronously — empirically the
# preset shows up on disk ~10–50ms later. Without polling, sync_from races
# the export and raises "BTT export file was not created" on every run.
_EXPORT_WAIT_TIMEOUT = 5.0

# BTT regenerates BTTPresetUUID on every export_preset call, may rewrite
# BTTLastUpdatedAt during app updates or database migrations, records
# BTTLastUsed runtime history when triggers fire, may rewrite entity UUID/order
# metadata after imports, and emits the BTTPresetContent containers /
# BTTTriggers arrays in no guaranteed order — all without any user-visible
# preset content changing. A naive byte-for-byte hash would flag those cases as
# dirty, so we normalize volatile metadata and entity-collection order before
# hashing.
_BTT_UUID_LINE_RE = re.compile(
    r'^(\s*"BTTPresetUUID"\s*:\s*")[^"]+(",?\s*)$',
    re.MULTILINE,
)
_BTT_LAST_UPDATED_LINE_RE = re.compile(
    r'^(\s*"BTTLastUpdatedAt"\s*:\s*)[-+]?\d+(?:\.\d+)?(,?\s*)$',
    re.MULTILINE,
)
_VOLATILE_PRESET_KEYS = {
    "BTTPresetUUID",
    "BTTUUID",
    "BTTLastUpdatedAt",
    "BTTLastUsed",
    "BTTLastUsedAt",
}


def _is_trigger_entity(value: dict) -> bool:
    return "BTTTriggerType" in value or "BTTTriggerClass" in value


_UNORDERED_PRESET_LIST_KEYS = {"BTTPresetContent", "BTTTriggers"}


def _normalize_preset_data(value, *, key: str | None = None):
    if isinstance(value, dict):
        volatile_keys = set(_VOLATILE_PRESET_KEYS)
        if _is_trigger_entity(value):
            volatile_keys.add("BTTOrder")
        return {
            child_key: _normalize_preset_data(child, key=child_key)
            for child_key, child in value.items()
            if child_key not in volatile_keys
        }
    if isinstance(value, list):
        normalized = [_normalize_preset_data(item) for item in value]
        # export_preset emits entity collections (BTTPresetContent containers,
        # BTTTriggers) in no guaranteed order; user-visible ordering lives in
        # each entity's BTTOrder field, so array position carries no signal.
        if (
            key in _UNORDERED_PRESET_LIST_KEYS
            and normalized
            and all(isinstance(item, dict) for item in normalized)
        ):
            normalized.sort(
                key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)
            )
        return normalized
    return value


def _hash_preset(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        normalized = _BTT_UUID_LINE_RE.sub(r"\1<normalized>\2", text)
        normalized = _BTT_LAST_UPDATED_LINE_RE.sub(r"\1<normalized>\2", normalized)
    else:
        normalized = json.dumps(
            _normalize_preset_data(data),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class BetterTouchToolApp(App):
    name = "bettertouchtool"
    description = "BetterTouchTool presets (.bttpreset, names configurable)"

    APP_PATH = Path("/Applications/BetterTouchTool.app")
    DATA_DIR = Path.home() / "Library" / "Application Support" / "BetterTouchTool"

    @classmethod
    def from_config(cls, cfg) -> "BetterTouchToolApp":
        # Precedence: new app_options namespace > legacy bettertouchtool_presets field.
        # The legacy field is preserved for one release cycle so existing dotsync.toml
        # files keep working until users save through the new code path.
        opts = cfg.app_options.get(cls.name, {}) if hasattr(cfg, "app_options") else {}
        if "presets" in opts:
            return cls(presets=list(opts["presets"]))
        return cls(presets=cfg.bettertouchtool_presets)

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
                p
                for p in data_dir.glob("btt_data_store.version_*")
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

    DEFAULT_PRESETS: tuple[str, ...] = ("Master_bt",)

    @staticmethod
    def _validate_preset_name(preset: str) -> str:
        if not isinstance(preset, str) or not preset:
            raise ValueError("invalid BetterTouchTool preset name")
        if (
            preset in {".", ".."}
            or "/" in preset
            or "\\" in preset
            or '"' in preset
            or any(ord(ch) < 32 for ch in preset)
        ):
            raise ValueError(f"invalid BetterTouchTool preset name: {preset!r}")
        return preset

    @classmethod
    def _validated_presets(cls, presets: list[str]) -> list[str]:
        return [cls._validate_preset_name(preset) for preset in presets]

    @classmethod
    def extra_init_args(cls, parser) -> None:
        parser.add_argument(
            "--btt-presets",
            default=None,
            help=f"BetterTouchTool preset names, comma-separated (default: {','.join(cls.DEFAULT_PRESETS)})",
        )

    @classmethod
    def picker_annotation(cls, *, detected: bool) -> str | None:
        if not detected:
            return None
        count = len(cls.discover_preset_names())
        if count <= 0:
            return None
        return f"{count} preset" if count == 1 else f"{count} presets"

    @classmethod
    def resolve_options(
        cls,
        args,
        *,
        prev_apps: list[str],
        new_apps: list[str],
        interactive: bool,
    ) -> dict | None:
        # Not tracking BTT? leave options alone.
        if cls.name not in new_apps:
            return None
        # Explicit flag wins.
        flag_value = getattr(args, "btt_presets", None)
        if flag_value:
            presets = [p.strip() for p in flag_value.split(",") if p.strip()]
            return {"presets": cls._validated_presets(presets)}
        # Toggling BTT on (was-not, now-is) interactively → auto-discover.
        was = cls.name in prev_apps
        if interactive and not was:
            discovered = cls.discover_preset_names()
            if discovered:
                return {"presets": discovered}
        # No change requested.
        return None

    @classmethod
    def extra_config_subcommands(cls, subparser) -> None:
        p = subparser.add_parser(
            "btt-presets",
            help="set BetterTouchTool preset names (comma-separated)",
        )
        p.add_argument("presets", help="comma-separated names")

    @classmethod
    def handle_config_subcommand(cls, args, cfg) -> int | None:
        from dotsync import ui
        from dotsync.config import save_config

        if getattr(args, "cfg_cmd", None) != "btt-presets":
            return None
        raw_presets = [p.strip() for p in args.presets.split(",") if p.strip()]
        if not raw_presets:
            import sys

            print("provide at least one preset name", file=sys.stderr)
            return 2
        try:
            new_presets = cls._validated_presets(raw_presets)
        except ValueError as exc:
            import sys

            print(str(exc), file=sys.stderr)
            return 2
        cfg.app_options.setdefault(cls.name, {})["presets"] = new_presets
        save_config(cfg)
        ui.done(f"bettertouchtool presets = {new_presets}")
        return 0

    def __init__(self, presets: list[str] | None = None):
        super().__init__()
        selected = list(presets) if presets else ["Master_bt"]
        self.presets = self._validated_presets(selected)

    def _stored(self, target_dir: Path, preset: str) -> Path:
        return target_dir / self.name / "presets" / f"{preset}.bttpreset"

    @staticmethod
    def _applescript_string(value: str | Path) -> str:
        text = str(value)
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )
        return f'"{escaped}"'

    def _ensure_stored_path(self, path: Path, target_dir: Path, label: str) -> None:
        ensure_path_within_root(path, target_dir, label)

    def _ensure_stored_preset(self, target_dir: Path, preset: str) -> Path:
        stored = self._stored(target_dir, preset)
        self._ensure_stored_path(stored, target_dir, f"presets/{preset}.bttpreset")
        ensure_not_symlink(stored, f"presets/{preset}.bttpreset")
        return stored

    def _wait_for_export(self, path: Path, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (
            timeout if timeout is not None else _EXPORT_WAIT_TIMEOUT
        )
        interval = 0.02
        while True:
            if path.exists() and path.stat().st_size > 0:
                return True
            if time.monotonic() >= deadline:
                return path.exists() and path.stat().st_size > 0
            time.sleep(interval)
            interval = min(interval * 1.5, 0.2)

    def _osascript(self, script: str) -> None:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        out = (result.stdout or "").strip()
        if result.returncode != 0 or out != "done":
            raise RuntimeError(
                f"osascript failed (rc={result.returncode}): "
                f"stdout={out!r} stderr={result.stderr.strip()!r}. "
                f"Is BetterTouchTool running?"
            )

    def plan_from(self, target_dir: Path) -> AppPlan:
        status = self.status(target_dir)
        changes: list[Change] = []
        if status.state == "unknown":
            details = status.details or "cannot preview live BetterTouchTool presets"
            return AppPlan(
                self.name,
                "from",
                [Change("presets/", "unknown", details=details)],
                self.description,
            )
        dirty = {item.strip() for item in status.details.split(",") if item.strip()}
        for preset in self.presets:
            label = f"presets/{preset}.bttpreset"
            stored = self._stored(target_dir, preset)
            if status.state == "missing" or f"{preset}.bttpreset" in dirty:
                kind = "create" if not stored.exists() else "update"
            else:
                kind = "unchanged"
            changes.append(Change(label, kind, dest=stored))
        return AppPlan(self.name, "from", changes, self.description)

    def plan_to(self, target_dir: Path) -> AppPlan:
        # Mirror plan_from: ask status() whether each preset's live BTT state
        # already matches the stored bytes (UUID-normalized hash). Without
        # this, plan_to always reported "update" — even immediately after
        # sync_from — because BTT preset state lives in BTT's app DB, not on
        # disk, so a naive stored-exists check can never tell apart "needs
        # import" from "already in sync".
        status = self.status(target_dir)

        if status.state == "unknown":
            details = status.details or "cannot preview live BetterTouchTool presets"
            return AppPlan(
                self.name,
                "to",
                [Change("presets/", "unknown", details=details)],
                self.description,
            )

        dirty = {item.strip() for item in status.details.split(",") if item.strip()}
        changes: list[Change] = []
        for preset in self.presets:
            label = f"presets/{preset}.bttpreset"
            stored = self._stored(target_dir, preset)
            dest = Path(f"BetterTouchTool:{preset}")
            if not stored.exists():
                kind = "missing-source"
            elif status.state == "missing" or f"{preset}.bttpreset" in dirty:
                kind = "update"
            else:
                kind = "unchanged"
            changes.append(Change(label, kind, source=stored, dest=dest))
        return AppPlan(self.name, "to", changes, self.description)

    def sync_from(self, target_dir: Path) -> None:
        for preset in self.presets:
            ui.dim(f"preset: {preset}")
            dst = self._stored(target_dir, preset)
            self._ensure_stored_path(dst, target_dir, f"presets/{preset}.bttpreset")
            dst.parent.mkdir(parents=True, exist_ok=True)
            script = (
                f'tell application "BetterTouchTool" to export_preset '
                f"{self._applescript_string(preset)} outputPath "
                f"{self._applescript_string(dst)} compress false includeSettings true"
            )
            if dst.exists():
                ensure_not_symlink(dst, f"presets/{preset}.bttpreset")
                dst.unlink()
            self._osascript(script)
            if not self._wait_for_export(dst):
                raise RuntimeError(f"BTT export file was not created: {dst}")
            ui.sub(f"presets/{preset}.bttpreset")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        # First pass: verify every preset has a stored .bttpreset before
        # importing any of them — fail-fast keeps the local BTT state intact
        # if the sync folder is missing files.
        missing = [
            p
            for p in self.presets
            if not self._ensure_stored_preset(target_dir, p).exists()
        ]
        if missing:
            first_missing = self._stored(target_dir, missing[0])
            raise FileNotFoundError(
                f"{first_missing} not found "
                f"(bettertouchtool/presets/.bttpreset missing for: {', '.join(missing)})"
            )

        for preset in self.presets:
            ui.dim(f"preset: {preset}")
            src = self._ensure_stored_preset(target_dir, preset)
            backup_target = backup_dir / self.name / f"{preset}.bttpreset"
            ensure_path_within_root(backup_target, backup_dir, f"{preset}.bttpreset")
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            export_script = (
                f'tell application "BetterTouchTool" to export_preset '
                f"{self._applescript_string(preset)} outputPath "
                f"{self._applescript_string(backup_target)} compress false includeSettings true"
            )
            if backup_target.exists():
                backup_target.unlink()
            try:
                self._osascript(export_script)
                if self._wait_for_export(backup_target):
                    ui.dim(f"backup → {backup_target}")
                else:
                    ui.warn(
                        f"existing preset backup file did not appear for {preset} (continuing anyway)"
                    )
            except RuntimeError:
                ui.warn(
                    f"existing preset backup failed for {preset} (continuing anyway)"
                )

            import_script = (
                f'tell application "BetterTouchTool" to import_preset '
                f"{self._applescript_string(src)}"
            )
            self._osascript(import_script)
            ui.sub(f"presets/{preset}.bttpreset → BTT")
        ui.dim("hint: check BetterTouchTool to confirm the presets are active")

    def status(self, target_dir: Path) -> AppStatus:
        import tempfile

        # 1) any preset whose stored file is missing → state=missing
        missing = [
            p
            for p in self.presets
            if not self._ensure_stored_preset(target_dir, p).exists()
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
                stored = self._ensure_stored_preset(target_dir, preset)
                live = tmp / f"{preset}.live.bttpreset"
                export_script = (
                    f'tell application "BetterTouchTool" to export_preset '
                    f"{self._applescript_string(preset)} outputPath "
                    f"{self._applescript_string(live)} compress false includeSettings true"
                )
                try:
                    self._osascript(export_script)
                except RuntimeError:
                    return AppStatus(
                        state="unknown",
                        details="BTT not running — cannot diff live preset",
                    )
                if not self._wait_for_export(live):
                    return AppStatus(
                        state="unknown",
                        details="BTT export produced no file",
                    )
                if _hash_preset(live) != _hash_preset(stored):
                    differs.append(f"{preset}.bttpreset")

        if differs:
            return AppStatus(state="dirty", details=", ".join(differs))
        return AppStatus(state="clean")
