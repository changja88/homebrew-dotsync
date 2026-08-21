import json
import os
from pathlib import Path

import pytest

from dotsync.config import Config, load_config, save_config
from dotsync.plan import AppPlan, Change, plan_tree_mirror
from dotsync.sync_service import (
    StaleSyncPlan,
    SyncService,
    serialize_sync_plan,
)


@pytest.fixture
def sync_setup(fake_home, monkeypatch, tmp_path):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    config = Config(dir=sync_dir, apps=["zsh"])
    save_config(config)
    monkeypatch.setenv("DOTSYNC_DIR", str(sync_dir))
    local_file = fake_home / ".zshrc"
    local_file.write_text("local before preview")
    return SyncService(config), sync_dir, local_file


def test_execute_rejects_plan_when_files_changed_after_preview(sync_setup):
    service, sync_dir, local_file = sync_setup
    preview = service.preview(direction="backup", apps=("zsh",))

    local_file.write_text("changed after preview")

    with pytest.raises(StaleSyncPlan, match="preview"):
        service.execute(preview.digest)
    assert not (sync_dir / "zsh" / ".zshrc").exists()


def test_execute_validates_every_app_before_any_sync_mutation(
    fake_home, monkeypatch, tmp_path
):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    config = Config(dir=sync_dir, apps=["zsh", "ghostty"])
    save_config(config)
    monkeypatch.setenv("DOTSYNC_DIR", str(sync_dir))
    (fake_home / ".zshrc").write_text("zsh before preview")
    ghostty_file = (
        fake_home
        / "Library"
        / "Application Support"
        / "com.mitchellh.ghostty"
        / "config.ghostty"
    )
    ghostty_file.parent.mkdir(parents=True)
    ghostty_file.write_text("ghostty before preview")
    service = SyncService(config)
    preview = service.preview(direction="backup", apps=("zsh", "ghostty"))

    ghostty_file.write_text("ghostty changed after preview")

    with pytest.raises(StaleSyncPlan, match="preview"):
        service.execute(preview.digest)
    assert not (sync_dir / "zsh" / ".zshrc").exists()
    assert not (sync_dir / "ghostty" / "config.ghostty").exists()


def test_apply_result_exposes_backup_path(sync_setup):
    service, sync_dir, local_file = sync_setup
    stored_file = sync_dir / "zsh" / ".zshrc"
    stored_file.parent.mkdir()
    stored_file.write_text("stored version")
    preview = service.preview(direction="apply", apps=("zsh",))

    result = service.execute(preview.digest)

    assert result.backup_dir is not None
    assert result.errors == ()
    assert local_file.read_text() == "stored version"
    assert (result.backup_dir / "zsh" / ".zshrc").read_text() == (
        "local before preview"
    )


def test_preview_digest_is_deterministic_and_never_serializes_file_contents(sync_setup):
    service, _, _ = sync_setup

    first = service.preview(direction="backup", apps=("zsh",))
    second = service.preview(direction="backup", apps=("zsh",))
    serialized = json.dumps(first.to_dict(), ensure_ascii=False, sort_keys=True)

    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert first.direction == "backup"
    assert first.apps == ("zsh",)
    assert "local before preview" not in serialized
    assert '"sha256"' in serialized
    assert '"mtime_ns"' in serialized


def test_status_returns_dirty_plan_without_printing(sync_setup, capsys):
    service, sync_dir, local_file = sync_setup
    stored_file = sync_dir / "zsh" / ".zshrc"
    stored_file.parent.mkdir()
    stored_file.write_text("stored version")
    local_file.write_text("local version")

    result = service.status()

    assert result.sync_dir == sync_dir
    assert len(result.apps) == 1
    assert result.apps[0].name == "zsh"
    assert result.apps[0].status.state == "dirty"
    assert result.apps[0].plan is not None
    assert result.apps[0].plan.changes[0].kind == "update"
    assert capsys.readouterr().out == ""


