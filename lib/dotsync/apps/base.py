"""Abstract base for app sync modules."""

from __future__ import annotations
import hashlib
import shutil
import subprocess
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Tuple

from dotsync.plan import AppPlan, plan_file_copy

StatusState = Literal["clean", "dirty", "missing", "unknown"]


@dataclass
class AppStatus:
    state: StatusState
    details: str = ""
    direction: str = ""  # "local-newer" | "folder-newer" | "diverged" | ""


@dataclass
class FilePair:
    """One (local, stored) pair an app tracks for sync.

    `local` is the canonical on-machine path; `stored` is the path inside
    target_dir/<app.name>/. `label` is what shows up in ui.sub() output.
    """

    local: Path
    stored: Path
    label: str


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def ensure_not_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"{path} is a symlink ({label}); refusing to sync symlinks")


def ensure_path_within_root(path: Path, root: Path, label: str) -> None:
    root_abs = root.absolute()
    path_abs = path.absolute()
    try:
        rel = path_abs.relative_to(root_abs)
    except ValueError as exc:
        raise RuntimeError(f"{path} is outside {root} ({label})") from exc

    root_real = root.resolve()
    path_real = path.resolve()
    if path_real != root_real and root_real not in path_real.parents:
        raise RuntimeError(f"{path} escapes {root} via symlink ({label})")

    current = root_abs
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(
                f"{current} is a symlink ({label}); refusing to sync symlinks"
            )


def ensure_directory(path: Path, label: str, *, root: Path | None = None) -> None:
    if root is not None:
        ensure_path_within_root(path, root, label)
    if not path.exists() and not path.is_symlink():
        return
    ensure_not_symlink(path, label)
    if not path.is_dir():
        raise RuntimeError(f"{path} is not a directory ({label})")


def copy_file_safely(
    source: Path,
    dest: Path,
    label: str,
    *,
    source_root: Path | None = None,
    dest_root: Path | None = None,
) -> None:
    if source_root is not None:
        ensure_path_within_root(source, source_root, label)
    if dest_root is not None:
        ensure_path_within_root(dest, dest_root, label)
    ensure_not_symlink(source, label)
    ensure_not_symlink(dest, label)
    shutil.copy2(source, dest)


def write_text_safely(
    dest: Path,
    text: str,
    label: str,
    *,
    dest_root: Path | None = None,
) -> None:
    if dest_root is not None:
        ensure_path_within_root(dest, dest_root, label)
    ensure_not_symlink(dest, label)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def diff_files(pairs: Iterable[Tuple[Path, Path]]) -> AppStatus:
    """Compare (local, stored) file pairs by sha256, with a mtime-based direction hint.

    Returns:
      missing — at least one side absent
      dirty   — every file present but at least one pair differs (with direction)
      clean   — every pair byte-identical
    """
    pairs = list(pairs)
    if not pairs:
        return AppStatus(state="unknown")
    missing: list[str] = []
    differs: list[Tuple[Path, Path, str]] = []  # (local, stored, name)
    for local, stored in pairs:
        if local.is_symlink():
            return AppStatus(state="unknown", details=f"{local.name} is a symlink")
        if stored.is_symlink():
            return AppStatus(state="unknown", details=f"{stored.name} is a symlink")
        if not local.exists() or not stored.exists():
            missing.append(local.name)
            continue
        if _hash(local) != _hash(stored):
            differs.append((local, stored, local.name))
    if missing:
        return AppStatus(state="missing", details=", ".join(missing))
    if differs:
        local_newer = sum(
            1
            for local, stored, _ in differs
            if local.stat().st_mtime > stored.stat().st_mtime
        )
        folder_newer = len(differs) - local_newer
        if local_newer and folder_newer:
            direction = "diverged"
        elif local_newer:
            direction = "local-newer"
        else:
            direction = "folder-newer"
        return AppStatus(
            state="dirty",
            details=", ".join(name for _, _, name in differs),
            direction=direction,
        )
    return AppStatus(state="clean")


