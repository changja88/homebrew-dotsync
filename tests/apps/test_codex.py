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


def test_sync_from_preserves_stored_agents_when_local_agents_missing(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "AGENTS.md").write_text("STALE\n")

    _codex_app().sync_from(target)

    assert (target / "codex" / "AGENTS.md").read_text() == "STALE\n"


def test_sync_from_copies_optional_files_when_present(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    (cdir / "AGENTS.override.md").write_text("# override\n")
    (cdir / "hooks.json").write_text("{}\n")
    (cdir / "requirements.toml").write_text("[features]\n")
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    stored = target / "codex"
    assert (stored / "AGENTS.override.md").read_text() == "# override\n"
    assert (stored / "hooks.json").read_text() == "{}\n"
    assert (stored / "requirements.toml").read_text() == "[features]\n"


def test_sync_from_mirrors_rules_directory(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "default.rules").write_text("allow\n")
    target = tmp_path / "configs"
    (target / "codex" / "rules").mkdir(parents=True)
    (target / "codex" / "rules" / "stale.rules").write_text("stale\n")

    _codex_app().sync_from(target)

    assert (target / "codex" / "rules" / "default.rules").read_text() == "allow\n"
    assert not (target / "codex" / "rules" / "stale.rules").exists()


def test_sync_from_mirrors_user_skills_but_excludes_system_skills(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    (cdir / "skills" / "mine").mkdir(parents=True)
    (cdir / "skills" / "mine" / "SKILL.md").write_text("# mine\n")
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("# builtin\n")
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    assert (target / "codex" / "skills" / "mine" / "SKILL.md").read_text() == "# mine\n"
    assert not (target / "codex" / "skills" / ".system").exists()


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


def test_sync_to_restores_optional_files_with_backup(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.override.md").write_text("OLD OVERRIDE\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "AGENTS.override.md").write_text("NEW OVERRIDE\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "AGENTS.override.md").read_text() == "NEW OVERRIDE\n"
    assert (backup / "codex" / "AGENTS.override.md").read_text() == "OLD OVERRIDE\n"


def test_sync_to_mirrors_rules_directory_with_backup(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "old.rules").write_text("old\n")
    (cdir / "rules" / "shared.rules").write_text("local\n")
    target = tmp_path / "configs"
    (target / "codex" / "rules").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "rules" / "shared.rules").write_text("stored\n")
    (target / "codex" / "rules" / "new.rules").write_text("new\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "rules" / "shared.rules").read_text() == "stored\n"
    assert (cdir / "rules" / "new.rules").read_text() == "new\n"
    assert not (cdir / "rules" / "old.rules").exists()
    assert (backup / "codex" / "rules" / "old.rules").read_text() == "old\n"
    assert (backup / "codex" / "rules" / "shared.rules").read_text() == "local\n"


def test_sync_to_preserves_local_system_skills(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("# builtin\n")
    target = tmp_path / "configs"
    (target / "codex" / "skills" / "mine").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "skills" / "mine" / "SKILL.md").write_text("# mine\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "skills" / "mine" / "SKILL.md").read_text() == "# mine\n"
    assert (cdir / "skills" / ".system" / "builtin" / "SKILL.md").read_text() == "# builtin\n"


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


def test_status_dirty_when_optional_file_exists_on_one_side(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")
    (cdir / "AGENTS.override.md").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("X")

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "AGENTS.override.md" in status.details


def test_status_dirty_when_rules_directory_differs(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "default.rules").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex" / "rules").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("X")
    (target / "codex" / "rules" / "default.rules").write_text("STORED")

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "rules/default.rules" in status.details


def test_status_ignores_system_skills(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X")
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex" / "skills").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("X")

    assert _codex_app().status(target).state == "clean"


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


def test_plan_from_reports_codex_directory_mirror_removals(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config")
    (codex_dir / "rules").mkdir()
    (codex_dir / "rules" / "keep.rules").write_text("new")
    stored_rules = target / "codex" / "rules"
    stored_rules.mkdir(parents=True)
    (stored_rules / "old.rules").write_text("old")

    plan = app.plan_from(target)

    rules = [c for c in plan.changes if c.label == "rules/"][0]
    assert rules.kind == "update"
    assert "1 create" in rules.details
    assert "1 remove" in rules.details


def test_plan_to_reports_codex_optional_file_update(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("local")
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("stored")
    (stored / "AGENTS.md").write_text("stored agents")

    plan = app.plan_to(target)

    labels = {c.label: c.kind for c in plan.changes}
    assert labels["config.toml"] == "update"
    assert labels["AGENTS.md"] == "create"


def test_plan_from_reports_codex_skills_system_purge(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config")
    (codex_dir / "skills" / "user").mkdir(parents=True)
    (codex_dir / "skills" / "user" / "SKILL.md").write_text("# user\n")
    stored_skills = target / "codex" / "skills"
    (stored_skills / "user").mkdir(parents=True)
    (stored_skills / "user" / "SKILL.md").write_text("# user\n")
    (stored_skills / ".system" / "generated").mkdir(parents=True)
    (stored_skills / ".system" / "generated" / "SKILL.md").write_text(
        "# generated\n"
    )

    plan = app.plan_from(target)

    skills = [c for c in plan.changes if c.label == "skills/"][0]
    assert skills.kind == "update"
    assert "purge" in skills.details
    assert ".system" in skills.details


def test_plan_from_reports_empty_directory_creation(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config")
    (codex_dir / "rules").mkdir()

    plan = app.plan_from(target)

    rules = [c for c in plan.changes if c.label == "rules/"][0]
    assert rules.kind == "create"
