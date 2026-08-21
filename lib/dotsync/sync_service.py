"""UI-neutral orchestration for dotsync status, preview, and execution."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from dotsync.apps import build_app
from dotsync.apps.base import App, AppStatus
from dotsync.backup import new_backup_session, rotate_backups
from dotsync.config import Config, load_config_from, save_config
from dotsync.plan import AppPlan, path_fingerprint

SyncDirection = Literal["backup", "apply"]
AppFactory = Callable[[str, Config], App]
BackupSessionFactory = Callable[[Path], Path]
BackupRotator = Callable[[Path, int], None]


def _canonical_json(data: dict[str, object]) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest_json(data: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


class StaleSyncPlan(RuntimeError):
    """Raised when an execution no longer matches its preview."""


@dataclass(frozen=True)
class SyncAppStatus:
    name: str
    status: AppStatus
    plan: AppPlan | None = None

    def to_dict(self, *, relative_to: Path) -> dict[str, object]:
        return {
            "name": self.name,
            "status": {
                "state": self.status.state,
                "details": self.status.details,
                "direction": self.status.direction,
            },
            "plan": (
                self.plan.to_dict(relative_to=relative_to)
                if self.plan is not None
                else None
            ),
        }


@dataclass(frozen=True)
class SyncStatus:
    sync_dir: Path
    apps: tuple[SyncAppStatus, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "sync_dir": {
                "scope": "sync-root",
                "id": path_fingerprint(self.sync_dir),
            },
            "apps": [
                app.to_dict(relative_to=self.sync_dir) for app in self.apps
            ],
        }


@dataclass(frozen=True)
class _ConfigSnapshot:
    sync_dir: Path
    apps: tuple[str, ...]
    backup_dir: Path | None
    backup_keep: int
    bettertouchtool_presets: tuple[str, ...]
    app_options_json: str

    @classmethod
    def capture(cls, config: Config) -> "_ConfigSnapshot":
        return cls(
            sync_dir=config.dir,
            apps=tuple(config.apps),
            backup_dir=config.backup_dir,
            backup_keep=config.backup_keep,
            bettertouchtool_presets=tuple(config.bettertouchtool_presets),
            app_options_json=_canonical_json(copy.deepcopy(config.app_options)),
        )

    @property
    def revision(self) -> str:
        return _digest_json(
            {
                "dir": str(self.sync_dir.absolute()),
                "apps": list(self.apps),
                "backup_dir": (
                    str(self.backup_dir.absolute())
                    if self.backup_dir is not None
                    else None
                ),
                "backup_keep": self.backup_keep,
                "bettertouchtool_presets": list(self.bettertouchtool_presets),
                "app_options": self.app_options_json,
            }
        )

    def to_config(self) -> Config:
        return Config(
            dir=self.sync_dir,
            apps=list(self.apps),
            backup_dir=self.backup_dir,
            backup_keep=self.backup_keep,
            bettertouchtool_presets=list(self.bettertouchtool_presets),
            app_options=json.loads(self.app_options_json),
        )


@dataclass(frozen=True)
class SyncPreview:
    direction: SyncDirection
    apps: tuple[str, ...]
    plans: tuple[AppPlan, ...]
    digest: str
    _plan_data: dict[str, object] = field(repr=False)
    _config_snapshot: _ConfigSnapshot = field(repr=False)

    def to_dict(self) -> dict[str, object]:
        result = copy.deepcopy(self._plan_data)
        result["digest"] = self.digest
        return result


@dataclass(frozen=True)
class SyncExecutionResult:
    direction: SyncDirection
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]
    failed: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: dict[str, tuple[str, ...]]
    backup_dir: Path | None
    duration_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "direction": self.direction,
            "changed": list(self.changed),
            "unchanged": list(self.unchanged),
            "failed": list(self.failed),
            "errors": list(self.errors),
            "warnings": {
                name: list(warnings) for name, warnings in self.warnings.items()
            },
            "backup_dir": str(self.backup_dir) if self.backup_dir is not None else None,
            "duration_ms": self.duration_ms,
        }


class SyncEvents:
    """Optional execution events for callers that render progress."""

    def app_started(
        self, name: str, app: App, *, index: int, total: int
    ) -> None:  # pragma: no cover - default no-op
        return None

    def backup_created(
        self, backup_dir: Path
    ) -> None:  # pragma: no cover - default no-op
        return None

    def app_succeeded(self, app: App) -> None:  # pragma: no cover - default no-op
        return None

    def app_unchanged(self, app: App) -> None:  # pragma: no cover - default no-op
        return None

    def app_failed(
        self, app: App, error: Exception
    ) -> None:  # pragma: no cover - default no-op
        return None

    def app_finished(self, app: App) -> None:  # pragma: no cover - default no-op
        return None


def serialize_sync_plan(
    *,
    direction: SyncDirection,
    apps: tuple[str, ...],
    plans: tuple[AppPlan, ...],
    sync_dir: Path,
    config_revision: str = "",
) -> dict[str, object]:
    """Build the canonical, JSON-safe filesystem plan used for validation."""
    return {
        "direction": direction,
        "apps": list(apps),
        "config_revision": config_revision,
        "plans": [plan.to_dict(relative_to=sync_dir) for plan in plans],
        "sync_dir": {
            "scope": "sync-root",
            "id": path_fingerprint(sync_dir),
        },
    }


def sync_plan_digest(plan_data: dict[str, object]) -> str:
    """Hash canonical UTF-8 JSON for a serialized sync plan."""
    return _digest_json(plan_data)


class SyncService:
    def __init__(
        self,
        config: Config,
        *,
        events: SyncEvents | None = None,
        app_factory: AppFactory = build_app,
        backup_session_factory: BackupSessionFactory = new_backup_session,
        backup_rotator: BackupRotator = rotate_backups,
    ) -> None:
        self.config = config
        self._events = events or SyncEvents()
        self._app_factory = app_factory
        self._backup_session_factory = backup_session_factory
        self._backup_rotator = backup_rotator
        self._previews: dict[str, SyncPreview] = {}

    def status(self) -> SyncStatus:
        statuses: list[SyncAppStatus] = []
        for name in self.config.apps:
            app = self._app_factory(name, self.config)
            app_status = app.status(self.config.dir)
            plan: AppPlan | None = None
            if app_status.state == "dirty":
                try:
                    plan = app.plan_from(self.config.dir)
                except (OSError, RuntimeError, ValueError):
                    pass
            statuses.append(SyncAppStatus(name=name, status=app_status, plan=plan))
        return SyncStatus(sync_dir=self.config.dir, apps=tuple(statuses))

    def preview(
        self, direction: SyncDirection, apps: tuple[str, ...]
    ) -> SyncPreview:
        config_snapshot = _ConfigSnapshot.capture(self.config)
        preview = self._build_preview(
            direction=direction,
            apps=apps,
            config_snapshot=config_snapshot,
        )
        self._previews[preview.digest] = preview
        return preview

    def execute(self, digest: str) -> SyncExecutionResult:
        expected = self._previews.get(digest)
        if expected is None:
            raise StaleSyncPlan(
                "sync preview is unavailable; create a new preview before executing"
            )

        if _ConfigSnapshot.capture(self.config) != expected._config_snapshot:
            self._previews.pop(digest, None)
            raise StaleSyncPlan(
                "sync preview is stale; create a new preview before executing"
            )

        current = self._build_preview(
            direction=expected.direction,
            apps=expected.apps,
            config_snapshot=expected._config_snapshot,
        )
        if current.digest != digest or current._plan_data != expected._plan_data:
            self._previews.pop(digest, None)
            raise StaleSyncPlan(
                "sync preview is stale; create a new preview before executing"
            )

        result = self._execute_preview(current)
        self._previews.pop(digest, None)
        return result

    def with_config(self, config: Config) -> "SyncService":
        """Build an unpublished service candidate with the same dependencies."""
        return SyncService(
            config,
            events=self._events,
            app_factory=self._app_factory,
            backup_session_factory=self._backup_session_factory,
            backup_rotator=self._backup_rotator,
        )

    def update_apps(self, apps: tuple[str, ...]) -> Config:
        candidate = copy.deepcopy(self.config)
        candidate.apps = list(apps)
        try:
            save_config(candidate)
        except BaseException:
            try:
                self.config = load_config_from(self.config.dir)
            finally:
                self._previews.clear()
            raise
        self.config = candidate
        self._previews.clear()
        return self.config

    def _build_preview(
        self,
        *,
        direction: SyncDirection,
        apps: tuple[str, ...],
        config_snapshot: _ConfigSnapshot,
    ) -> SyncPreview:
        if direction not in ("backup", "apply"):
            raise ValueError(f"unknown sync direction: {direction}")
        config = config_snapshot.to_config()
        plan_direction = "from" if direction == "backup" else "to"
        plans: list[AppPlan] = []
        for name in apps:
            app = self._app_factory(name, config)
            if plan_direction == "from":
                plans.append(app.plan_from(config.dir))
            else:
                plans.append(app.plan_to(config.dir))
        plan_tuple = tuple(plans)
        plan_data = serialize_sync_plan(
            direction=direction,
            apps=apps,
            plans=plan_tuple,
            sync_dir=config.dir,
            config_revision=config_snapshot.revision,
        )
        return SyncPreview(
            direction=direction,
            apps=apps,
            plans=plan_tuple,
            digest=sync_plan_digest(plan_data),
            _plan_data=plan_data,
            _config_snapshot=config_snapshot,
        )

    def _execute_preview(self, preview: SyncPreview) -> SyncExecutionResult:
        config = preview._config_snapshot.to_config()
        unchanged_by_plan = {
            plan.app: bool(plan.changes) and not plan.has_changes
            for plan in preview.plans
        }
        start = time.monotonic()
        changed: list[str] = []
        unchanged: list[str] = []
        failed: list[str] = []
        errors: list[str] = []
        warnings: dict[str, tuple[str, ...]] = {}
        backup_dir: Path | None = None

        for index, name in enumerate(preview.apps, 1):
            app = self._app_factory(name, config)
            self._events.app_started(
                name, app, index=index, total=len(preview.apps)
            )
            if unchanged_by_plan.get(name, False):
                unchanged.append(name)
                self._events.app_unchanged(app)
                self._events.app_finished(app)
                continue
            try:
                if preview.direction == "apply" and backup_dir is None:
                    assert config.backup_dir is not None
                    backup_dir = self._backup_session_factory(config.backup_dir)
                    self._events.backup_created(backup_dir)
                if preview.direction == "backup":
                    app.sync_from(config.dir)
                else:
                    assert backup_dir is not None
                    app.sync_to(config.dir, backup_dir)
                changed.append(name)
                self._events.app_succeeded(app)
            except (FileNotFoundError, RuntimeError) as error:
                failed.append(name)
                errors.append(str(error))
                self._events.app_failed(app, error)
            if app.warnings:
                warnings[name] = tuple(app.warnings)
            self._events.app_finished(app)

        if backup_dir is not None:
            assert config.backup_dir is not None
            self._backup_rotator(config.backup_dir, config.backup_keep)

        return SyncExecutionResult(
            direction=preview.direction,
            changed=tuple(changed),
            unchanged=tuple(unchanged),
            failed=tuple(failed),
            errors=tuple(errors),
            warnings=warnings,
            backup_dir=backup_dir,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