class App(ABC):
    """One concrete subclass per supported app."""

    name: str = ""  # short id, must match config and dir/<name>/
    description: str = ""  # human-readable

    @classmethod
    def from_config(cls, cfg) -> "App":
        """Construct a configured instance from `cfg` (a dotsync.config.Config).

        Default: zero-arg construction. Apps that need to read their options
        from cfg (e.g. BetterTouchToolApp reading preset names) override this.
        """
        return cls()

    @classmethod
    def is_present_locally(cls) -> bool:
        """Return True if this app is detected as installed on this machine.

        Default: False. Concrete apps override; init's auto-detection skips
        any app that returns False here.
        """
        return False

    def __init__(self) -> None:
        # Per-instance accumulator for non-fatal warnings (failed external
        # process calls in fail_mode="warn", etc.). cli's summary surface
        # reads this after each sync so partial failures aren't silenced.
        self.warnings: list[str] = []

    def _note_skipped_symlink(self, label: str) -> None:
        """Record (once) that a symlinked entry was left alone, and say so."""
        from dotsync import ui

        msg = f"{label} is a symlink; skipped"
        if msg in self.warnings:
            return
        self.warnings.append(msg)
        ui.warn(msg)

    def _run_external(
        self,
        cmd: list[str],
        *,
        desc: str,
        fail_mode: Literal["warn", "raise"] = "warn",
    ) -> subprocess.CompletedProcess[str]:
        """Run an external command, with a uniform failure policy.

        - `desc` is the human label shown in warnings/errors.
        - fail_mode="warn": on rc!=0 append a warning to self.warnings, return
          the CompletedProcess so the caller can react. Use for best-effort
          plugin restoration, optional backups, etc.
        - fail_mode="raise": on rc!=0 raise RuntimeError. Use when the failure
          should abort the whole app's sync.
        """
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result
        msg = (
            f"{desc} failed (rc={result.returncode}): "
            f"{(result.stderr or '').strip() or 'no stderr'}"
        )
        if fail_mode == "raise":
            raise RuntimeError(msg)
        self.warnings.append(msg)
        return result

    def tracked_files(self, target_dir: Path) -> list["FilePair"]:
        """Declare the (local, stored) file pairs this app tracks.

        Default: []. Apps with simple file-based sync override this and rely
        on the default sync_from/sync_to/status (added in Phase 4.2); apps with
        non-file resources (claude plugins, BTT presets) may return [] and
        override the sync methods directly, OR mix declarative tracked_files
        with overridden sync methods that call super().
        """
        return []

    def plan_from(self, target_dir: Path) -> AppPlan:
        """Plan local app config -> sync folder for declarative file apps."""
        pairs = self.tracked_files(target_dir)
        changes = [
            plan_file_copy(pair.label, pair.local, pair.stored, dest_root=target_dir)
            for pair in pairs
        ]
        return AppPlan(
            app=self.name,
            direction="from",
            changes=changes or [],
            description=self.description,
        )

    def plan_to(self, target_dir: Path) -> AppPlan:
        """Plan sync folder -> local app config for declarative file apps."""
        pairs = self.tracked_files(target_dir)
        changes = [
            plan_file_copy(pair.label, pair.stored, pair.local, source_root=target_dir)
            for pair in pairs
        ]
        return AppPlan(
            app=self.name,
            direction="to",
            changes=changes or [],
            description=self.description,
        )

    def sync_from(self, target_dir: Path) -> None:
        """Local app config → target_dir/<self.name>/

        Default: walk tracked_files(), copy each .local → .stored. Apps that
        need extra behavior (network calls, file transformation) override this.
        """
        from dotsync import ui

        pairs = self.tracked_files(target_dir)
        if not pairs:
            raise NotImplementedError(
                f"{type(self).__name__} declares no tracked_files and does not "
                f"override sync_from"
            )
        for pair in pairs:
            if not pair.local.exists():
                raise FileNotFoundError(
                    f"{pair.local} not found ({pair.label} missing)"
                )
            ensure_path_within_root(pair.stored, target_dir, pair.label)
            pair.stored.parent.mkdir(parents=True, exist_ok=True)
            copy_file_safely(pair.local, pair.stored, pair.label, dest_root=target_dir)
            ui.sub(pair.label)

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        """target_dir/<self.name>/ → local app config, after backing up local.

        Default: walk tracked_files(), for each pair (a) verify .stored exists,
        (b) if .local exists copy it to backup_dir/<self.name>/<label>, (c)
        copy .stored over .local.
        """
        from dotsync import ui

        pairs = self.tracked_files(target_dir)
        if not pairs:
            raise NotImplementedError(
                f"{type(self).__name__} declares no tracked_files and does not "
                f"override sync_to"
            )
        # Fail-fast: verify every stored side exists before mutating any local file.
        missing = [p for p in pairs if not p.stored.exists()]
        if missing:
            first = missing[0]
            raise FileNotFoundError(
                f"{first.stored} not found ({self.name}/{first.label} missing)"
            )
        for pair in pairs:
            ensure_path_within_root(pair.stored, target_dir, pair.label)
        for pair in pairs:
            pair.local.parent.mkdir(parents=True, exist_ok=True)
            if pair.local.exists():
                bdst = backup_dir / self.name / pair.label
                ensure_path_within_root(bdst, backup_dir, pair.label)
                bdst.parent.mkdir(parents=True, exist_ok=True)
                copy_file_safely(pair.local, bdst, pair.label, dest_root=backup_dir)
                ui.dim(f"backup → {bdst}")
            copy_file_safely(
                pair.stored, pair.local, pair.label, source_root=target_dir
            )
            ui.sub(pair.label)

    def status(self, target_dir: Path) -> AppStatus:
        """Default: diff_files over tracked_files. Apps with non-file state
        (BTT live exports, claude .claude.json mcp comparison) override."""
        pairs = self.tracked_files(target_dir)
        if not pairs:
            return AppStatus(state="unknown")
        for pair in pairs:
            try:
                ensure_path_within_root(pair.stored, target_dir, pair.label)
            except RuntimeError as exc:
                return AppStatus(state="unknown", details=str(exc))
        return diff_files((p.local, p.stored) for p in pairs)

    # ----- CLI extension hooks --------------------------------------------
    # An App that needs CLI customization (extra flags on `init`, picker
    # annotations, config subcommands) overrides these. Defaults are no-ops
    # so apps without customization stay free of CLI plumbing.

    @classmethod
    def extra_init_args(cls, parser) -> None:
        """Optionally add app-specific argparse args to `dotsync init`.
        E.g. BetterTouchTool adds --btt-presets here."""
        return None

    @classmethod
    def picker_annotation(cls, *, detected: bool) -> str | None:
        """Right-side annotation shown next to this app's row in the picker.
        Return None for no annotation."""
        return None

    @classmethod
    def resolve_options(
        cls,
        args,
        *,
        prev_apps: list[str],
        new_apps: list[str],
        interactive: bool,
    ) -> dict | None:
        """Compute this app's options dict (the value stored under
        cfg.app_options[cls.name]) given init-time inputs.

        - args: argparse Namespace (may carry app-specific flags).
        - prev_apps / new_apps: tracked apps before/after the picker, for
          apps that re-discover state when toggled on (BTT does this).
        - interactive: True for interactive init, False for --yes / scripted.

        Return None to leave app_options[cls.name] unchanged.
        """
        return None

    @classmethod
    def extra_config_subcommands(cls, subparser) -> None:
        """Optionally register `dotsync config <name>-...` subcommands."""
        return None

    @classmethod
    def handle_config_subcommand(cls, args, cfg) -> int | None:
        """If the dispatch matched a subcommand registered above, mutate cfg
        and return an exit code. Return None if not a match."""
        return None

    # ----- per-app section finishers --------------------------------------
    # Both finishers exist so per-app sections close with a uniform line —
    # the cli's per-app loop never has to special-case which marker to draw.

    def _finish_ok(self) -> None:
        """Close a per-app sync section with a green ✓ done line."""
        from dotsync import ui

        ui.ok("done")

    def _finish_unchanged(self) -> None:
        """Close a per-app sync section with a dim 'unchanged' line — used
        by `dotsync apply` when local already matches stored, so the user can
        see at a glance which apps did vs. didn't move."""
        from dotsync import ui

        ui.dim("unchanged")
