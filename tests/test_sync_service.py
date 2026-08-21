import json

import pytest

from dotsync.config import Config, load_config, save_config
from dotsync.sync_service import StaleSyncPlan, SyncService


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
