import json
from pathlib import Path

import pytest

from dotsync.apps.skills import SkillsApp

HERDR = {
    "source": "ogulcancelik/herdr",
    "sourceType": "github",
    "skillPath": "skills/herdr/SKILL.md",
}
ARCHIFY = {
    "source": "tt-a1i/archify",
    "sourceType": "github",
    "skillPath": "archify/SKILL.md",
}


def _agents(home: Path) -> Path:
    return home / ".agents"


def _write_lock(home: Path, skills: dict, extra: dict | None = None) -> Path:
    lock = _agents(home) / ".skill-lock.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 3, "skills": skills}
    doc.update(extra or {})
    lock.write_text(json.dumps(doc))
    return lock


def _install(home: Path, name: str, agents: tuple[str, ...] = ("claude-code",)) -> None:
    """Lay out what `npx skills add -g` leaves behind: canonical dir + agent links."""
    canonical = _agents(home) / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "SKILL.md").write_text(f"# {name}\n")
    dirs = {
        "claude-code": home / ".claude" / "skills",
        "codex": home / ".codex" / "skills",
    }
    for agent in agents:
        dirs[agent].mkdir(parents=True, exist_ok=True)
        (dirs[agent] / name).symlink_to(canonical, target_is_directory=True)


def test_is_present_locally_requires_lock_file(fake_home):
    assert SkillsApp.is_present_locally() is False
    _write_lock(fake_home, {})
    assert SkillsApp.is_present_locally() is True


def test_sync_from_records_sources_and_linked_agents_without_the_lock(
    fake_home, tmp_path
):
    _write_lock(
        fake_home,
        {"herdr": HERDR, "archify": ARCHIFY},
        extra={"githubToken": "ghp_secret"},
    )
    _install(fake_home, "herdr", ("claude-code", "codex"))
    _install(fake_home, "archify", ("claude-code",))
    target = tmp_path / "sync"
    target.mkdir()

    SkillsApp().sync_from(target)

    text = (target / "skills" / "skills.json").read_text()
    assert json.loads(text) == {
        "archify": {
            "source": "tt-a1i/archify",
            "sourceType": "github",
            "agents": ["claude-code"],
        },
        "herdr": {
            "source": "ogulcancelik/herdr",
            "sourceType": "github",
            "agents": ["claude-code", "codex"],
        },
    }
    assert "ghp_secret" not in text
    assert text.endswith("\n")
    assert sorted(p.name for p in (target / "skills").iterdir()) == ["skills.json"]


def test_sync_from_ignores_agent_links_that_point_elsewhere(fake_home, tmp_path):
    _write_lock(fake_home, {"herdr": HERDR})
    (_agents(fake_home) / "skills" / "herdr").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (fake_home / ".claude" / "skills").mkdir(parents=True)
    (fake_home / ".claude" / "skills" / "herdr").symlink_to(
        elsewhere, target_is_directory=True
    )
    target = tmp_path / "sync"
    target.mkdir()

    SkillsApp().sync_from(target)

    manifest = json.loads((target / "skills" / "skills.json").read_text())
    assert manifest["herdr"]["agents"] == []


def test_sync_from_without_lock_records_no_skills(fake_home, tmp_path):
    target = tmp_path / "sync"
    target.mkdir()

    SkillsApp().sync_from(target)

    assert (target / "skills" / "skills.json").read_text() == "{}\n"


def test_sync_from_refuses_symlinked_lock(fake_home, tmp_path):
    outside = tmp_path / "outside-lock.json"
    outside.write_text(json.dumps({"skills": {}}))
    _agents(fake_home).mkdir()
    (_agents(fake_home) / ".skill-lock.json").symlink_to(outside)
    target = tmp_path / "sync"
    target.mkdir()

    with pytest.raises(RuntimeError, match="symlink"):
        SkillsApp().sync_from(target)

    assert not (target / "skills" / "skills.json").exists()


def test_sync_from_invalid_lock_raises(fake_home, tmp_path):
    lock = _agents(fake_home) / ".skill-lock.json"
    lock.parent.mkdir()
    lock.write_text("{not json")
    target = tmp_path / "sync"
    target.mkdir()

    with pytest.raises(RuntimeError, match="invalid JSON"):
        SkillsApp().sync_from(target)