def test_update_apps_persists_selected_order(sync_setup):
    service, _, _ = sync_setup

    updated = service.update_apps(("ghostty", "zsh"))

    assert updated.apps == ["ghostty", "zsh"]
    assert load_config().apps == ["ghostty", "zsh"]


def test_update_apps_invalidates_preview_before_selected_app_is_called(
    monkeypatch, tmp_path
):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    config = Config(dir=sync_dir, apps=["zsh"])
    save_config(config)
    monkeypatch.setenv("DOTSYNC_DIR", str(sync_dir))
    calls = {"plan": 0, "sync": 0}

    class CountingApp:
        description = "Counting app"
        warnings: list[str] = []

        def plan_from(self, target_dir):
            calls["plan"] += 1
            return AppPlan(
                app="zsh",
                direction="from",
                changes=[Change("settings", "create")],
            )

        def sync_from(self, target_dir):
            calls["sync"] += 1

    service = SyncService(config, app_factory=lambda name, cfg: CountingApp())
    preview = service.preview(direction="backup", apps=("zsh",))
    assert calls == {"plan": 1, "sync": 0}

    service.update_apps(("ghostty",))

    with pytest.raises(StaleSyncPlan, match="preview"):
        service.execute(preview.digest)
    assert calls == {"plan": 1, "sync": 0}


def test_execute_rejects_direct_config_revision_before_replanning(
    monkeypatch, tmp_path
):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    config = Config(dir=sync_dir, apps=["zsh"])
    save_config(config)
    monkeypatch.setenv("DOTSYNC_DIR", str(sync_dir))
    calls = {"plan": 0, "sync": 0}

    class CountingApp:
        description = "Counting app"
        warnings: list[str] = []

        def plan_from(self, target_dir):
            calls["plan"] += 1
            return AppPlan("zsh", "from", [Change("settings", "create")])

        def sync_from(self, target_dir):
            calls["sync"] += 1

    service = SyncService(config, app_factory=lambda name, cfg: CountingApp())
    preview = service.preview(direction="backup", apps=("zsh",))
    assert calls == {"plan": 1, "sync": 0}

    config.app_options["zsh"] = {"revision": "changed"}

    with pytest.raises(StaleSyncPlan, match="preview"):
        service.execute(preview.digest)
    assert calls == {"plan": 1, "sync": 0}


def test_serialized_plan_uses_safe_scopes_without_local_path_disclosure(tmp_path):
    sync_dir = tmp_path / "selected-user-private" / "configs"
    sync_dir.mkdir(parents=True)
    external = Path(
        "/Users/external-user-secret/Library/Preferences/settings.json"
    )
    plan = AppPlan(
        "zsh",
        "from",
        [
            Change(
                "settings.json",
                "create",
                external,
                sync_dir / "zsh/settings.json",
                f"cannot read {external} from {sync_dir}",
            )
        ],
    )

    data = serialize_sync_plan(
        direction="backup",
        apps=("zsh",),
        plans=(plan,),
        sync_dir=sync_dir,
        config_revision="a" * 64,
    )
    serialized = json.dumps(data, sort_keys=True)
    source = data["plans"][0]["changes"][0]["source"]
    dest = data["plans"][0]["changes"][0]["dest"]

    assert source["scope"] == "external"
    assert source["id"].startswith("sha256:")
    assert dest == {
        "scope": "sync",
        "id": "zsh/settings.json",
        "kind": "missing",
    }
    assert data["sync_dir"]["scope"] == "sync-root"
    assert data["sync_dir"]["id"].startswith("sha256:")
    assert "external-user-secret" not in serialized
    assert "selected-user-private" not in serialized
    assert str(sync_dir) not in serialized
    assert "/Users/" not in serialized
    assert '".."' not in serialized
    assert "../" not in serialized


