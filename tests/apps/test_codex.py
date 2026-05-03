from pathlib import Path
import pytest


def _codex_app():
    from dotsync.apps.codex import CodexApp
    return CodexApp()


def _codex_dir(home: Path) -> Path:
    return home / ".codex"


def test_sync_from_copies_config_and_agents_when_present(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "gpt-5.2"\n')
    (cdir / "AGENTS.md").write_text("# local instructions\n")
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    assert (target / "codex" / "config.toml").read_text() == 'model = "gpt-5.2"\n'
    assert (target / "codex" / "AGENTS.md").read_text() == "# local instructions\n"


def test_sync_from_missing_config_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()

    with pytest.raises(FileNotFoundError, match="config.toml"):
        _codex_app().sync_from(target)


def test_sync_from_removes_stale_stored_agents_when_local_agents_missing(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "AGENTS.md").write_text("STALE\n")

    _codex_app().sync_from(target)

    assert not (target / "codex" / "AGENTS.md").exists()


def test_sync_to_backs_up_and_writes_config_and_agents(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.md").write_text("OLD AGENTS\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "AGENTS.md").write_text("NEW AGENTS\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "AGENTS.md").read_text() == "NEW AGENTS\n"
    assert (backup / "codex" / "config.toml").read_text() == "OLD\n"
    assert (backup / "codex" / "AGENTS.md").read_text() == "OLD AGENTS\n"


def test_sync_to_without_stored_agents_keeps_local_agents(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.md").write_text("KEEP ME\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "AGENTS.md").read_text() == "KEEP ME\n"
    assert not (backup / "codex" / "AGENTS.md").exists()


def test_status_clean_when_config_and_agents_match(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")
    (cdir / "AGENTS.md").write_text("Y")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("X")
    (target / "codex" / "AGENTS.md").write_text("Y")

    assert _codex_app().status(target).state == "clean"


def test_status_dirty_when_agents_differ(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")
    (cdir / "AGENTS.md").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("X")
    (target / "codex" / "AGENTS.md").write_text("STORED")

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "AGENTS.md" in status.details


def test_status_ignores_agents_when_missing_on_both_sides(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("X")

    assert _codex_app().status(target).state == "clean"


def test_status_missing_when_config_absent(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()

    assert _codex_app().status(target).state == "missing"


def test_is_present_locally_true_when_config_exists(fake_home):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")

    assert type(_codex_app()).is_present_locally() is True


def test_is_present_locally_false_when_no_config(fake_home):
    assert type(_codex_app()).is_present_locally() is False
