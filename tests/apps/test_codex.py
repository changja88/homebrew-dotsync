from pathlib import Path
import json
import subprocess
import pytest

# Every Codex sync now asks the Codex CLI for installed plugins; answer from
# an in-memory fake (empty by default) instead of the real binary.
pytestmark = pytest.mark.usefixtures("fake_codex_cli")


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


def test_sync_from_removes_stale_optional_items_when_local_items_missing(
    fake_home, tmp_path
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    for name in (
        "AGENTS.md",
        "AGENTS.override.md",
        "hooks.json",
        "requirements.toml",
    ):
        (stored / name).write_text("STALE\n")
    (stored / "rules").mkdir()
    (stored / "rules" / "stale.rules").write_text("stale\n")
    (stored / "skills").mkdir()
    (stored / "skills" / "stale").mkdir()
    (stored / "skills" / "stale" / "SKILL.md").write_text("# stale\n")

    _codex_app().sync_from(target)

    for name in (
        "AGENTS.md",
        "AGENTS.override.md",
        "hooks.json",
        "requirements.toml",
        "rules",
        "skills",
    ):
        assert not (stored / name).exists()


def test_sync_from_copies_optional_files_when_present(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
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
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "rules").mkdir()
    (cdir / "rules" / "default.rules").write_text("allow\n")
    target = tmp_path / "configs"
    (target / "codex" / "rules").mkdir(parents=True)
    (target / "codex" / "rules" / "stale.rules").write_text("stale\n")

    _codex_app().sync_from(target)

    assert (target / "codex" / "rules" / "default.rules").read_text() == "allow\n"
    assert not (target / "codex" / "rules" / "stale.rules").exists()


def test_sync_from_skips_symlink_in_rules_directory_and_warns(fake_home, tmp_path):
    outside = tmp_path / "secret.rules"
    outside.write_text("secret\n")
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "rules").mkdir()
    (cdir / "rules" / "leak.rules").symlink_to(outside)
    (cdir / "rules" / "safe.rules").write_text("safe\n")
    target = tmp_path / "configs"
    target.mkdir()

    app = _codex_app()
    app.sync_from(target)

    assert (target / "codex" / "rules" / "safe.rules").read_text() == "safe\n"
    assert not (target / "codex" / "rules" / "leak.rules").is_symlink()
    assert not (target / "codex" / "rules" / "leak.rules").exists()
    assert app.warnings == ["rules/leak.rules is a symlink; skipped"]


def test_sync_from_refuses_symlink_stored_app_root(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    target = tmp_path / "configs"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "codex").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_from(target)

    assert not (outside / "config.toml").exists()


def test_sync_from_mirrors_user_skills_but_excludes_system_skills(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "skills" / "mine").mkdir(parents=True)
    (cdir / "skills" / "mine" / "SKILL.md").write_text("# mine\n")
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("# builtin\n")
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    assert (target / "codex" / "skills" / "mine" / "SKILL.md").read_text() == "# mine\n"
    assert not (target / "codex" / "skills" / ".system").exists()


def test_sync_from_unlinks_stored_system_skills_symlink(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "skills" / "mine").mkdir(parents=True)
    (cdir / "skills" / "mine" / "SKILL.md").write_text("# mine\n")
    target = tmp_path / "configs"
    stored_skills = target / "codex" / "skills"
    (stored_skills / "mine").mkdir(parents=True)
    (stored_skills / "mine" / "SKILL.md").write_text("# old\n")
    outside = tmp_path / "outside-system"
    outside.mkdir()
    (outside / "secret").write_text("secret\n")
    (stored_skills / ".system").symlink_to(outside, target_is_directory=True)

    _codex_app().sync_from(target)

    assert not (stored_skills / ".system").exists()
    assert (outside / "secret").read_text() == "secret\n"


def test_sync_to_backs_up_and_writes_config_and_agents(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.md").write_text("OLD AGENTS\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "AGENTS.md").write_text("NEW AGENTS\n")

    _codex_app().sync_to(target)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "AGENTS.md").read_text() == "NEW AGENTS\n"


