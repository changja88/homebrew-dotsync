from pathlib import Path

from dotsync.apps.herdr import HerdrApp


def _herdr_dir(home: Path) -> Path:
    return home / ".config" / "herdr"


def test_sync_from_copies_only_config(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text('[theme]\nname = "nord"\n')
    (local_dir / "session.json").write_text('{"workspaces": []}\n')
    (local_dir / "herdr-server.log").write_text("runtime log\n")
    target = tmp_path / "sync"
    target.mkdir()

    HerdrApp().sync_from(target)

    stored_dir = target / "herdr"
    assert (stored_dir / "config.toml").read_text() == (
        '[theme]\nname = "nord"\n'
    )
    assert sorted(path.name for path in stored_dir.iterdir()) == ["config.toml"]


def test_sync_to_backs_up_config_and_preserves_runtime_state(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("OLD\n")
    (local_dir / "session.json").write_text("SESSION\n")
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("NEW\n")

    HerdrApp().sync_to(target)

    assert (local_dir / "config.toml").read_text() == "NEW\n"
    assert (local_dir / "session.json").read_text() == "SESSION\n"


def test_sync_to_creates_local_config_directory(fake_home, tmp_path):
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("NEW\n")

    HerdrApp().sync_to(target)

    assert (_herdr_dir(fake_home) / "config.toml").read_text() == "NEW\n"


def test_status_clean_when_config_matches(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("X\n")
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("X\n")

    assert HerdrApp().status(target).state == "clean"


def test_status_dirty_when_config_differs(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("LOCAL\n")
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    (target / "herdr" / "config.toml").write_text("STORED\n")

    assert HerdrApp().status(target).state == "dirty"
