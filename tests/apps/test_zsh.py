from pathlib import Path
import pytest
from dotsync.apps.zsh import ZshApp


def test_sync_from_copies_zshrc_to_target(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("export FOO=1\n")
    target = tmp_path / "configs"
    target.mkdir()

    ZshApp().sync_from(target)

    assert (target / "zsh" / ".zshrc").read_text() == "export FOO=1\n"


def test_sync_from_missing_zshrc_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with pytest.raises(FileNotFoundError, match=".zshrc"):
        ZshApp().sync_from(target)


def test_sync_to_backs_up_then_overwrites(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("OLD\n")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    ZshApp().sync_to(target, backup)

    assert (fake_home / ".zshrc").read_text() == "NEW\n"
    assert (backup / "zsh" / ".zshrc").read_text() == "OLD\n"


def test_sync_to_missing_target_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="zsh/.zshrc"):
        ZshApp().sync_to(target, backup)


def test_status_clean_when_files_match(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("X")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("X")
    assert ZshApp().status(target).state == "clean"


def test_status_dirty_when_content_differs(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("OLD")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("NEW")
    assert ZshApp().status(target).state == "dirty"


def test_status_missing_when_either_absent(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    assert ZshApp().status(target).state == "missing"
