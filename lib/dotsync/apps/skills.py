"""Agent skills installed with `npx skills` — record sources, reinstall on apply.

`npx skills add -g` keeps the canonical copy under `~/.agents/skills/<name>/`
and links it into each agent's skills dir. The global lock file records only
where a skill came from (and may hold a GitHub token), so dotsync writes its
own manifest: source + the managed agents that are linked to it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotsync import ui
from dotsync.apps.base import (
    App,
    AppStatus,
    copy_file_safely,
    ensure_directory,
    ensure_not_symlink,
    ensure_path_within_root,
    write_text_safely,
)
from dotsync.plan import AppPlan, Change, plan_file_copy

MANIFEST = "skills.json"
LOCK = ".skill-lock.json"
REINSTALLABLE_SOURCE_TYPES = ("github",)


class SkillsApp(App):
    name = "skills"
    description = "Agent skills installed with npx skills (~/.agents)"

    @classmethod
    def is_present_locally(cls) -> bool:
        try:
            return cls._lock_path().is_file()
        except OSError:
            return False

    @classmethod
    def _agents_dir(cls) -> Path:
        return Path.home() / ".agents"

    @classmethod
    def _lock_path(cls) -> Path:
        return cls._agents_dir() / LOCK

    @classmethod
    def _canonical_dir(cls, skill: str) -> Path:
        return cls._agents_dir() / "skills" / skill

    @classmethod
    def _managed_agent_dirs(cls) -> dict[str, Path]:
        home = Path.home()
        return {
            "claude-code": home / ".claude" / "skills",
            "codex": home / ".codex" / "skills",
        }

    def _stored(self, target_dir: Path) -> Path:
        return target_dir / self.name

    # ----- manifest -------------------------------------------------------

    def _linked_agents(self, skill: str) -> list[str]:
        canonical = self._canonical_dir(skill).resolve()
        agents = []
        for agent, skills_dir in self._managed_agent_dirs().items():
            link = skills_dir / skill
            if link.is_symlink() and link.resolve() == canonical:
                agents.append(agent)
        return sorted(agents)

    def _local_manifest(self) -> dict[str, dict[str, Any]]:
        lock = self._lock_path()
        ensure_not_symlink(lock, LOCK)
        if not lock.is_file():
            return {}
        try:
            doc = json.loads(lock.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{lock} is invalid JSON: {exc}") from exc
        skills = doc.get("skills") if isinstance(doc, dict) else None
        if not isinstance(skills, dict):
            raise RuntimeError(f"{lock} has no skills table")
        manifest: dict[str, dict[str, Any]] = {}
        for name, entry in skills.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                continue
            source_type = entry.get("sourceType")
            manifest[name] = {
                "source": entry["source"],
                "sourceType": source_type if isinstance(source_type, str) else "unknown",
                "agents": self._linked_agents(name),
            }
        return manifest

    @staticmethod
    def _manifest_text(manifest: dict[str, dict[str, Any]]) -> str:
        return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def _read_stored_manifest(self, target_dir: Path) -> dict[str, dict[str, Any]]:
        """Stored manifest, normalized; FileNotFoundError / RuntimeError when unusable."""
        path = self._stored(target_dir) / MANIFEST
        ensure_path_within_root(path, target_dir, MANIFEST)
        ensure_not_symlink(path, MANIFEST)
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found (skills/{MANIFEST} missing)")
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} is invalid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise RuntimeError(f"{path} must be a JSON object")
        manifest: dict[str, dict[str, Any]] = {}
        for name, entry in doc.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                raise RuntimeError(f"{path}: skill `{name}` needs a string source")
            agents = entry.get("agents", [])
            if not isinstance(agents, list) or not all(
                isinstance(a, str) for a in agents
            ):
                raise RuntimeError(f"{path}: skill `{name}` agents must be strings")
            source_type = entry.get("sourceType")
            manifest[name] = {
                "source": entry["source"],
                "sourceType": source_type if isinstance(source_type, str) else "unknown",
                "agents": sorted(agents),
            }
        return manifest

    @staticmethod
    def _manifest_diff(
        local: dict[str, dict[str, Any]], stored: dict[str, dict[str, Any]]
    ) -> str:
        parts = []
        for name in sorted(set(local) | set(stored)):
            if name not in stored:
                parts.append(f"+{name}")
            elif name not in local:
                parts.append(f"−{name}")
            elif local[name] != stored[name]:
                parts.append(f"~{name}")
        return ", ".join(parts)

    # ----- backup ---------------------------------------------------------

    def plan_from(self, target_dir: Path) -> AppPlan:
        lock = self._lock_path()
        stored_path = self._stored(target_dir) / MANIFEST
        safety = plan_file_copy(MANIFEST, lock, stored_path, dest_root=target_dir)
        if safety.kind == "unknown":
            return AppPlan(self.name, "from", [safety], self.description)
        try:
            local = self._local_manifest()
        except RuntimeError as exc:
            change = Change(MANIFEST, "unknown", lock, stored_path, str(exc))
            return AppPlan(self.name, "from", [change], self.description)
        if not stored_path.exists():
            change = Change(
                MANIFEST,
                "create",
                lock,
                stored_path,
                ", ".join(f"+{name}" for name in sorted(local)),
                diffable=False,
            )
            return AppPlan(self.name, "from", [change], self.description)
        try:
            stored = self._read_stored_manifest(target_dir)
        except (FileNotFoundError, RuntimeError) as exc:
            change = Change(MANIFEST, "unknown", lock, stored_path, str(exc))
            return AppPlan(self.name, "from", [change], self.description)
        diff = self._manifest_diff(local, stored)
        change = Change(
            MANIFEST,
            "update" if diff else "unchanged",
            lock,
            stored_path,
            diff,
            diffable=False,
        )
        return AppPlan(self.name, "from", [change], self.description)

    def sync_from(self, target_dir: Path) -> None:
        stored = self._stored(target_dir)
        ensure_directory(stored, "skills/", root=target_dir)
        manifest = self._local_manifest()
        stored.mkdir(parents=True, exist_ok=True)
        write_text_safely(
            stored / MANIFEST,
            self._manifest_text(manifest),
            MANIFEST,
            dest_root=target_dir,
        )
        ui.ok(MANIFEST)
        for name, entry in sorted(manifest.items()):
            agents = ", ".join(entry["agents"]) or "no managed agent link"
            ui.sub(f"{name} ← {entry['source']} ({agents})")
        if not manifest:
            ui.dim("no skills recorded")

    # ----- status ---------------------------------------------------------

    def status(self, target_dir: Path) -> AppStatus:
        stored_path = self._stored(target_dir) / MANIFEST
        if stored_path.is_symlink():
            return AppStatus(state="unknown", details=f"{MANIFEST} is a symlink")
        if not stored_path.exists():
            return AppStatus(state="missing", details=MANIFEST)
        try:
            stored = self._read_stored_manifest(target_dir)
            local = self._local_manifest()
        except (FileNotFoundError, RuntimeError) as exc:
            return AppStatus(state="unknown", details=str(exc))
        diff = self._manifest_diff(local, stored)
        if not diff:
            return AppStatus(state="clean")
        return AppStatus(state="dirty", details=diff)

    # ----- apply ----------------------------------------------------------

    def _is_installed(self, skill: str, agents: list[str]) -> bool:
        if not self._canonical_dir(skill).is_dir():
            return False
        dirs = self._managed_agent_dirs()
        return all((dirs[agent] / skill).exists() for agent in agents)

    @staticmethod
    def _reinstall_argv(skill: str, source: str, agents: list[str]) -> list[str]:
        argv = ["npx", "-y", "skills", "add", source, "--global", "--skill", skill]
        for agent in agents:
            argv += ["--agent", agent]
        return argv + ["--yes"]

    def plan_to(self, target_dir: Path) -> AppPlan:
        stored_path = self._stored(target_dir) / MANIFEST
        safety = plan_file_copy(
            MANIFEST, stored_path, self._lock_path(), source_root=target_dir
        )
        if safety.kind == "unknown":
            return AppPlan(self.name, "to", [safety], self.description)
        if not stored_path.exists():
            change = Change(MANIFEST, "missing-source", stored_path, None)
            return AppPlan(self.name, "to", [change], self.description)
        try:
            stored = self._read_stored_manifest(target_dir)
        except (FileNotFoundError, RuntimeError) as exc:
            change = Change(MANIFEST, "unknown", stored_path, None, str(exc))
            return AppPlan(self.name, "to", [change], self.description)

        managed = self._managed_agent_dirs()
        changes: list[Change] = []
        for name, entry in sorted(stored.items()):
            label = f"skills add {name}"
            agents = [a for a in entry["agents"] if a in managed]
            if entry["sourceType"] not in REINSTALLABLE_SOURCE_TYPES:
                details = f"{entry['sourceType']} source cannot be reinstalled"
                changes.append(
                    Change(label, "unknown", stored_path, None, details, diffable=False)
                )
            elif not agents:
                changes.append(
                    Change(
                        label,
                        "unknown",
                        stored_path,
                        None,
                        "no managed agent link",
                        diffable=False,
                    )
                )
            elif self._is_installed(name, agents):
                changes.append(
                    Change(label, "unchanged", stored_path, None, diffable=False)
                )
            else:
                details = f"{entry['source']} → {', '.join(agents)}"
                changes.append(
                    Change(label, "create", stored_path, None, details, diffable=False)
                )
        if not changes:
            changes.append(
                Change(
                    MANIFEST,
                    "unchanged",
                    stored_path,
                    None,
                    "no skills recorded",
                    diffable=False,
                )
            )
        return AppPlan(self.name, "to", changes, self.description)

    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        stored = self._stored(target_dir)
        ensure_directory(stored, "skills/", root=target_dir)
        manifest = self._read_stored_manifest(target_dir)
        managed = self._managed_agent_dirs()

        lock = self._lock_path()
        if lock.exists() or lock.is_symlink():
            bdir = backup_dir / self.name
            ensure_directory(bdir, "skills backup", root=backup_dir)
            bdir.mkdir(parents=True, exist_ok=True)
            copy_file_safely(lock, bdir / LOCK, LOCK, dest_root=backup_dir)
            ui.dim(f"backup → {bdir}")

        npx_missing = False
        for name, entry in sorted(manifest.items()):
            desc = f"skills add {name}"
            agents = [a for a in entry["agents"] if a in managed]
            if entry["sourceType"] not in REINSTALLABLE_SOURCE_TYPES:
                msg = (
                    f"{desc} skipped: {entry['sourceType']} source cannot be reinstalled"
                )
                self.warnings.append(msg)
                ui.warn(msg)
                continue
            if not agents:
                ui.sub(f"{desc} (no managed agent link, skipped)")
                continue
            if self._is_installed(name, agents):
                ui.sub(f"{desc} (already present)")
                continue
            if npx_missing:
                continue
            argv = self._reinstall_argv(name, entry["source"], agents)
            try:
                result = self._run_external(argv, desc=desc, fail_mode="warn")
            except FileNotFoundError:
                npx_missing = True
                msg = "skills add skipped: npx not installed"
                self.warnings.append(msg)
                ui.warn(msg)
                continue
            if result.returncode == 0:
                ui.ok(desc)
        ui.dim("hint: restart Claude Code / Codex to pick up new skills")
