"""zsh sync — single file ~/.zshrc <-> <dir>/zsh/.zshrc

The marked `export DOTSYNC_DIR=…` block (shellrc.MARKER + its export line)
belongs to this Mac: comparisons ignore it and a pull keeps this Mac's own
block, so pulling another Mac's ~/.zshrc can never point dotsync at a
different sync folder.
"""

from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import (
    App,
    AppStatus,
    FilePair,
    ensure_not_symlink,
    ensure_path_within_root,
)
from dotsync.diffinfo import summarize_texts
from dotsync.plan import AppPlan, Change
from dotsync.shellrc import _write_atomic, strip_marked_block, with_local_marked_block


class ZshApp(App):
    name = "zsh"
    description = "Zsh shell config (~/.zshrc)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return (Path.home() / ".zshrc").exists()

    def tracked_files(self, target_dir: Path) -> list[FilePair]:
        return [
            FilePair(
                local=Path.home() / ".zshrc",
                stored=target_dir / self.name / ".zshrc",
                label=".zshrc",
            )
        ]

    def _same_without_block(self, pair: FilePair) -> bool:
        try:
            local = pair.local.read_text()
            stored = pair.stored.read_text()
        except (OSError, UnicodeDecodeError):
            return False
        return strip_marked_block(local) == strip_marked_block(stored)

    def status(self, target_dir: Path) -> AppStatus:
        status = super().status(target_dir)
        if status.state == "dirty" and self._same_without_block(self.tracked_files(target_dir)[0]):
            return AppStatus(state="clean")
        return status

    def _ignore_block(self, plan: AppPlan, target_dir: Path) -> AppPlan:
        pair = self.tracked_files(target_dir)[0]
        changes = [
            Change(label=c.label, kind="unchanged", source=c.source, dest=c.dest)
            if c.kind == "update" and self._same_without_block(pair)
            else c
            for c in plan.changes
        ]
        return AppPlan(app=plan.app, direction=plan.direction, changes=changes, description=plan.description)

    def plan_from(self, target_dir: Path) -> AppPlan:
        return self._ignore_block(super().plan_from(target_dir), target_dir)

    def plan_to(self, target_dir: Path) -> AppPlan:
        plan = self._ignore_block(super().plan_to(target_dir), target_dir)
        return AppPlan(
            app=plan.app,
            direction=plan.direction,
            changes=[self._as_pulled(c) for c in plan.changes],
            description=plan.description,
        )

    def _as_pulled(self, change: Change) -> Change:
        """An update shows what the pull writes — the folder's copy with this
        Mac's own block — not the folder's copy as it is."""
        if change.kind != "update" or change.source is None or change.dest is None:
            return change
        try:
            local = change.dest.read_text()
            pulled = with_local_marked_block(change.source.read_text(), local)
        except (OSError, UnicodeDecodeError):
            return change
        return replace(change, details=summarize_texts(local, pulled, ""), new_text=pulled)

    def sync_to(self, target_dir: Path) -> None:
        pair = self.tracked_files(target_dir)[0]
        if not pair.stored.exists():
            raise FileNotFoundError(f"{pair.stored} not found ({self.name}/{pair.label} missing)")
        ensure_path_within_root(pair.stored, target_dir, pair.label)
        ensure_not_symlink(pair.stored, pair.label)
        ensure_not_symlink(pair.local, pair.label)
        local_text = pair.local.read_text() if pair.local.exists() else ""
        _write_atomic(pair.local, with_local_marked_block(pair.stored.read_text(), local_text))
        ui.sub(pair.label)
        ui.dim("hint: open a new shell or `source ~/.zshrc`")