def test_sync_to_refuses_symlink_stored_app_root(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.toml").write_text("NEW\n")
    (target / "codex").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_to(target)

    assert (cdir / "config.toml").read_text() == "OLD\n"


def test_sync_to_refuses_symlink_stored_optional_file_before_mutating_config(
    fake_home, tmp_path
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    outside = tmp_path / "outside-agents.md"
    outside.write_text("SECRET\n")
    (stored / "AGENTS.md").symlink_to(outside)

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_to(target)

    assert (cdir / "config.toml").read_text() == "OLD\n"
    assert outside.read_text() == "SECRET\n"


def test_sync_to_without_stored_agents_keeps_local_agents(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.md").write_text("KEEP ME\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")

    _codex_app().sync_to(target)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "AGENTS.md").read_text() == "KEEP ME\n"


def test_sync_to_restores_optional_files_with_backup(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.override.md").write_text("OLD OVERRIDE\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "AGENTS.override.md").write_text("NEW OVERRIDE\n")

    _codex_app().sync_to(target)

    assert (cdir / "AGENTS.override.md").read_text() == "NEW OVERRIDE\n"


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

    _codex_app().sync_to(target)

    assert (cdir / "rules" / "shared.rules").read_text() == "stored\n"
    assert (cdir / "rules" / "new.rules").read_text() == "new\n"
    assert not (cdir / "rules" / "old.rules").exists()


def test_sync_to_refuses_file_stored_rules_before_backup(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "keep.rules").write_text("keep\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "rules").write_text("not a directory")

    with pytest.raises(RuntimeError, match="directory"):
        _codex_app().sync_to(target)

    assert (cdir / "config.toml").read_text() == "OLD\n"
    assert (cdir / "rules" / "keep.rules").read_text() == "keep\n"


def test_sync_to_keeps_local_rules_symlink_and_skips_it_in_backup(fake_home, tmp_path):
    outside = tmp_path / "outside.rules"
    outside.write_text("SECRET\n")
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "leak.rules").symlink_to(outside)
    target = tmp_path / "configs"
    (target / "codex" / "rules").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "rules" / "safe.rules").write_text("SAFE\n")

    app = _codex_app()
    app.sync_to(target)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "rules" / "safe.rules").read_text() == "SAFE\n"
    assert (cdir / "rules" / "leak.rules").is_symlink()
    assert outside.read_text() == "SECRET\n"
    assert app.warnings == ["rules/leak.rules is a symlink; skipped"]


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

    _codex_app().sync_to(target)

    assert (cdir / "skills" / "mine" / "SKILL.md").read_text() == "# mine\n"
    assert (
        cdir / "skills" / ".system" / "builtin" / "SKILL.md"
    ).read_text() == "# builtin\n"


def test_status_clean_when_config_and_agents_match(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "AGENTS.md").write_text("Y")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')
    (target / "codex" / "AGENTS.md").write_text("Y")

    assert _codex_app().status(target).state == "clean"


