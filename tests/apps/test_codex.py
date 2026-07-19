from pathlib import Path
import subprocess
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


def test_sync_from_removes_stale_optional_items_when_local_items_missing(
    fake_home, tmp_path
):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    for name in (
        "AGENTS.md",
        "AGENTS.override.md",
        "hooks.json",
        "requirements.toml",
        "plugins.toml",
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
        "plugins.toml",
        "rules",
        "skills",
    ):
        assert not (stored / name).exists()


def test_sync_from_copies_optional_files_when_present(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    (cdir / "AGENTS.override.md").write_text("# override\n")
    (cdir / "hooks.json").write_text("{}\n")
    (cdir / "requirements.toml").write_text("[features]\n")
    (cdir / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    stored = target / "codex"
    assert (stored / "AGENTS.override.md").read_text() == "# override\n"
    assert (stored / "hooks.json").read_text() == "{}\n"
    assert (stored / "requirements.toml").read_text() == "[features]\n"
    assert (stored / "plugins.toml").read_text() == 'plugins = ["sample@debug"]\n'


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


def test_sync_from_refuses_symlink_in_rules_directory(fake_home, tmp_path):
    outside = tmp_path / "secret.rules"
    outside.write_text("secret\n")
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
    (cdir / "rules").mkdir()
    (cdir / "rules" / "leak.rules").symlink_to(outside)
    target = tmp_path / "configs"
    target.mkdir()

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_from(target)

    assert not (target / "codex" / "rules" / "leak.rules").exists()


def test_sync_from_refuses_symlink_stored_app_root(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
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


def test_sync_from_unlinks_stored_system_skills_symlink(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("X\n")
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
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "AGENTS.md").read_text() == "NEW AGENTS\n"
    assert (backup / "codex" / "config.toml").read_text() == "OLD\n"
    assert (backup / "codex" / "AGENTS.md").read_text() == "OLD AGENTS\n"


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
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "OLD\n"
    assert not (backup / "codex" / "config.toml").exists()


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
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "OLD\n"
    assert outside.read_text() == "SECRET\n"
    assert not (backup / "codex").exists()


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


def test_sync_to_restores_optional_files_with_backup(fake_home, tmp_path, monkeypatch):
    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    (cdir / "AGENTS.override.md").write_text("OLD OVERRIDE\n")
    (cdir / "plugins.toml").write_text("plugins = []\n")
    target = tmp_path / "configs"
    (target / "codex").mkdir(parents=True)
    (target / "codex" / "config.toml").write_text("NEW\n")
    (target / "codex" / "AGENTS.override.md").write_text("NEW OVERRIDE\n")
    (target / "codex" / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert (cdir / "AGENTS.override.md").read_text() == "NEW OVERRIDE\n"
    assert (cdir / "plugins.toml").read_text() == 'plugins = ["sample@debug"]\n'
    assert (backup / "codex" / "AGENTS.override.md").read_text() == "OLD OVERRIDE\n"
    assert (backup / "codex" / "plugins.toml").read_text() == "plugins = []\n"


def test_sync_to_installs_plugins_from_manifest(fake_home, tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"installed": []}\n', stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert ["codex", "plugin", "add", "sample@debug", "--json"] in calls


def test_sync_to_restores_plugin_marketplaces_before_plugins(
    fake_home, tmp_path, monkeypatch
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "marketplace", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"marketplaces": [{"name": "debug", "root": "/tmp/debug"}]}\n',
                stderr="",
            )
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"installed": []}\n', stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text(
        'plugins = ["sample@debug"]\n\n'
        "[[marketplaces]]\n"
        'name = "debug"\n'
        'source = "owner/repo"\n'
        'ref = "main"\n'
        'sparse = [".agents/plugins", "extras/plugins"]\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    add_marketplace = [
        "codex",
        "plugin",
        "marketplace",
        "add",
        "owner/repo",
        "--ref",
        "main",
        "--sparse",
        ".agents/plugins",
        "--sparse",
        "extras/plugins",
        "--json",
    ]
    list_marketplaces = ["codex", "plugin", "marketplace", "list", "--json"]
    upgrade_marketplace = ["codex", "plugin", "marketplace", "upgrade", "debug"]
    add_plugin = ["codex", "plugin", "add", "sample@debug", "--json"]
    assert calls.index(add_marketplace) < calls.index(list_marketplaces)
    assert calls.index(list_marketplaces) < calls.index(upgrade_marketplace)
    assert calls.index(upgrade_marketplace) < calls.index(add_plugin)
    assert ["codex", "plugin", "marketplace", "upgrade"] not in calls


def test_sync_to_skips_plugins_from_unvalidated_marketplace(
    fake_home, tmp_path, monkeypatch
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "marketplace", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"marketplaces": [{"name": "other", "root": "/tmp/other"}]}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text(
        'plugins = ["sample@debug"]\n\n'
        "[[marketplaces]]\n"
        'name = "debug"\n'
        'source = "owner/repo"\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls
    assert any("debug" in warning for warning in app.warnings)


def test_sync_to_skips_marketplace_plugins_when_marketplace_add_fails(
    fake_home, tmp_path, monkeypatch
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == [
            "codex",
            "plugin",
            "marketplace",
            "add",
            "owner/repo",
            "--json",
        ]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="denied")
        if cmd == ["codex", "plugin", "marketplace", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"marketplaces": [{"name": "debug", "root": "/tmp/debug"}]}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"installed": []}\n', stderr=""
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text(
        'plugins = ["sample@debug"]\n\n'
        "[[marketplaces]]\n"
        'name = "debug"\n'
        'source = "owner/repo"\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert ["codex", "plugin", "marketplace", "list", "--json"] not in calls
    assert ["codex", "plugin", "marketplace", "upgrade", "debug"] not in calls
    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls
    assert any("marketplace add debug failed" in warning for warning in app.warnings)


def test_sync_to_skips_marketplace_plugins_when_marketplace_upgrade_fails(
    fake_home, tmp_path, monkeypatch
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "marketplace", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"marketplaces": [{"name": "debug", "root": "/tmp/debug"}]}\n',
                stderr="",
            )
        if cmd == ["codex", "plugin", "marketplace", "upgrade", "debug"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="network")
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"installed": []}\n', stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text(
        'plugins = ["sample@debug"]\n\n'
        "[[marketplaces]]\n"
        'name = "debug"\n'
        'source = "owner/repo"\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert ["codex", "plugin", "marketplace", "upgrade", "debug"] in calls
    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls
    assert any(
        "marketplace upgrade debug failed" in warning for warning in app.warnings
    )