def test_execute_rejects_retargeted_symlink_before_apply_mutation(tmp_path):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    first_target = sync_dir / "first.json"
    second_target = sync_dir / "second.json"
    first_target.write_text("first")
    second_target.write_text("second")
    stored_link = sync_dir / "settings.json"
    stored_link.symlink_to(first_target)
    local_dest = tmp_path / "local" / "settings.json"
    config = Config(dir=sync_dir, apps=["zsh"])
    calls = {"backup": 0, "sync": 0}

    class SymlinkApp:
        description = "Symlink app"
        warnings: list[str] = []

        def plan_to(self, target_dir):
            return AppPlan(
                "zsh",
                "to",
                [Change("settings.json", "update", stored_link, local_dest)],
            )

        def sync_to(self, target_dir, backup_dir):
            calls["sync"] += 1

    def backup_session(root):
        calls["backup"] += 1
        return root / "session"

    service = SyncService(
        config,
        app_factory=lambda name, cfg: SymlinkApp(),
        backup_session_factory=backup_session,
    )
    preview = service.preview(direction="apply", apps=("zsh",))
    original_stat = stored_link.lstat()

    stored_link.unlink()
    stored_link.symlink_to(second_target)
    os.utime(
        stored_link,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        follow_symlinks=False,
    )
    assert stored_link.lstat().st_mtime_ns == original_stat.st_mtime_ns

    with pytest.raises(StaleSyncPlan, match="preview"):
        service.execute(preview.digest)
    assert calls == {"backup": 0, "sync": 0}


def test_ignored_tree_change_does_not_stale_planner_scoped_preview(tmp_path):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    local_skills = tmp_path / "local" / "skills"
    ignored_file = local_skills / ".system" / "managed-by-codex.md"
    tracked_file = local_skills / "mine" / "SKILL.md"
    ignored_file.parent.mkdir(parents=True)
    tracked_file.parent.mkdir(parents=True)
    ignored_file.write_text("ignored before")
    tracked_file.write_text("tracked")
    calls = {"sync": 0}

    class TreeApp:
        description = "Tree app"
        warnings: list[str] = []

        def plan_from(self, target_dir):
            return AppPlan(
                "zsh",
                "from",
                [
                    plan_tree_mirror(
                        "skills/",
                        local_skills,
                        target_dir / "zsh" / "skills",
                        ignored_top_dirs=(".system",),
                        dest_root=target_dir,
                    )
                ],
            )

        def sync_from(self, target_dir):
            calls["sync"] += 1

    service = SyncService(
        Config(dir=sync_dir, apps=["zsh"]),
        app_factory=lambda name, cfg: TreeApp(),
    )
    preview = service.preview(direction="backup", apps=("zsh",))

    ignored_file.write_text("ignored after")

    result = service.execute(preview.digest)
    assert result.errors == ()
    assert calls == {"sync": 1}


def test_planned_tree_descendant_change_stales_preview(tmp_path):
    sync_dir = tmp_path / "configs"
    sync_dir.mkdir()
    local_skills = tmp_path / "local" / "skills"
    ignored_file = local_skills / ".system" / "managed-by-codex.md"
    tracked_file = local_skills / "mine" / "SKILL.md"
    ignored_file.parent.mkdir(parents=True)
    tracked_file.parent.mkdir(parents=True)
    ignored_file.write_text("ignored")
    tracked_file.write_text("tracked before")
    calls = {"sync": 0}

    class TreeApp:
        description = "Tree app"
        warnings: list[str] = []

        def plan_from(self, target_dir):
            return AppPlan(
                "zsh",
                "from",
                [
                    plan_tree_mirror(
                        "skills/",
                        local_skills,
                        target_dir / "zsh" / "skills",
                        ignored_top_dirs=(".system",),
                        dest_root=target_dir,
                    )
                ],
            )

        def sync_from(self, target_dir):
            calls["sync"] += 1

    service = SyncService(
        Config(dir=sync_dir, apps=["zsh"]),
        app_factory=lambda name, cfg: TreeApp(),
    )
    preview = service.preview(direction="backup", apps=("zsh",))

    tracked_file.write_text("tracked after")

    with pytest.raises(StaleSyncPlan, match="preview"):
        service.execute(preview.digest)
    assert calls == {"sync": 0}