def test_status_missing_then_clean_then_dirty(fake_home, tmp_path):
    target = tmp_path / "sync"
    target.mkdir()
    _write_lock(fake_home, {"herdr": HERDR})
    _install(fake_home, "herdr")
    app = SkillsApp()

    assert app.status(target).state == "missing"
    app.sync_from(target)
    assert app.status(target).state == "clean"

    _write_lock(fake_home, {"herdr": HERDR, "archify": ARCHIFY})
    _install(fake_home, "archify")
    status = app.status(target)

    assert status.state == "dirty"
    assert status.details == "+archify"


def test_status_reports_changed_agents(fake_home, tmp_path):
    target = tmp_path / "sync"
    target.mkdir()
    _write_lock(fake_home, {"herdr": HERDR})
    _install(fake_home, "herdr", ("claude-code",))
    app = SkillsApp()
    app.sync_from(target)
    (fake_home / ".codex" / "skills").mkdir(parents=True)
    (fake_home / ".codex" / "skills" / "herdr").symlink_to(
        _agents(fake_home) / "skills" / "herdr", target_is_directory=True
    )

    status = app.status(target)

    assert status.state == "dirty"
    assert status.details == "~herdr"


def test_status_unknown_for_unreadable_stored_manifest(fake_home, tmp_path):
    target = tmp_path / "sync"
    (target / "skills").mkdir(parents=True)
    (target / "skills" / "skills.json").write_text("[]")
    _write_lock(fake_home, {})

    status = SkillsApp().status(target)

    assert status.state == "unknown"
    assert "object" in status.details


def test_plan_from_reports_create_then_unchanged(fake_home, tmp_path):
    _write_lock(fake_home, {"herdr": HERDR})
    _install(fake_home, "herdr")
    target = tmp_path / "sync"
    target.mkdir()
    app = SkillsApp()

    assert [c.kind for c in app.plan_from(target).changes] == ["create"]
    app.sync_from(target)
    assert [c.kind for c in app.plan_from(target).changes] == ["unchanged"]


def test_plan_from_reports_unknown_for_symlinked_lock(fake_home, tmp_path):
    outside = tmp_path / "outside-lock.json"
    outside.write_text("{}")
    _agents(fake_home).mkdir()
    (_agents(fake_home) / ".skill-lock.json").symlink_to(outside)
    target = tmp_path / "sync"
    target.mkdir()

    change = SkillsApp().plan_from(target).changes[0]

    assert change.kind == "unknown"
    assert "symlink" in change.details


# ----- apply --------------------------------------------------------------

from unittest.mock import patch  # noqa: E402


def _stored_manifest(target: Path, manifest: dict) -> Path:
    path = target / "skills" / "skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path


def _entry(
    agents: list[str],
    source: str = "ogulcancelik/herdr",
    source_type: str = "github",
) -> dict:
    return {"source": source, "sourceType": source_type, "agents": agents}


def _ok_run(run) -> None:
    run.return_value.returncode = 0
    run.return_value.stdout = ""
    run.return_value.stderr = ""


