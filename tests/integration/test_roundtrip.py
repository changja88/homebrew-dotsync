"""Round-trip idempotency: from→to and to→from must not mutate the
non-source side. Regression net for Phase 4's default sync_from/sync_to."""
import shutil
from pathlib import Path
import pytest
from dotsync.apps.codex import CodexApp
from dotsync.apps.ghostty import GhosttyApp
from dotsync.apps.zsh import ZshApp


def _ghostty_local(home: Path) -> Path:
    return home / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config.ghostty"


def _codex_dir(home: Path) -> Path:
    return home / ".codex"


def test_ghostty_from_then_to_does_not_change_local(fake_home, tmp_path):
    local = _ghostty_local(fake_home)
    local.parent.mkdir(parents=True)
    local.write_text("font-family = JetBrains Mono\n")
    target = tmp_path / "sync"; target.mkdir()
    backup = tmp_path / "backup"; backup.mkdir()

    GhosttyApp().sync_from(target)
    GhosttyApp().sync_to(target, backup)

    assert local.read_text() == "font-family = JetBrains Mono\n"


def test_ghostty_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"; target.mkdir()
    stored_dir = target / "ghostty"; stored_dir.mkdir()
    (stored_dir / "config.ghostty").write_text("theme = catppuccin\n")
    backup = tmp_path / "backup"; backup.mkdir()
    _ghostty_local(fake_home).parent.mkdir(parents=True)
    _ghostty_local(fake_home).write_text("old content\n")

    GhosttyApp().sync_to(target, backup)
    GhosttyApp().sync_from(target)

    assert (stored_dir / "config.ghostty").read_text() == "theme = catppuccin\n"


def test_zsh_from_then_to_does_not_change_local(fake_home, tmp_path):
    local = fake_home / ".zshrc"
    local.write_text("export FOO=bar\n")
    target = tmp_path / "sync"; target.mkdir()
    backup = tmp_path / "backup"; backup.mkdir()

    ZshApp().sync_from(target)
    ZshApp().sync_to(target, backup)

    assert local.read_text() == "export FOO=bar\n"


def test_zsh_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"; target.mkdir()
    (target / "zsh").mkdir()
    (target / "zsh" / ".zshrc").write_text("alias ll='ls -la'\n")
    backup = tmp_path / "backup"; backup.mkdir()
    (fake_home / ".zshrc").write_text("old\n")

    ZshApp().sync_to(target, backup)
    ZshApp().sync_from(target)

    assert (target / "zsh" / ".zshrc").read_text() == "alias ll='ls -la'\n"


def test_codex_from_then_to_does_not_change_local(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "gpt-5.2"\n')
    (cdir / "AGENTS.md").write_text("# instructions\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "default.rules").write_text("allow\n")
    (cdir / "skills" / "mine").mkdir(parents=True)
    (cdir / "skills" / "mine" / "SKILL.md").write_text("# mine\n")
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("# builtin\n")
    target = tmp_path / "sync"; target.mkdir()
    backup = tmp_path / "backup"; backup.mkdir()

    CodexApp().sync_from(target)
    CodexApp().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == 'model = "gpt-5.2"\n'
    assert (cdir / "AGENTS.md").read_text() == "# instructions\n"
    assert (cdir / "rules" / "default.rules").read_text() == "allow\n"
    assert (cdir / "skills" / "mine" / "SKILL.md").read_text() == "# mine\n"
    assert (cdir / "skills" / ".system" / "builtin" / "SKILL.md").read_text() == "# builtin\n"
    assert not (target / "codex" / "skills" / ".system").exists()


def test_codex_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"; target.mkdir()
    stored_dir = target / "codex"; stored_dir.mkdir()
    (stored_dir / "config.toml").write_text('approval_policy = "on-request"\n')
    (stored_dir / "AGENTS.md").write_text("# shared instructions\n")
    (stored_dir / "rules").mkdir()
    (stored_dir / "rules" / "default.rules").write_text("prompt\n")
    (stored_dir / "skills" / "shared").mkdir(parents=True)
    (stored_dir / "skills" / "shared" / "SKILL.md").write_text("# shared\n")
    backup = tmp_path / "backup"; backup.mkdir()
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("old\n")
    (cdir / "AGENTS.md").write_text("old agents\n")

    CodexApp().sync_to(target, backup)
    CodexApp().sync_from(target)

    assert (stored_dir / "config.toml").read_text() == 'approval_policy = "on-request"\n'
    assert (stored_dir / "AGENTS.md").read_text() == "# shared instructions\n"
    assert (stored_dir / "rules" / "default.rules").read_text() == "prompt\n"
    assert (stored_dir / "skills" / "shared" / "SKILL.md").read_text() == "# shared\n"


def test_ghostty_from_then_to_creates_backup_before_overwriting(fake_home, tmp_path):
    """from→to must back up the pre-existing local before copying stored
    over it. The backup content must equal the original local content."""
    local = _ghostty_local(fake_home)
    local.parent.mkdir(parents=True)
    local.write_text("ORIGINAL\n")
    target = tmp_path / "sync"; target.mkdir()
    backup = tmp_path / "backup"; backup.mkdir()

    GhosttyApp().sync_from(target)
    # Mutate local so to has something to overwrite
    local.write_text("MUTATED\n")

    GhosttyApp().sync_to(target, backup)

    # Backup captured the MUTATED content (the pre-to local)
    assert (backup / "ghostty" / "config.ghostty").read_text() == "MUTATED\n"
    # Local now matches the stored snapshot (which is ORIGINAL from sync_from)
    assert local.read_text() == "ORIGINAL\n"
