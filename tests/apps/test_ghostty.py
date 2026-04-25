from pathlib import Path
import pytest
from dotsync.apps.ghostty import GhosttyApp


def _ghostty_dir(home: Path) -> Path:
    return home / "Library" / "Application Support" / "com.mitchellh.ghostty"


def test_sync_from_copies_config(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home)
    gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("font-size = 14\n")
    target = tmp_path / "configs"
    target.mkdir()

    GhosttyApp().sync_from(target)

    assert (target / "ghostty" / "config.ghostty").read_text() == "font-size = 14\n"


def test_sync_from_missing_local_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with pytest.raises(FileNotFoundError, match="config.ghostty"):
        GhosttyApp().sync_from(target)


def test_sync_to_backs_up_and_writes(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home)
    gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("OLD\n")
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    GhosttyApp().sync_to(target, backup)

    assert (gdir / "config.ghostty").read_text() == "NEW\n"
    assert (backup / "ghostty" / "config.ghostty").read_text() == "OLD\n"


def test_sync_to_creates_local_dir_if_missing(fake_home, tmp_path):
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("X\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    GhosttyApp().sync_to(target, backup)

    assert (_ghostty_dir(fake_home) / "config.ghostty").read_text() == "X\n"


def test_status_clean(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home); gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("X")
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("X")
    assert GhosttyApp().status(target).state == "clean"


def test_status_dirty(fake_home, tmp_path):
    gdir = _ghostty_dir(fake_home); gdir.mkdir(parents=True)
    (gdir / "config.ghostty").write_text("OLD")
    target = tmp_path / "configs"
    (target / "ghostty").mkdir(parents=True)
    (target / "ghostty" / "config.ghostty").write_text("NEW")
    assert GhosttyApp().status(target).state == "dirty"