def test_status_reports_symlink_stored_root_without_reading_target(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("LOCAL")
    target = tmp_path / "configs"
    target.mkdir()
    outside = tmp_path / "outside-codex"
    outside.mkdir()
    (outside / "config.toml").write_text("SECRET")
    (target / "codex").symlink_to(outside, target_is_directory=True)

    status = _codex_app().status(target)

    assert status.state == "unknown"
    assert "symlink" in status.details


def test_status_dirty_when_agents_differ(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "AGENTS.md").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')
    (target / "codex" / "AGENTS.md").write_text("STORED")

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "AGENTS.md" in status.details


def test_status_dirty_when_optional_file_exists_on_one_side(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "AGENTS.override.md").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "AGENTS.override.md" in status.details


def test_status_dirty_when_rules_directory_differs(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "rules").mkdir()
    (cdir / "rules" / "default.rules").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex" / "rules").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')
    (target / "codex" / "rules" / "default.rules").write_text("STORED")

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "rules/default.rules" in status.details


def test_status_ignores_system_skills(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "skills" / ".system" / "builtin").mkdir(parents=True)
    (cdir / "skills" / ".system" / "builtin" / "SKILL.md").write_text("LOCAL")
    target = tmp_path / "configs"
    (target / "codex" / "skills").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')

    assert _codex_app().status(target).state == "clean"


def test_status_ignores_agents_when_missing_on_both_sides(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')

    assert _codex_app().status(target).state == "clean"


def test_status_missing_when_config_absent(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()

    assert _codex_app().status(target).state == "missing"


def test_is_present_locally_true_when_config_exists(fake_home):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = \"x\"\n')

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

    changes = {c.label: c for c in plan.changes}
    assert changes["config.toml"].kind == "update"
    assert changes["AGENTS.md"].kind == "create"
    # config.toml is sanitized TOML on both sides — a line-count summary is
    # safe and meaningful (unlike claude's mcp-servers.json, which compares
    # structurally different files).
    assert changes["config.toml"].details.startswith("+")
    assert changes["config.toml"].details != ""


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
    (stored_skills / ".system" / "generated" / "SKILL.md").write_text("# generated\n")

    plan = app.plan_from(target)

    skills = [c for c in plan.changes if c.label == "skills/"][0]
    assert skills.kind == "update"
    assert "purge" in skills.details
    assert ".system" in skills.details


def test_plan_from_reports_broken_codex_skills_system_symlink_purge(
    fake_home, tmp_path
):
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
    (stored_skills / ".system").symlink_to(tmp_path / "missing-system")

    plan = app.plan_from(target)

    skills = [c for c in plan.changes if c.label == "skills/"][0]
    assert skills.kind == "update"
    assert "purge" in skills.details
    assert ".system" in skills.details


def test_plan_from_reports_stale_optional_item_removals(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config")
    stored = target / "codex"
    stored.mkdir(parents=True)
    for name in (
        "AGENTS.md",
        "AGENTS.override.md",
        "hooks.json",
        "requirements.toml",
    ):
        (stored / name).write_text("stale")
    (stored / "rules").mkdir()
    (stored / "rules" / "stale.rules").write_text("stale")
    (stored / "skills").mkdir()
    (stored / "skills" / "old" / "SKILL.md").parent.mkdir(parents=True)
    (stored / "skills" / "old" / "SKILL.md").write_text("# stale")

    plan = app.plan_from(target)

    removals = {c.label: c for c in plan.changes if c.kind == "remove"}
    assert removals["AGENTS.md"].dest == stored / "AGENTS.md"
    assert removals["AGENTS.override.md"].dest == stored / "AGENTS.override.md"
    assert removals["hooks.json"].dest == stored / "hooks.json"
    assert removals["requirements.toml"].dest == stored / "requirements.toml"
    assert removals["rules/"].dest == stored / "rules"
    assert removals["skills/"].dest == stored / "skills"


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


def test_codex_mirror_tree_replaces_directory_with_file(tmp_path):
    from dotsync.apps.codex import CodexApp

    app = CodexApp()
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "conflict.rules").write_text("file\n")
    (dst / "conflict.rules").mkdir()
    (dst / "conflict.rules" / "old.rules").write_text("old\n")

    app._mirror_tree(src, dst)

    assert (dst / "conflict.rules").is_file()
    assert (dst / "conflict.rules").read_text() == "file\n"


def test_sync_from_excludes_dynamic_serena_mcp_from_config(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        'model = "gpt-5.2"\n\n'
        "[mcp_servers.serena]\n"
        'url = "http://127.0.0.1:9123/mcp"\n\n'
        "[mcp_servers.playwright]\n"
        'command = "npx"\n'
    )
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    stored = (target / "codex" / "config.toml").read_text()
    assert "mcp_servers.serena" not in stored
    assert "mcp_servers.playwright" in stored


def test_sync_to_excludes_dynamic_serena_mcp_from_local_config(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "old"\n')
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text(
        'model = "gpt-5.2"\n\n[mcp_servers.serena]\nurl = "http://127.0.0.1:9123/mcp"\n'
    )

    _codex_app().sync_to(target)

    assert "mcp_servers.serena" not in (cdir / "config.toml").read_text()


def test_codex_status_ignores_dynamic_serena_mcp_difference(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        'model = "gpt-5.2"\n\n[mcp_servers.serena]\nurl = "http://127.0.0.1:9123/mcp"\n'
    )
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text('model = "gpt-5.2"\n')

    assert _codex_app().status(target).state == "clean"


def test_plan_from_marks_update_when_stored_has_only_stale_serena_url(
    fake_home, tmp_path
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "gpt-5.2"\n')
    stored = tmp_path / "configs" / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text(
        'model = "gpt-5.2"\n\n[mcp_servers.serena]\nurl = "http://127.0.0.1:9123/mcp"\n'
    )

    plan = _codex_app().plan_from(tmp_path / "configs")

    assert {c.label: c.kind for c in plan.changes}["config.toml"] == "update"


def test_plan_to_marks_update_when_local_has_only_stale_serena_url(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        'model = "gpt-5.2"\n\n[mcp_servers.serena]\nurl = "http://127.0.0.1:9123/mcp"\n'
    )
    stored = tmp_path / "configs" / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text('model = "gpt-5.2"\n')

    plan = _codex_app().plan_to(tmp_path / "configs")

    assert {c.label: c.kind for c in plan.changes}["config.toml"] == "update"


def test_status_ignores_skill_bak_file(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    (cdir / "skills" / "graphify").mkdir(parents=True)
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "skills" / "graphify" / "SKILL.md").write_text("# skill\n")
    (cdir / "skills" / "graphify" / "SKILL.md.bak").write_text("# older\n")
    target = tmp_path / "configs"
    (target / "codex" / "skills" / "graphify").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')
    (target / "codex" / "skills" / "graphify" / "SKILL.md").write_text("# skill\n")

    assert _codex_app().status(target).state == "clean"


def _codex_app_ignoring(tmp_path: Path, *names: str):
    from dotsync.apps.codex import CodexApp
    from dotsync.config import Config

    cfg = Config(
        dir=tmp_path,
        apps=["codex"],
        app_options={"codex": {"skills_ignore": list(names)}},
    )
    return CodexApp.from_config(cfg)


def test_sync_to_leaves_configured_ignored_skill_alone(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    (cdir / "skills" / "graphify").mkdir(parents=True)
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "skills" / "graphify" / "SKILL.md").write_text("# v2\n")
    target = tmp_path / "configs"
    (target / "codex" / "skills" / "graphify").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "skills" / "graphify" / "SKILL.md").write_text("# v1\n")

    _codex_app_ignoring(tmp_path, "graphify").sync_to(target)

    assert (cdir / "skills" / "graphify" / "SKILL.md").read_text() == "# v2\n"


def test_sync_from_purges_configured_ignored_skill_from_folder(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    (cdir / "skills" / "graphify").mkdir(parents=True)
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "skills" / "graphify" / "SKILL.md").write_text("# v2\n")
    target = tmp_path / "configs"
    (target / "codex" / "skills" / "graphify").mkdir(parents=True)
    (target / "codex" / "skills" / "graphify" / "SKILL.md").write_text("# v1\n")

    _codex_app_ignoring(tmp_path, "graphify").sync_from(target)

    assert not (target / "codex" / "skills" / "graphify").exists()


def test_status_ignores_configured_ignored_skill(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    (cdir / "skills" / "graphify").mkdir(parents=True)
    (cdir / "config.toml").write_text('model = \"x\"\n')
    (cdir / "skills" / "graphify" / "SKILL.md").write_text("# v2\n")
    target = tmp_path / "configs"
    (target / "codex" / "skills" / "graphify").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = \"x\"\n')
    (target / "codex" / "skills" / "graphify" / "SKILL.md").write_text("# v1\n")

    assert _codex_app_ignoring(tmp_path, "graphify").status(target).state == "clean"


USER_MARKET = "https://github.com/me/my-market.git"


def _config_with_plugin_tables(home: Path, settings: str = 'model = "gpt-5.5"\n') -> str:
    return settings + f"""
[marketplaces.openai-bundled]
source_type = "local"
source = "{home}/.codex/.tmp/bundled-marketplaces/openai-bundled"

[marketplaces.my-market]
last_updated = "2026-08-15T16:53:43Z"
source_type = "git"
source = "{USER_MARKET}"
ref = "main"

[plugins."mine@my-market"]
enabled = true
"""


def _plugin(plugin_id: str, *, policy: str = "AVAILABLE", source: dict | None = None) -> dict:
    item = {"pluginId": plugin_id, "enabled": True, "installPolicy": policy}
    if source is not None:
        item["marketplaceSource"] = source
    return item


def _write_stored_manifest(target: Path, marketplaces: list, plugins: list) -> Path:
    path = target / "codex" / "plugins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"marketplaces": marketplaces, "plugins": plugins}))
    return path


def test_sync_from_stores_config_without_marketplace_and_plugin_tables(
    fake_home, tmp_path
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(_config_with_plugin_tables(fake_home))
    target = tmp_path / "configs"

    _codex_app().sync_from(target)

    assert (target / "codex" / "config.toml").read_text() == 'model = "gpt-5.5"\n'


def test_sync_from_records_user_plugins_in_plugins_json(
    fake_home, tmp_path, fake_codex_cli
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(_config_with_plugin_tables(fake_home))
    bundled = {
        "sourceType": "local",
        "source": f"{fake_home}/.codex/.tmp/bundled-marketplaces/openai-bundled",
    }
    fake_codex_cli.installed = [
        _plugin("mine@my-market", source={"sourceType": "git", "source": USER_MARKET}),
        _plugin("superpowers@openai-curated-remote"),
        _plugin("pages@openai-curated-remote", policy="INSTALLED_BY_DEFAULT"),
        _plugin("browser@openai-bundled", source=bundled),
    ]
    target = tmp_path / "configs"

    _codex_app().sync_from(target)

    assert json.loads((target / "codex" / "plugins.json").read_text()) == {
        "marketplaces": [{"name": "my-market", "source": USER_MARKET, "ref": "main"}],
        "plugins": ["mine@my-market", "superpowers@openai-curated-remote"],
    }


def test_sync_from_keeps_stored_plugins_json_when_plugin_list_fails(
    fake_home, tmp_path, fake_codex_cli
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "x"\n')
    target = tmp_path / "configs"
    stored = _write_stored_manifest(target, [], ["mine@my-market"])
    before = stored.read_text()
    fake_codex_cli.failing = {"plugin list"}
    app = _codex_app()

    app.sync_from(target)

    assert stored.read_text() == before
    assert any("plugins.json" in w for w in app.warnings)


def test_sync_to_keeps_local_plugin_tables_and_applies_stored_settings(
    fake_home, tmp_path
):
    import tomllib

    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        _config_with_plugin_tables(fake_home, 'model = "old"\n')
    )
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = "new"\n')

    _codex_app().sync_to(target)

    local = tomllib.loads((cdir / "config.toml").read_text())
    assert local["model"] == "new"
    assert set(local["marketplaces"]) == {"openai-bundled", "my-market"}
    assert local["marketplaces"]["my-market"]["last_updated"] == "2026-08-15T16:53:43Z"
    assert local["plugins"] == {"mine@my-market": {"enabled": True}}


def _apply_manifest_setup(fake_home: Path, tmp_path: Path) -> Path:
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "x"\n')
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = "x"\n')
    _write_stored_manifest(
        target,
        [{"name": "my-market", "source": "me/my-market", "ref": "main", "sparse": ["plugins"]}],
        ["mine@my-market", "superpowers@openai-curated-remote"],
    )
    return target


def test_sync_to_adds_missing_marketplace_then_installs_missing_plugins(
    fake_home, tmp_path, fake_codex_cli
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    fake_codex_cli.marketplace_sources = {"me/my-market": "my-market"}

    _codex_app().sync_to(target)

    assert fake_codex_cli.commands("plugin", "marketplace", "add") == [
        ["plugin", "marketplace", "add", "me/my-market", "--ref", "main",
         "--sparse", "plugins", "--json"]
    ]
    assert fake_codex_cli.commands("plugin", "add") == [
        ["plugin", "add", "mine@my-market", "--json"],
        ["plugin", "add", "superpowers@openai-curated-remote", "--json"],
    ]
    first_plugin_add = next(
        i for i, c in enumerate(fake_codex_cli.calls) if c[1:3] == ["plugin", "add"]
    )
    marketplace_add = next(
        i for i, c in enumerate(fake_codex_cli.calls) if c[1:4] == ["plugin", "marketplace", "add"]
    )
    assert marketplace_add < first_plugin_add


def test_sync_to_twice_installs_each_plugin_once(fake_home, tmp_path, fake_codex_cli):
    target = _apply_manifest_setup(fake_home, tmp_path)
    fake_codex_cli.marketplace_sources = {"me/my-market": "my-market"}

    _codex_app().sync_to(target)
    _codex_app().sync_to(target)

    assert len(fake_codex_cli.commands("plugin", "marketplace", "add")) == 1
    assert len(fake_codex_cli.commands("plugin", "add")) == 2


def test_sync_to_skips_plugins_whose_marketplace_could_not_be_added(
    fake_home, tmp_path, fake_codex_cli
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    app = _codex_app()

    app.sync_to(target)

    assert fake_codex_cli.commands("plugin", "add") == [
        ["plugin", "add", "superpowers@openai-curated-remote", "--json"]
    ]
    assert any("mine@my-market" in w for w in app.warnings)


def test_sync_to_warns_and_keeps_files_when_codex_cli_missing(
    fake_home, tmp_path, fake_codex_cli
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    (target / "codex" / "config.toml").write_text('model = "new"\n')
    fake_codex_cli.missing = True
    app = _codex_app()

    app.sync_to(target)

    assert (_codex_dir(fake_home) / "config.toml").read_text() == 'model = "new"\n'
    assert any("codex" in w for w in app.warnings)


def test_sync_to_warns_on_invalid_plugins_json_without_running_codex(
    fake_home, tmp_path, fake_codex_cli
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    (target / "codex" / "plugins.json").write_text('{"plugins": "mine@my-market"}')
    app = _codex_app()

    app.sync_to(target)

    assert fake_codex_cli.calls == []
    assert any("plugins.json" in w for w in app.warnings)


def _status_setup(fake_home: Path, tmp_path: Path, fake_codex_cli, plugins: list) -> Path:
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(_config_with_plugin_tables(fake_home, 'model = "x"\n'))
    fake_codex_cli.installed = [
        _plugin("mine@my-market", source={"sourceType": "git", "source": USER_MARKET})
    ]
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = "x"\n')
    _write_stored_manifest(
        target, [{"name": "my-market", "source": USER_MARKET, "ref": "main"}], plugins
    )
    return target


def test_status_clean_when_only_plugin_tables_differ(fake_home, tmp_path, fake_codex_cli):
    target = _status_setup(fake_home, tmp_path, fake_codex_cli, ["mine@my-market"])

    assert _codex_app().status(target).state == "clean"


def test_status_dirty_when_recorded_plugins_differ(fake_home, tmp_path, fake_codex_cli):
    target = _status_setup(fake_home, tmp_path, fake_codex_cli, [])

    status = _codex_app().status(target)

    assert status.state == "dirty"
    assert "plugins.json" in status.details


def test_plan_from_reports_plugins_json_update(fake_home, tmp_path, fake_codex_cli):
    target = _status_setup(fake_home, tmp_path, fake_codex_cli, [])

    plan = _codex_app().plan_from(target)

    change = {c.label: c for c in plan.changes}["plugins.json"]
    assert change.kind == "update"
    assert "+mine@my-market" in change.details


def test_plan_to_lists_marketplaces_and_plugins_to_install(
    fake_home, tmp_path, fake_codex_cli
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    fake_codex_cli.installed = [_plugin("superpowers@openai-curated-remote")]

    plan = _codex_app().plan_to(target)

    change = {c.label: c for c in plan.changes}["plugins.json"]
    assert change.kind == "update"
    assert "marketplace my-market" in change.details
    assert "mine@my-market" in change.details
    assert "superpowers" not in change.details


def test_plan_to_reports_unchanged_plugins_when_everything_is_installed(
    fake_home, tmp_path, fake_codex_cli
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    fake_codex_cli.marketplaces = [{"name": "my-market"}]
    fake_codex_cli.installed = [
        _plugin("mine@my-market"),
        _plugin("superpowers@openai-curated-remote"),
    ]

    plan = _codex_app().plan_to(target)

    assert {c.label: c.kind for c in plan.changes}["plugins.json"] == "unchanged"


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", '{"installed": "nope"}', '{"installed": [{"pluginId": 3}]}'],
)
def test_sync_to_skips_restore_on_unexpected_plugin_list_output(
    fake_home, tmp_path, fake_codex_cli, raw
):
    target = _apply_manifest_setup(fake_home, tmp_path)
    fake_codex_cli.marketplaces = [{"name": "my-market"}]
    fake_codex_cli.stdout = {"plugin list": raw}
    app = _codex_app()

    app.sync_to(target)

    assert fake_codex_cli.commands("plugin", "add") == []
    assert any("plugins restore skipped" in w for w in app.warnings)


def test_sync_from_keeps_plugins_json_on_unexpected_plugin_list_output(
    fake_home, tmp_path, fake_codex_cli
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "x"\n')
    target = tmp_path / "configs"
    stored = _write_stored_manifest(target, [], ["mine@my-market"])
    before = stored.read_text()
    fake_codex_cli.stdout = {"plugin list": '{"installed": [{"name": "half"}]}'}
    app = _codex_app()

    app.sync_from(target)

    assert stored.read_text() == before
    assert any("plugins.json not updated" in w for w in app.warnings)


def test_sync_from_is_quiet_about_npx_skill_links_when_skills_app_is_tracked(
    fake_home, tmp_path
):
    from dotsync.apps.codex import CodexApp
    from dotsync.config import Config

    cdir = _codex_dir(fake_home)
    (cdir / "skills").mkdir(parents=True)
    (cdir / "config.toml").write_text('model = "x"\n')
    canonical = fake_home / ".agents" / "skills" / "herdr"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("# herdr\n")
    (cdir / "skills" / "herdr").symlink_to(canonical, target_is_directory=True)
    target = tmp_path / "configs"
    app = CodexApp.from_config(Config(dir=tmp_path, apps=["codex", "skills"]))

    app.sync_from(target)

    assert app.warnings == []


def test_pull_leaves_local_config_untouched_when_settings_match(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    local_text = (
        'model = "x"\n\n'
        '[plugins."mine@my-market"]\nenabled = true\n\n'
        '[tui]\ntheme = "dark"\n'
    )
    (cdir / "config.toml").write_text(local_text)
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text('model = "x"\n\n[tui]\ntheme = "dark"\n')
    app = _codex_app()

    plan = app.plan_to(target)
    app.sync_to(target)

    assert {c.label: c.kind for c in plan.changes}["config.toml"] == "unchanged"
    assert (cdir / "config.toml").read_text() == local_text