def test_sync_to_skips_already_installed_plugins(fake_home, tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"installed": [{"pluginId": "sample@debug"}]}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert ["codex", "plugin", "list", "--json"] in calls
    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls


def test_sync_to_skips_plugin_install_when_installed_state_unknown(
    fake_home, tmp_path, monkeypatch
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert ["codex", "plugin", "list", "--json"] in calls
    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls
    assert any("plugin install skipped" in warning for warning in app.warnings)


def test_sync_to_skips_plugin_install_when_installed_key_missing(
    fake_home, tmp_path, monkeypatch
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert ["codex", "plugin", "list", "--json"] in calls
    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls
    assert any("plugin install skipped" in warning for warning in app.warnings)


def test_sync_to_warns_and_keeps_files_when_codex_cli_missing(
    fake_home, tmp_path, monkeypatch
):
    def fake_run(cmd, capture_output, text):
        raise FileNotFoundError("codex")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "NEW\n"
    assert (cdir / "plugins.toml").read_text() == 'plugins = ["sample@debug"]\n'
    assert any("codex" in warning for warning in app.warnings)


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
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(RuntimeError, match="directory"):
        _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "OLD\n"
    assert (cdir / "rules" / "keep.rules").read_text() == "keep\n"
    assert not (backup / "codex" / "rules").exists()


def test_sync_to_refuses_symlink_in_local_rules_before_backup(fake_home, tmp_path):
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
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(RuntimeError, match="symlink"):
        _codex_app().sync_to(target, backup)

    assert (cdir / "config.toml").read_text() == "OLD\n"
    assert outside.read_text() == "SECRET\n"
    assert not (backup / "codex" / "rules" / "leak.rules").exists()


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
    assert (
        cdir / "skills" / ".system" / "builtin" / "SKILL.md"
    ).read_text() == "# builtin\n"


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

    changes = {c.label: c for c in plan.changes}
    assert changes["config.toml"].kind == "update"
    assert changes["AGENTS.md"].kind == "create"
    # config.toml is sanitized TOML on both sides — a line-count summary is
    # safe and meaningful (unlike claude's mcp-servers.json, which compares
    # structurally different files).
    assert changes["config.toml"].details.startswith("+")
    assert changes["config.toml"].details != ""


def test_plan_to_reports_plugin_restore_when_plugins_manifest_exists(
    fake_home, tmp_path
):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config\n")
    (codex_dir / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("config\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')

    plan = app.plan_to(target)

    restore = [c for c in plan.changes if c.label == "plugins restore"][0]
    assert restore.kind == "unknown"
    assert "sample@debug" in restore.details
    assert plan.has_changes


def test_plan_to_reports_invalid_plugins_manifest_shape(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config\n")
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("config\n")
    (stored / "plugins.toml").write_text('[marketplaces]\nplugins = ["sample@debug"]\n')

    plan = app.plan_to(target)

    restore = [c for c in plan.changes if c.label == "plugins restore"][0]
    assert restore.kind == "unknown"
    assert "invalid plugins.toml" in restore.details
    assert "top-level plugins" in restore.details


def test_plan_to_does_not_read_symlinked_plugins_manifest(fake_home, tmp_path):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config\n")
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("config\n")
    outside = tmp_path / "outside-plugins.toml"
    outside.write_text('plugins = ["secret@debug"]\n')
    (stored / "plugins.toml").symlink_to(outside)

    plan = app.plan_to(target)

    changes = [c for c in plan.changes if c.label == "plugins restore"]
    assert len(changes) == 1
    assert changes[0].kind == "unknown"
    assert "symlink" in changes[0].details
    assert "secret@debug" not in changes[0].details


@pytest.mark.parametrize(
    "manifest, detail",
    [
        ("", "top-level plugins"),
        ('plugin = ["sample@debug"]\n', "unknown top-level key"),
        ("plugins = []\nextra = true\n", "unknown top-level key"),
        ('plugins = ""\n', "top-level plugins"),
        ('plugins = ["sample"]\n', "plugin selectors"),
        ('plugins = ["sample@"]\n', "plugin selectors"),
        ('plugins = ["@debug"]\n', "plugin selectors"),
        ('plugins = ["sample@debug@extra"]\n', "plugin selectors"),
        ('plugins = [" sample@debug"]\n', "plugin selectors"),
        ('plugins = ["sample@debug "]\n', "plugin selectors"),
        ('plugins = []\nmarketplaces = ""\n', "marketplaces array"),
        (
            'plugins = []\n[[marketplaces]]\nname = ""\nsource = "owner/repo"\n',
            "marketplace requires",
        ),
        (
            'plugins = []\n[[marketplaces]]\nname = "debug"\nsource = ""\n',
            "marketplace requires",
        ),
        (
            'plugins = []\n[[marketplaces]]\nname = "debug"\nsource = "owner/repo"\nsparse = ""\n',
            "sparse",
        ),
        (
            'plugins = []\n[[marketplaces]]\nname = "debug"\nsource = "owner/repo"\nsparse = [""]\n',
            "sparse",
        ),
        (
            'plugins = []\n[[marketplaces]]\nname = "debug"\nsource = "owner/repo"\nsparce = [".agents/plugins"]\n',
            "unknown marketplace key",
        ),
    ],
)
def test_plan_to_reports_invalid_plugins_manifest_values(
    fake_home, tmp_path, manifest, detail
):
    app = _codex_app()
    target = tmp_path / "sync"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("config\n")
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("config\n")
    (stored / "plugins.toml").write_text(manifest)

    plan = app.plan_to(target)

    restore = [c for c in plan.changes if c.label == "plugins restore"][0]
    assert restore.kind == "unknown"
    assert "invalid plugins.toml" in restore.details
    assert detail in restore.details


@pytest.mark.parametrize(
    "list_command, warning",
    [
        (["codex", "plugin", "marketplace", "list", "--json"], "marketplace list"),
        (["codex", "plugin", "list", "--json"], "plugin list"),
    ],
)
def test_sync_to_warns_on_unexpected_codex_cli_json_shape(
    fake_home, tmp_path, monkeypatch, list_command, warning
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == list_command:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text(
        'plugins = ["sample@debug"]\n\n'
        "[[marketplaces]]\n"
        'name = "debug"\n'
        'source = "owner/repo"\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert list_command in calls
    assert any(warning in item and "unexpected JSON" in item for item in app.warnings)


@pytest.mark.parametrize(
    "list_command, stdout, warning",
    [
        (
            ["codex", "plugin", "marketplace", "list", "--json"],
            '{"marketplaces": "debug"}\n',
            "marketplace list",
        ),
        (
            ["codex", "plugin", "marketplace", "list", "--json"],
            '{"marketplaces": ["debug"]}\n',
            "marketplace list",
        ),
        (
            ["codex", "plugin", "list", "--json"],
            '{"installed": "sample@debug"}\n',
            "plugin list",
        ),
        (
            ["codex", "plugin", "list", "--json"],
            '{"installed": ["sample@debug"]}\n',
            "plugin list",
        ),
    ],
)
def test_sync_to_warns_on_malformed_codex_cli_json_fields(
    fake_home, tmp_path, monkeypatch, list_command, stdout, warning
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == list_command:
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd == ["codex", "plugin", "marketplace", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"marketplaces": [{"name": "debug", "root": "/tmp/debug"}]}\n',
                stderr="",
            )
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"installed": []}\n', stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text(
        'plugins = ["sample@debug"]\n\n'
        "[[marketplaces]]\n"
        'name = "debug"\n'
        'source = "owner/repo"\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert list_command in calls
    assert any(warning in item and "unexpected JSON" in item for item in app.warnings)


@pytest.mark.parametrize(
    "stdout",
    [
        '{"installed": [{"pluginId": 123}]}\n',
        '{"installed": [{"name": "sample", "marketplaceName": 123}]}\n',
        '{"installed": [{"name": "sample"}]}\n',
    ],
)
def test_sync_to_skips_plugin_install_on_malformed_installed_entries(
    fake_home, tmp_path, monkeypatch, stdout
):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if cmd == ["codex", "plugin", "list", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text("OLD\n")
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text("NEW\n")
    (stored / "plugins.toml").write_text('plugins = ["sample@debug"]\n')
    backup = tmp_path / "backup"
    backup.mkdir()
    app = _codex_app()

    app.sync_to(target, backup)

    assert ["codex", "plugin", "list", "--json"] in calls
    assert ["codex", "plugin", "add", "sample@debug", "--json"] not in calls
    assert any(
        "plugin list" in item and "unexpected JSON" in item for item in app.warnings
    )
    assert any("plugin install skipped" in warning for warning in app.warnings)


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
        "plugins.toml",
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
    assert removals["plugins.toml"].dest == stored / "plugins.toml"
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
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

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
