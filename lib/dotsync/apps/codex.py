"""Codex CLI sync — user-authored settings, instructions, rules, and skills."""
from __future__ import annotations
import shutil
from pathlib import Path
from dotsync import ui
from dotsync.apps.base import App, AppStatus, diff_files, _hash
from dotsync.plan import AppPlan, plan_file_copy, plan_tree_mirror

OPTIONAL_FILES = ("AGENTS.md", "AGENTS.override.md", "hooks.json", "requirements.toml")
OPTIONAL_DIRECTORIES = ("rules", "skills")
SKILL_IGNORED_TOP_DIRS = (".system",)


class CodexApp(App):
    name = "codex"
    description = "Codex CLI settings (config + global instructions + user rules/skills)"

    @classmethod
    def is_present_locally(cls) -> bool:
        return cls._config_path().exists()

    @classmethod
    def _codex_dir(cls) -> Path:
        return Path.home() / ".codex"

    @classmethod
    def _config_path(cls) -> Path:
        return cls._codex_dir() / "config.toml"

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    @staticmethod
    def _ignored_top_dirs(name: str) -> tuple[str, ...]:
        return SKILL_IGNORED_TOP_DIRS if name == "skills" else ()

    @staticmethod
    def _is_ignored_rel(rel: Path, ignored_top_dirs: tuple[str, ...]) -> bool:
        return bool(rel.parts and rel.parts[0] in ignored_top_dirs)

    def _tree_files(self, root: Path, ignored_top_dirs: tuple[str, ...] = ()) -> set[Path]:
        if not root.exists():
            return set()
        return {
            f.relative_to(root)
            for f in root.rglob("*")
            if f.is_file() and not self._is_ignored_rel(f.relative_to(root), ignored_top_dirs)
        }

    def _diff_tree(
        self,
        local: Path,
        stored: Path,
        ignored_top_dirs: tuple[str, ...] = (),
    ) -> tuple[set[Path], set[Path], set[Path]]:
        """Return (added_in_stored, removed_in_stored, modified) relative paths."""
        local_files = self._tree_files(local, ignored_top_dirs)
        stored_files = self._tree_files(stored, ignored_top_dirs)
        added = stored_files - local_files
        removed = local_files - stored_files
        common = local_files & stored_files
        modified = {rel for rel in common if _hash(local / rel) != _hash(stored / rel)}
        return added, removed, modified

    def _mirror_tree(
        self,
        src: Path,
        dst: Path,
        ignored_top_dirs: tuple[str, ...] = (),
        *,
        purge_ignored_dst: bool = False,
    ) -> None:
        """Mirror managed files from src to dst, preserving ignored dst trees."""
        dst.mkdir(parents=True, exist_ok=True)
        src_rels = self._tree_files(src, ignored_top_dirs)
        dst_rels = self._tree_files(dst, ignored_top_dirs)

        for rel in src_rels:
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, target)

        for rel in dst_rels - src_rels:
            (dst / rel).unlink()

        if purge_ignored_dst:
            for name in ignored_top_dirs:
                ignored = dst / name
                if ignored.is_dir():
                    shutil.rmtree(ignored)
                elif ignored.exists():
                    ignored.unlink()

        subdirs = sorted(
            (d for d in dst.rglob("*") if d.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for d in subdirs:
            rel = d.relative_to(dst)
            if self._is_ignored_rel(rel, ignored_top_dirs):
                continue
            try:
                d.rmdir()
            except OSError:
                pass

    def _backup_file(self, local: Path, backup_dir: Path, label: str) -> None:
        if not local.exists():
            return
        bdst = backup_dir / self.name / label
        bdst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, bdst)
        ui.dim(f"backup → {bdst}")

    def _backup_tree(self, local: Path, backup_dir: Path, label: str) -> None:
        if not local.exists():
            return
        bdst = backup_dir / self.name / label
        shutil.copytree(local, bdst, dirs_exist_ok=True)
        ui.dim(f"backup → {bdst}")

    @staticmethod
    def _merge_statuses(statuses: list[AppStatus]) -> AppStatus:
        if any(s.state == "missing" for s in statuses):
            missing = [s.details for s in statuses if s.state == "missing" and s.details]
            return AppStatus(state="missing", details=", ".join(missing))
        dirty = [s for s in statuses if s.state == "dirty"]
        if dirty:
            details = ", ".join(s.details for s in dirty if s.details)
            return AppStatus(state="dirty", details=details)
        return AppStatus(state="clean")

    def plan_from(self, target_dir: Path) -> AppPlan:
        stored = self._stored(target_dir)
        changes = [
            plan_file_copy("config.toml", self._config_path(), stored / "config.toml")
        ]
        for name in OPTIONAL_FILES:
            local_file = self._codex_dir() / name
            if local_file.exists():
                changes.append(plan_file_copy(name, local_file, stored / name))
        for name in OPTIONAL_DIRECTORIES:
            local_dir = self._codex_dir() / name
            if local_dir.exists():
                changes.append(
                    plan_tree_mirror(
                        f"{name}/",
                        local_dir,
                        stored / name,
                        self._ignored_top_dirs(name),
                    )
                )
        return AppPlan(self.name, "from", changes, self.description)

    def plan_to(self, target_dir: Path) -> AppPlan:
        stored = self._stored(target_dir)
        local_dir = self._codex_dir()
        changes = [
            plan_file_copy("config.toml", stored / "config.toml", self._config_path())
        ]
        for name in OPTIONAL_FILES:
            stored_file = stored / name
            if stored_file.exists():
                changes.append(plan_file_copy(name, stored_file, local_dir / name))
        for name in OPTIONAL_DIRECTORIES:
            stored_dir = stored / name
            if stored_dir.exists():
                changes.append(
                    plan_tree_mirror(
                        f"{name}/",
                        stored_dir,
                        local_dir / name,
                        self._ignored_top_dirs(name),
                    )
                )
        return AppPlan(self.name, "to", changes, self.description)

    def sync_from(self, target_dir: Path) -> None:
        stored = self._stored(target_dir)
        local_config = self._config_path()
        if not local_config.exists():
            raise FileNotFoundError(f"{local_config} not found (config.toml missing)")

        stored.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_config, stored / "config.toml")
        ui.sub("config.toml")

        for name in OPTIONAL_FILES:
            local_file = self._codex_dir() / name
            if local_file.exists():
                shutil.copy2(local_file, stored / name)
                ui.sub(name)

        for name in OPTIONAL_DIRECTORIES:
            local_dir = self._codex_dir() / name
            if local_dir.exists():
                ignored = self._ignored_top_dirs(name)
                self._mirror_tree(
                    local_dir,
                    stored / name,
                    ignored,
                    purge_ignored_dst=bool(ignored),
                )
                ui.sub(f"{name}/")

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        stored = self._stored(target_dir)
        stored_config = stored / "config.toml"
        if not stored_config.exists():
            raise FileNotFoundError(f"{stored_config} not found (codex/config.toml missing)")

        local_dir = self._codex_dir()
        local_dir.mkdir(parents=True, exist_ok=True)

        local_config = self._config_path()
        self._backup_file(local_config, backup_dir, "config.toml")
        shutil.copy2(stored_config, local_config)
        ui.sub("config.toml")

        for name in OPTIONAL_FILES:
            stored_file = stored / name
            if not stored_file.exists():
                continue
            local_file = local_dir / name
            self._backup_file(local_file, backup_dir, name)
            shutil.copy2(stored_file, local_file)
            ui.sub(name)

        for name in OPTIONAL_DIRECTORIES:
            stored_dir = stored / name
            if not stored_dir.exists():
                continue
            local_dir_for_name = local_dir / name
            self._backup_tree(local_dir_for_name, backup_dir, name)
            self._mirror_tree(
                stored_dir,
                local_dir_for_name,
                self._ignored_top_dirs(name),
            )
            ui.sub(f"{name}/")

    def status(self, target_dir: Path) -> AppStatus:
        stored = self._stored(target_dir)
        base = diff_files([(self._config_path(), stored / "config.toml")])
        if base.state == "missing":
            return base
        statuses = [base]

        optional_file_changes: list[str] = []
        for name in OPTIONAL_FILES:
            local_file = self._codex_dir() / name
            stored_file = stored / name
            if local_file.exists() and stored_file.exists():
                if _hash(local_file) != _hash(stored_file):
                    optional_file_changes.append(name)
            elif local_file.exists() or stored_file.exists():
                optional_file_changes.append(name)
        if optional_file_changes:
            statuses.append(AppStatus(state="dirty", details=", ".join(optional_file_changes)))

        flat_paths: list[str] = []
        summary_parts: list[tuple[str, int]] = []
        for name in OPTIONAL_DIRECTORIES:
            local_dir = self._codex_dir() / name
            stored_dir = stored / name
            added, removed, modified = self._diff_tree(
                local_dir,
                stored_dir,
                self._ignored_top_dirs(name),
            )
            count = len(added) + len(removed) + len(modified)
            if count > 0:
                for rel in sorted(added | removed | modified):
                    flat_paths.append(f"{name}/{rel}")
                summary_parts.append((f"{name}/", count))

        if flat_paths:
            details = (
                ", ".join(flat_paths)
                if len(flat_paths) <= 8
                else ", ".join(f"{label} ({n} changed)" for label, n in summary_parts)
            )
            statuses.append(AppStatus(state="dirty", details=details))

        return self._merge_statuses(statuses)