def test_sync_to_reinstalls_missing_skill_with_exact_argv(fake_home, tmp_path):
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["claude-code", "codex"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        _ok_run(run)
        app = SkillsApp()
        app.sync_to(target, backup)

    assert [c.args[0] for c in run.call_args_list] == [
        [
            "npx", "-y", "skills", "add", "ogulcancelik/herdr",
            "--global", "--skill", "herdr",
            "--agent", "claude-code", "--agent", "codex", "--yes",
        ]
    ]
    assert app.warnings == []


def test_sync_to_skips_skill_already_present(fake_home, tmp_path):
    _install(fake_home, "herdr", ("claude-code",))
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["claude-code"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        SkillsApp().sync_to(target, backup)

    run.assert_not_called()


def test_sync_to_reinstalls_when_an_agent_link_is_missing(fake_home, tmp_path):
    _install(fake_home, "herdr", ("claude-code",))
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["claude-code", "codex"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        _ok_run(run)
        SkillsApp().sync_to(target, backup)

    assert run.call_count == 1


def test_sync_to_only_targets_managed_agents(fake_home, tmp_path):
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["cursor", "claude-code"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        _ok_run(run)
        SkillsApp().sync_to(target, backup)

    argv = run.call_args_list[0].args[0]
    assert argv.count("--agent") == 1
    assert "cursor" not in argv


def test_sync_to_skips_skill_without_managed_agent(fake_home, tmp_path):
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["cursor"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        app = SkillsApp()
        app.sync_to(target, backup)

    run.assert_not_called()
    assert app.warnings == []


def test_sync_to_warns_on_non_github_source(fake_home, tmp_path):
    target = tmp_path / "sync"
    _stored_manifest(
        target,
        {"mine": _entry(["claude-code"], source="/Users/me/mine", source_type="local")},
    )
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        app = SkillsApp()
        app.sync_to(target, backup)

    run.assert_not_called()
    assert app.warnings == [
        "skills add mine skipped: local source cannot be reinstalled"
    ]


def test_sync_to_warns_when_install_fails(fake_home, tmp_path):
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["claude-code"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "boom\n"
        app = SkillsApp()
        app.sync_to(target, backup)

    assert app.warnings == ["skills add herdr failed (rc=1): boom"]


def test_sync_to_warns_once_when_npx_is_missing(fake_home, tmp_path):
    target = tmp_path / "sync"
    _stored_manifest(
        target,
        {
            "herdr": _entry(["claude-code"]),
            "archify": _entry(["claude-code"], source="tt-a1i/archify"),
        },
    )
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        run.side_effect = FileNotFoundError("npx")
        app = SkillsApp()
        app.sync_to(target, backup)

    assert run.call_count == 1
    assert app.warnings == ["skills add skipped: npx not installed"]


def test_sync_to_backs_up_lock_before_running(fake_home, tmp_path):
    lock = _write_lock(fake_home, {"herdr": HERDR}, extra={"githubToken": "x"})
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["claude-code"])})
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        _ok_run(run)
        SkillsApp().sync_to(target, backup)

    assert (backup / "skills" / ".skill-lock.json").read_text() == lock.read_text()


def test_sync_to_rejects_bad_manifest_before_running_anything(fake_home, tmp_path):
    target = tmp_path / "sync"
    (target / "skills").mkdir(parents=True)
    (target / "skills" / "skills.json").write_text('{"herdr": {"agents": []}}')
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.base.subprocess.run") as run:
        with pytest.raises(RuntimeError, match="source"):
            SkillsApp().sync_to(target, backup)

    run.assert_not_called()


def test_sync_to_missing_manifest_raises(fake_home, tmp_path):
    target = tmp_path / "sync"
    target.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(FileNotFoundError, match="skills.json"):
        SkillsApp().sync_to(target, backup)


def test_plan_to_reports_a_kind_per_skill(fake_home, tmp_path):
    _install(fake_home, "archify", ("claude-code",))
    target = tmp_path / "sync"
    _stored_manifest(
        target,
        {
            "archify": _entry(["claude-code"], source="tt-a1i/archify"),
            "herdr": _entry(["claude-code"]),
            "mine": _entry(["claude-code"], source="/Users/me/mine", source_type="local"),
            "solo": _entry(["cursor"], source="x/solo"),
        },
    )

    plan = SkillsApp().plan_to(target)

    assert {c.label: c.kind for c in plan.changes} == {
        "skills add archify": "unchanged",
        "skills add herdr": "create",
        "skills add mine": "unknown",
        "skills add solo": "unknown",
    }
    assert plan.has_changes


def test_plan_to_all_present_has_no_changes(fake_home, tmp_path):
    _install(fake_home, "herdr", ("claude-code",))
    target = tmp_path / "sync"
    _stored_manifest(target, {"herdr": _entry(["claude-code"])})

    plan = SkillsApp().plan_to(target)

    assert plan.changes and not plan.has_changes


def test_plan_to_missing_manifest(fake_home, tmp_path):
    plan = SkillsApp().plan_to(tmp_path / "sync")

    assert [c.kind for c in plan.changes] == ["missing-source"]


def test_registry_lists_skills_after_herdr():
    from dotsync.apps import APP_CLASSES, APP_NAMES

    names = [c.name for c in APP_CLASSES]
    assert "skills" in APP_NAMES
    assert names.index("skills") == names.index("herdr") + 1
