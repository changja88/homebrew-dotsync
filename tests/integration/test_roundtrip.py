"""Round-trip idempotency: backup→apply and apply→backup must not mutate the
non-source side. Regression net for Phase 4's default sync_from/sync_to."""

import json
from pathlib import Path
from unittest.mock import patch

from dotsync.apps.codex import CodexApp
from dotsync.apps.herdr import HerdrApp
from dotsync.apps.skills import SkillsApp
from dotsync.apps.ghostty import GhosttyApp
from dotsync.apps.zsh import ZshApp


def _ghostty_local(home: Path) -> Path:
    return (
        home
        / "Library"
        / "Application Support"
        / "com.mitchellh.ghostty"
        / "config.ghostty"
    )


def _codex_dir(home: Path) -> Path:
    return home / ".codex"


def _herdr_dir(home: Path) -> Path:
    return home / ".config" / "herdr"


def test_herdr_from_then_to_does_not_change_local(fake_home, tmp_path):
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    local = local_dir / "config.toml"
    local.write_text('[theme]\nname = "nord"\n')
    target = tmp_path / "sync"
    target.mkdir()

    HerdrApp().sync_from(target)
    HerdrApp().sync_to(target)

    assert local.read_text() == '[theme]\nname = "nord"\n'


def test_herdr_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"
    (target / "herdr").mkdir(parents=True)
    stored = target / "herdr" / "config.toml"
    stored.write_text("onboarding = false\n")
    local_dir = _herdr_dir(fake_home)
    local_dir.mkdir(parents=True)
    (local_dir / "config.toml").write_text("OLD\n")

    HerdrApp().sync_to(target)
    HerdrApp().sync_from(target)

    assert stored.read_text() == "onboarding = false\n"


def test_ghostty_from_then_to_does_not_change_local(fake_home, tmp_path):
    local = _ghostty_local(fake_home)
    local.parent.mkdir(parents=True)
    local.write_text("font-family = JetBrains Mono\n")
    target = tmp_path / "sync"
    target.mkdir()

    GhosttyApp().sync_from(target)
    GhosttyApp().sync_to(target)

    assert local.read_text() == "font-family = JetBrains Mono\n"


def test_ghostty_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"
    target.mkdir()
    stored_dir = target / "ghostty"
    stored_dir.mkdir()
    (stored_dir / "config.ghostty").write_text("theme = catppuccin\n")
    _ghostty_local(fake_home).parent.mkdir(parents=True)
    _ghostty_local(fake_home).write_text("old content\n")

    GhosttyApp().sync_to(target)
    GhosttyApp().sync_from(target)

    assert (stored_dir / "config.ghostty").read_text() == "theme = catppuccin\n"


def test_zsh_from_then_to_does_not_change_local(fake_home, tmp_path):
    local = fake_home / ".zshrc"
    local.write_text("export FOO=bar\n")
    target = tmp_path / "sync"
    target.mkdir()

    ZshApp().sync_from(target)
    ZshApp().sync_to(target)

    assert local.read_text() == "export FOO=bar\n"


def test_zsh_to_then_from_does_not_change_stored(fake_home, tmp_path):
    target = tmp_path / "sync"
    target.mkdir()
    (target / "zsh").mkdir()
    (target / "zsh" / ".zshrc").write_text("alias ll='ls -la'\n")
    (fake_home / ".zshrc").write_text("old\n")

    ZshApp().sync_to(target)
    ZshApp().sync_from(target)

    assert (target / "zsh" / ".zshrc").read_text() == "alias ll='ls -la'\n"


def test_codex_from_then_to_does_not_change_local(fake_home, tmp_path, fake_codex_cli):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    config = 'model = "gpt-5.2"\n\n[plugins."mine@my-market"]\nenabled = true\n'
    (cdir / "config.toml").write_text(config)
    (cdir / "AGENTS.md").write_text("# instructions\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "default.rules").write_text("allow\n")
    (cdir / "skills" / "mine").mkdir(parents=True)
    (cdir / "skills" / "mine" / "SKILL.md").write_text("# mine\n")
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("# builtin\n")
    target = tmp_path / "sync"
    target.mkdir()

    CodexApp().sync_from(target)
    CodexApp().sync_to(target)

    assert (cdir / "config.toml").read_text() == config
    assert (target / "codex" / "config.toml").read_text() == 'model = "gpt-5.2"\n'
    assert (cdir / "AGENTS.md").read_text() == "# instructions\n"
    assert (cdir / "rules" / "default.rules").read_text() == "allow\n"
    assert (cdir / "skills" / "mine" / "SKILL.md").read_text() == "# mine\n"
    assert (
        cdir / "skills" / ".system" / "builtin" / "SKILL.md"
    ).read_text() == "# builtin\n"
    assert not (target / "codex" / "skills" / ".system").exists()


def test_codex_to_then_from_does_not_change_stored(fake_home, tmp_path, fake_codex_cli):
    target = tmp_path / "sync"
    target.mkdir()
    stored_dir = target / "codex"
    stored_dir.mkdir()
    (stored_dir / "config.toml").write_text('approval_policy = "on-request"\n')
    (stored_dir / "AGENTS.md").write_text("# shared instructions\n")
    (stored_dir / "rules").mkdir()
    (stored_dir / "rules" / "default.rules").write_text("prompt\n")
    (stored_dir / "skills" / "shared").mkdir(parents=True)
    (stored_dir / "skills" / "shared" / "SKILL.md").write_text("# shared\n")
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("old\n")
    (cdir / "AGENTS.md").write_text("old agents\n")

    CodexApp().sync_to(target)
    CodexApp().sync_from(target)

    assert (
        stored_dir / "config.toml"
    ).read_text() == 'approval_policy = "on-request"\n'
    assert (stored_dir / "AGENTS.md").read_text() == "# shared instructions\n"
    assert (stored_dir / "rules" / "default.rules").read_text() == "prompt\n"
    assert (stored_dir / "skills" / "shared" / "SKILL.md").read_text() == "# shared\n"


def test_skills_from_then_to_does_not_change_local_or_run_npx(fake_home, tmp_path):
    agents = fake_home / ".agents"
    canonical = agents / "skills" / "herdr"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("# herdr\n")
    (fake_home / ".claude" / "skills").mkdir(parents=True)
    (fake_home / ".claude" / "skills" / "herdr").symlink_to(
        canonical, target_is_directory=True
    )
    lock = agents / ".skill-lock.json"
    lock_text = json.dumps(
        {
            "version": 3,
            "skills": {"herdr": {"source": "ogulcancelik/herdr", "sourceType": "github"}},
        }
    )
    lock.write_text(lock_text)
    target = tmp_path / "sync"
    target.mkdir()

    SkillsApp().sync_from(target)
    with patch("dotsync.apps.base.subprocess.run") as run:
        SkillsApp().sync_to(target)

    run.assert_not_called()
    assert (fake_home / ".claude" / "skills" / "herdr").is_symlink()
    assert lock.read_text() == lock_text
