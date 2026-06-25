from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from dotsync.apps.base import AppStatus
from dotsync.apps.bettertouchtool import BetterTouchToolApp


def _osascript_done(*args, **kwargs):
    class R:
        returncode = 0
        stdout = "done"
        stderr = ""

    cmd = args[0] if args else kwargs.get("args")
    for token in cmd:
        if "outputPath" in token:
            import re

            m = re.search(r'outputPath "([^"]+)"', token)
            if m:
                Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                Path(m.group(1)).write_text("<bttpreset/>")
    return R()


def _osascript_done_no_export(*args, **kwargs):
    class R:
        returncode = 0
        stdout = "done"
        stderr = ""

    return R()


def test_sync_from_invokes_osascript_export(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done
    ) as run:
        BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)
    assert run.called
    assert (target / "bettertouchtool" / "presets" / "Master_bt.bttpreset").exists()


def test_sync_from_uses_custom_preset_name(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done
    ):
        BetterTouchToolApp(presets=["MyPreset"]).sync_from(target)
    assert (target / "bettertouchtool" / "presets" / "MyPreset.bttpreset").exists()


def test_sync_from_exports_every_preset(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done
    ):
        BetterTouchToolApp(presets=["Master_bt", "Mini_bt"]).sync_from(target)
    presets_dir = target / "bettertouchtool" / "presets"
    assert (presets_dir / "Master_bt.bttpreset").exists()
    assert (presets_dir / "Mini_bt.bttpreset").exists()


def test_sync_to_imports_every_preset(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    (presets_dir / "Mini_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done
    ) as run:
        BetterTouchToolApp(presets=["Master_bt", "Mini_bt"]).sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert any("import_preset" in c and "Master_bt" in c for c in cmds)
    assert any("import_preset" in c and "Mini_bt" in c for c in cmds)


def test_status_dirty_when_one_of_many_presets_differs(tmp_path):
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    (presets / "Master_bt.bttpreset").write_text("<bttpreset>SAME</bttpreset>")
    (presets / "Mini_bt.bttpreset").write_text("<bttpreset>STORED</bttpreset>")

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        joined = " ".join(cmd)
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    if "Master_bt" in joined:
                        Path(m.group(1)).write_text("<bttpreset>SAME</bttpreset>")
                    else:
                        Path(m.group(1)).write_text("<bttpreset>LIVE</bttpreset>")
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt", "Mini_bt"]).status(target)
    assert result.state == "dirty"
    assert "Mini_bt" in result.details


def test_status_missing_when_one_of_many_presets_missing(tmp_path):
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    (presets / "Master_bt.bttpreset").write_text("<bttpreset/>")
    result = BetterTouchToolApp(presets=["Master_bt", "Mini_bt"]).status(target)
    assert result.state == "missing"
    assert "Mini_bt" in result.details


def test_sync_from_waits_for_async_export(tmp_path, monkeypatch):
    """BTT's export_preset returns 'done' before the file is on disk. sync_from
    must poll for the file to appear instead of failing immediately."""
    target = tmp_path / "configs"
    target.mkdir()
    expected = target / "bettertouchtool" / "presets" / "Master_bt.bttpreset"

    def osascript_async(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        return R()

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            expected.write_text("<bttpreset/>")

    monkeypatch.setattr("dotsync.apps.bettertouchtool.subprocess.run", osascript_async)
    monkeypatch.setattr("dotsync.apps.bettertouchtool.time.sleep", fake_sleep)

    BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)
    assert expected.exists()
    assert len(sleeps) >= 1


def test_sync_from_raises_when_export_never_appears(tmp_path, monkeypatch):
    target = tmp_path / "configs"
    target.mkdir()

    def osascript_async(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        return R()

    monkeypatch.setattr("dotsync.apps.bettertouchtool.subprocess.run", osascript_async)
    monkeypatch.setattr("dotsync.apps.bettertouchtool.time.sleep", lambda _s: None)
    monkeypatch.setattr("dotsync.apps.bettertouchtool._EXPORT_WAIT_TIMEOUT", 0.05)

    with pytest.raises(RuntimeError, match="not created"):
        BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)


def test_sync_from_failure_raises(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()

    class Fail:
        returncode = 1
        stdout = ""
        stderr = "BTT not running"

    with patch("dotsync.apps.bettertouchtool.subprocess.run", return_value=Fail()):
        with pytest.raises(RuntimeError, match="osascript"):
            BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)


def test_plan_to_reports_missing_preset(tmp_path):
    app = BetterTouchToolApp(presets=["Missing"])

    plan = app.plan_to(tmp_path)

    assert plan.changes[0].kind == "missing-source"
    assert plan.changes[0].label == "presets/Missing.bttpreset"


def _make_btt_subprocess(stored_text_per_preset: dict[str, str]):
    """Mock subprocess.run so each `export_preset "<name>" outputPath "..."`
    call writes the configured text for that preset name. Used to drive
    BTT's status() (and plan_from / plan_to via status()) deterministically."""

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        joined = " ".join(cmd)
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    out = Path(m.group(1))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    for preset, text in stored_text_per_preset.items():
                        if preset in joined:
                            out.write_text(text)
                            break
        return R()

    return fake_run


def test_plan_to_reports_unchanged_when_live_matches_stored(tmp_path):
    """After `dotsync backup`, every preset's live BTT state matches the stored
    bytes (modulo BTTPresetUUID, which status() normalizes). plan_to MUST
    surface that as 'unchanged' — otherwise `dotsync apply` immediately after
    `dotsync backup` falsely shows every BTT preset needing an update.
    Regression: plan_to used to skip the live-vs-stored comparison entirely
    and always returned 'update' whenever the stored file existed."""
    target = tmp_path / "configs"
    stored = target / "bettertouchtool" / "presets" / "Master_bt.bttpreset"
    stored.parent.mkdir(parents=True)
    stored.write_text("<bttpreset/>")

    fake_run = _make_btt_subprocess({"Master_bt": "<bttpreset/>"})
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        plan = BetterTouchToolApp(presets=["Master_bt"]).plan_to(target)

    assert plan.changes[0].kind == "unchanged"
    assert plan.has_changes is False


def test_plan_to_reports_update_when_live_differs_from_stored(tmp_path):
    target = tmp_path / "configs"
    stored = target / "bettertouchtool" / "presets" / "Master_bt.bttpreset"
    stored.parent.mkdir(parents=True)
    stored.write_text("<bttpreset>STORED</bttpreset>")

    fake_run = _make_btt_subprocess({"Master_bt": "<bttpreset>LIVE</bttpreset>"})
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        plan = BetterTouchToolApp(presets=["Master_bt"]).plan_to(target)

    assert plan.changes[0].kind == "update"
    assert plan.has_changes is True


def test_plan_to_reports_unknown_when_btt_not_running(tmp_path, monkeypatch):
    """Mirror plan_from: if BTT isn't running we can't export live state,
    so we can't know whether the import would change anything. Surface
    'unknown' instead of confidently claiming 'update'."""
    target = tmp_path / "configs"
    stored = target / "bettertouchtool" / "presets" / "Master_bt.bttpreset"
    stored.parent.mkdir(parents=True)
    stored.write_text("<bttpreset/>")

    app = BetterTouchToolApp(presets=["Master_bt"])
    monkeypatch.setattr(
        app, "status", lambda target_dir: AppStatus("unknown", "BTT not running")
    )

    plan = app.plan_to(target)

    assert plan.changes[0].kind == "unknown"
    assert "BTT not running" in plan.changes[0].details


def test_plan_from_reports_unknown_when_btt_status_unknown(tmp_path, monkeypatch):
    app = BetterTouchToolApp(presets=["Master_bt"])

    monkeypatch.setattr(
        app, "status", lambda target_dir: AppStatus("unknown", "BTT not running")
    )

    plan = app.plan_from(tmp_path)

    assert plan.changes[0].kind == "unknown"
    assert "BTT not running" in plan.changes[0].details


def test_sync_to_imports_preset(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run",
        side_effect=_osascript_done_no_export,
    ) as run:
        BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)

    calls = [c.args[0] for c in run.call_args_list]
    assert any("import_preset" in " ".join(c) for c in calls)


def test_sync_to_launches_btt_and_retries_missing_value(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / "backup"
    backup.mkdir()
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = "missing value" if len(calls) == 1 else "done"
            stderr = ""

        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)

    assert any(c[:3] == ["open", "-gja", "BetterTouchTool"] for c in calls)
    assert sum("import_preset" in " ".join(c) for c in calls) == 1


def test_sync_to_missing_preset_raises(tmp_path):
    target = tmp_path / "configs"
    (target / "bettertouchtool" / "presets").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="bttpreset"):
        BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)


def test_sync_from_refuses_symlink_stored_app_root(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "bettertouchtool").symlink_to(outside, target_is_directory=True)
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done
    ):
        with pytest.raises(RuntimeError, match="symlink"):
            BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)

    assert not (outside / "presets" / "Master_bt.bttpreset").exists()


def test_sync_to_refuses_symlink_stored_preset(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    outside = tmp_path / "outside.bttpreset"
    outside.write_text("<secret/>")
    (presets_dir / "Master_bt.bttpreset").symlink_to(outside)
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch(
        "dotsync.apps.bettertouchtool.subprocess.run",
        side_effect=_osascript_done_no_export,
    ):
        with pytest.raises(RuntimeError, match="symlink"):
            BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)


def test_sync_from_escapes_quote_in_export_path(tmp_path, monkeypatch):
    target = tmp_path / 'configs "quoted"'
    target.mkdir()
    scripts = []

    def fake_run(cmd, capture_output, text):
        scripts.append(cmd[2])

        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        return R()

    def fake_wait(self, path, timeout=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<bttpreset/>")
        return True

    monkeypatch.setattr("dotsync.apps.bettertouchtool.subprocess.run", fake_run)
    monkeypatch.setattr(BetterTouchToolApp, "_wait_for_export", fake_wait)

    BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)

    assert '\\"quoted\\"' in scripts[0]
    assert 'configs "quoted"' not in scripts[0]


def test_sync_to_escapes_quote_in_backup_path(tmp_path, monkeypatch):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / 'backup "quoted"'
    backup.mkdir()
    scripts = []

    def fake_run(cmd, capture_output, text):
        scripts.append(cmd[2])

        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        return R()

    def fake_wait(self, path, timeout=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<bttpreset/>")
        return True

    monkeypatch.setattr("dotsync.apps.bettertouchtool.subprocess.run", fake_run)
    monkeypatch.setattr(BetterTouchToolApp, "_wait_for_export", fake_wait)

    BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)

    assert '\\"quoted\\"' in scripts[0]
    assert 'backup "quoted"' not in scripts[0]


def test_is_present_locally_true_when_btt_app_exists(monkeypatch, tmp_path):
    fake_apps = tmp_path / "Applications"
    (fake_apps / "BetterTouchTool.app").mkdir(parents=True)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH",
        fake_apps / "BetterTouchTool.app",
    )
    assert BetterTouchToolApp.is_present_locally() is True


def test_is_present_locally_false_when_btt_app_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH",
        tmp_path / "nope" / "BetterTouchTool.app",
    )
    assert BetterTouchToolApp.is_present_locally() is False


def test_status_clean_when_export_matches_stored(tmp_path):
    """If the live BTT export matches the stored .bttpreset byte-for-byte,
    status() returns clean."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    (presets / "Master_bt.bttpreset").write_text("<bttpreset>SAME</bttpreset>")

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text("<bttpreset>SAME</bttpreset>")
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_clean_when_only_btt_preset_uuid_differs(tmp_path):
    """BTT regenerates BTTPresetUUID on every export_preset call, even when
    the preset content is otherwise identical. status() must normalize that
    line away or every backup→apply roundtrip falsely shows dirty."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = (
        "{\n"
        '  "BTTPresetVersion" : "4.0",\n'
        '  "BTTPresetUUID" : "AAAAAAAA-1111-2222-3333-444444444444",\n'
        '  "BTTPresetName" : "Master_bt"\n'
        "}\n"
    )
    live_text = (
        "{\n"
        '  "BTTPresetVersion" : "4.0",\n'
        '  "BTTPresetUUID" : "BBBBBBBB-9999-8888-7777-666666666666",\n'
        '  "BTTPresetName" : "Master_bt"\n'
        "}\n"
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_dirty_when_real_content_differs_despite_uuid_normalization(tmp_path):
    """Sanity check: normalizing the UUID line must NOT mask real content
    changes elsewhere in the file."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = (
        "{\n"
        '  "BTTPresetUUID" : "AAAAAAAA-1111-2222-3333-444444444444",\n'
        '  "BTTPresetName" : "Master_bt",\n'
        '  "trigger" : "OLD"\n'
        "}\n"
    )
    live_text = (
        "{\n"
        '  "BTTPresetUUID" : "BBBBBBBB-9999-8888-7777-666666666666",\n'
        '  "BTTPresetName" : "Master_bt",\n'
        '  "trigger" : "NEW"\n'
        "}\n"
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "dirty"


def test_status_clean_when_only_btt_last_updated_at_differs(tmp_path):
    """BTT may rewrite trigger metadata timestamps during app updates or
    database migrations. Those timestamps should not make unchanged shortcuts
    look dirty."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = (
        "{\n"
        '  "BTTPresetName" : "Master_bt",\n'
        '  "BTTTriggers" : [\n'
        "    {\n"
        '      "BTTLastUpdatedAt" : 1779981980.37502,\n'
        '      "BTTUUID" : "7771B270-FB80-4B09-A1A0-76E79E2EFB6E",\n'
        '      "BTTLayoutIndependentChar" : "HOME",\n'
        '      "BTTShortcutKeyCode" : 115\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    live_text = (
        "{\n"
        '  "BTTPresetName" : "Master_bt",\n'
        '  "BTTTriggers" : [\n'
        "    {\n"
        '      "BTTLastUpdatedAt" : 1780807565.605134,\n'
        '      "BTTUUID" : "7771B270-FB80-4B09-A1A0-76E79E2EFB6E",\n'
        '      "BTTLayoutIndependentChar" : "HOME",\n'
        '      "BTTShortcutKeyCode" : 115\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_clean_when_only_btt_last_used_metadata_differs(tmp_path):
    """BTT records shortcut usage metadata for triggers. That runtime history
    should not make unchanged shortcuts look dirty."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = (
        "{\n"
        '  "BTTPresetName" : "Master_bt",\n'
        '  "BTTTriggers" : [\n'
        "    {\n"
        '      "BTTLastUsed" : 1781942138.774268,\n'
        '      "BTTLastUsedAt" : 1781942138.774268,\n'
        '      "BTTUUID" : "7771B270-FB80-4B09-A1A0-76E79E2EFB6E",\n'
        '      "BTTLayoutIndependentChar" : "HOME",\n'
        '      "BTTShortcutKeyCode" : 115\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    live_text = (
        "{\n"
        '  "BTTPresetName" : "Master_bt",\n'
        '  "BTTTriggers" : [\n'
        "    {\n"
        '      "BTTLastUsed" : 1782025426.051253,\n'
        '      "BTTLastUsedAt" : 1782025426.051253,\n'
        '      "BTTUUID" : "7771B270-FB80-4B09-A1A0-76E79E2EFB6E",\n'
        '      "BTTLayoutIndependentChar" : "HOME",\n'
        '      "BTTShortcutKeyCode" : 115\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_clean_when_only_btt_runtime_stats_differ(tmp_path):
    """BTT updates app runtime counters/version markers in the exported
    general settings. Those do not change preset behavior."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = json.dumps(
        {
            "BTTPresetName": "Master_bt",
            "BTTGeneralSettings": {
                "BTTDidRegisterForUpdateStats": "6.521",
                "BTTNumberOfStarts": 5731,
                "BTTDefaultTBIconHeight": 22,
            },
        }
    )
    live_text = json.dumps(
        {
            "BTTPresetName": "Master_bt",
            "BTTGeneralSettings": {
                "BTTDidRegisterForUpdateStats": "6.591",
                "BTTNumberOfStarts": 5734,
                "BTTDefaultTBIconHeight": 22,
            },
        }
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_clean_when_only_container_order_differs(tmp_path):
    """BTT's export_preset emits the per-app containers in BTTPresetContent
    in no guaranteed order — the same preset can export [Global, Finder] one
    day and [Finder, Global] the next. A positional comparison flags that as
    dirty even though nothing changed."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    finder = {"BTTAppBundleIdentifier": "com.apple.finder", "BTTTriggers": []}
    global_ = {
        "BTTAppBundleIdentifier": "BT.G",
        "BTTTriggers": [{"BTTUUID": "U1", "BTTOrder": 0, "BTTShortcutKeyCode": 115}],
    }
    stored_text = json.dumps(
        {"BTTPresetName": "Master_bt", "BTTPresetContent": [finder, global_]}
    )
    live_text = json.dumps(
        {"BTTPresetName": "Master_bt", "BTTPresetContent": [global_, finder]}
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_clean_when_only_trigger_order_differs(tmp_path):
    """Trigger order inside a container's BTTTriggers array is just as
    volatile across exports as the container order. The user-visible ordering
    lives in each trigger's BTTOrder field, so array position carries no
    information and must not affect the dirty check."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    t1 = {"BTTUUID": "U1", "BTTOrder": 0, "BTTShortcutKeyCode": 115}
    t2 = {"BTTUUID": "U2", "BTTOrder": 1, "BTTShortcutKeyCode": 116}
    stored_text = json.dumps(
        {
            "BTTPresetContent": [
                {"BTTAppBundleIdentifier": "BT.G", "BTTTriggers": [t1, t2]}
            ],
        }
    )
    live_text = json.dumps(
        {
            "BTTPresetContent": [
                {"BTTAppBundleIdentifier": "BT.G", "BTTTriggers": [t2, t1]}
            ],
        }
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_clean_when_only_trigger_position_metadata_differs(tmp_path):
    """BTT can rewrite trigger/action order numbers and entity UUIDs without
    changing the shortcut behavior. A shortcut/action that still exists in the
    same app scope should compare clean."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_trigger = {
        "BTTUUID": "OLD-TRIGGER-ID",
        "BTTOrder": 0,
        "BTTTriggerType": 0,
        "BTTShortcutKeyCode": 115,
        "BTTShortcutModifierKeys": 8388608,
        "BTTAdditionalActions": [
            {
                "BTTUUID": "OLD-ACTION-ID",
                "BTTOrder": 1,
                "BTTPredefinedActionType": 264,
                "BTTShortcutToSend": "56,55,42",
            }
        ],
    }
    live_trigger = {
        "BTTUUID": "NEW-TRIGGER-ID",
        "BTTOrder": 12,
        "BTTTriggerType": 0,
        "BTTShortcutKeyCode": 115,
        "BTTShortcutModifierKeys": 8388608,
        "BTTAdditionalActions": [
            {
                "BTTUUID": "NEW-ACTION-ID",
                "BTTOrder": 1,
                "BTTPredefinedActionType": 264,
                "BTTShortcutToSend": "56,55,42",
            }
        ],
    }
    stored_text = json.dumps(
        {
            "BTTPresetContent": [
                {
                    "BTTAppBundleIdentifier": "com.google.android.studio",
                    "BTTTriggers": [stored_trigger],
                }
            ],
        }
    )
    live_text = json.dumps(
        {
            "BTTPresetContent": [
                {
                    "BTTAppBundleIdentifier": "com.google.android.studio",
                    "BTTTriggers": [live_trigger],
                }
            ],
        }
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "clean"


def test_status_dirty_when_trigger_content_differs_regardless_of_order(tmp_path):
    """Sanity check: order-insensitive comparison must NOT mask a real
    content change — same triggers reordered, but one key remapped."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    t1 = {"BTTUUID": "U1", "BTTOrder": 0, "BTTShortcutKeyCode": 115}
    t2 = {"BTTUUID": "U2", "BTTOrder": 1, "BTTShortcutKeyCode": 116}
    t2_changed = {"BTTUUID": "U2", "BTTOrder": 1, "BTTShortcutKeyCode": 999}
    stored_text = json.dumps(
        {
            "BTTPresetContent": [
                {"BTTAppBundleIdentifier": "BT.G", "BTTTriggers": [t1, t2]}
            ],
        }
    )
    live_text = json.dumps(
        {
            "BTTPresetContent": [
                {"BTTAppBundleIdentifier": "BT.G", "BTTTriggers": [t2_changed, t1]}
            ],
        }
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "dirty"


def test_status_dirty_when_action_order_metadata_differs(tmp_path):
    """Unlike trigger position, action order can affect behavior for a
    multi-action shortcut, so BTTOrder inside actions remains significant."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = json.dumps(
        {
            "BTTPresetContent": [
                {
                    "BTTAppBundleIdentifier": "BT.G",
                    "BTTTriggers": [
                        {
                            "BTTTriggerType": 0,
                            "BTTShortcutKeyCode": 115,
                            "BTTAdditionalActions": [
                                {
                                    "BTTPredefinedActionType": 264,
                                    "BTTShortcutToSend": "1",
                                    "BTTOrder": 1,
                                },
                                {
                                    "BTTPredefinedActionType": 264,
                                    "BTTShortcutToSend": "2",
                                    "BTTOrder": 2,
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    live_text = json.dumps(
        {
            "BTTPresetContent": [
                {
                    "BTTAppBundleIdentifier": "BT.G",
                    "BTTTriggers": [
                        {
                            "BTTTriggerType": 0,
                            "BTTShortcutKeyCode": 115,
                            "BTTAdditionalActions": [
                                {
                                    "BTTPredefinedActionType": 264,
                                    "BTTShortcutToSend": "1",
                                    "BTTOrder": 2,
                                },
                                {
                                    "BTTPredefinedActionType": 264,
                                    "BTTShortcutToSend": "2",
                                    "BTTOrder": 1,
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "dirty"


def test_status_dirty_when_action_array_order_differs_without_order_metadata(tmp_path):
    """If BTT emits actions without explicit BTTOrder, the list position may be
    the only execution order signal. Do not sort action arrays away."""
    import json

    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    action_one = {"BTTPredefinedActionType": 264, "BTTShortcutToSend": "1"}
    action_two = {"BTTPredefinedActionType": 264, "BTTShortcutToSend": "2"}
    stored_text = json.dumps(
        {
            "BTTPresetContent": [
                {
                    "BTTAppBundleIdentifier": "BT.G",
                    "BTTTriggers": [
                        {
                            "BTTTriggerType": 0,
                            "BTTShortcutKeyCode": 115,
                            "BTTAdditionalActions": [action_one, action_two],
                        }
                    ],
                }
            ],
        }
    )
    live_text = json.dumps(
        {
            "BTTPresetContent": [
                {
                    "BTTAppBundleIdentifier": "BT.G",
                    "BTTTriggers": [
                        {
                            "BTTTriggerType": 0,
                            "BTTShortcutKeyCode": 115,
                            "BTTAdditionalActions": [action_two, action_one],
                        }
                    ],
                }
            ],
        }
    )
    (presets / "Master_bt.bttpreset").write_text(stored_text)

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text(live_text)
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "dirty"


def test_status_dirty_when_export_differs(tmp_path):
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    (presets / "Master_bt.bttpreset").write_text("<bttpreset>STORED</bttpreset>")

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""

        cmd = args[0]
        for token in cmd:
            if "outputPath" in token:
                import re

                m = re.search(r'outputPath "([^"]+)"', token)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text("<bttpreset>LIVE</bttpreset>")
        return R()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=fake_run):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "dirty"


def test_status_missing_when_stored_absent(tmp_path):
    target = tmp_path / "configs"
    (target / "bettertouchtool" / "presets").mkdir(parents=True)
    # no .bttpreset stored
    result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "missing"


def test_status_unknown_when_btt_not_running(tmp_path):
    """osascript failure must not crash status — return unknown with a hint."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    (presets / "Master_bt.bttpreset").write_text("X")

    class Fail:
        returncode = 1
        stdout = ""
        stderr = "BTT not running"

    with patch("dotsync.apps.bettertouchtool.subprocess.run", return_value=Fail()):
        result = BetterTouchToolApp(presets=["Master_bt"]).status(target)
    assert result.state == "unknown"
    assert "running" in result.details.lower() or "btt" in result.details.lower()

def _make_btt_db(path: Path, preset_names: list[str]) -> None:
    """Create a minimal BTT-shaped SQLite DB with the given preset names
    in the ZNAME3 column of the ZBTTBASEENTITY table. Mirrors the schema
    fields our discover query relies on; ignores everything else."""
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME VARCHAR)")
    cur.execute(
        "CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER, Z_ENT INTEGER, ZNAME3 VARCHAR)"
    )
    cur.execute(
        "INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)", (12, "Preset")
    )
    cur.execute(
        "INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)", (8, "Gesture")
    )
    for i, name in enumerate(preset_names, start=1):
        cur.execute(
            "INSERT INTO ZBTTBASEENTITY (Z_PK, Z_ENT, ZNAME3) VALUES (?, ?, ?)",
            (i, 12, name),
        )
    # noise: a non-Preset row should not be picked up
    cur.execute(
        "INSERT INTO ZBTTBASEENTITY (Z_PK, Z_ENT, ZNAME3) VALUES (?, ?, ?)",
        (999, 8, "not_a_preset"),
    )
    conn.commit()
    conn.close()


def test_discover_preset_names_returns_sorted_list(tmp_path, monkeypatch):
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()
    db = btt_dir / "btt_data_store.version_6_306_build_2026032508"
    _make_btt_db(db, ["Work", "Master_bt", "Travel"])
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)

    names = BetterTouchToolApp.discover_preset_names()
    assert names == ["Master_bt", "Travel", "Work"]


def test_discover_preset_names_picks_most_recent_db(tmp_path, monkeypatch):
    """User has stale DB files from prior BTT versions; we use the latest."""
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()
    old_db = btt_dir / "btt_data_store.version_6_011_build_2026010801"
    new_db = btt_dir / "btt_data_store.version_6_306_build_2026032508"
    _make_btt_db(old_db, ["OldPreset"])
    _make_btt_db(new_db, ["CurrentPreset"])
    # Force the old DB to have an older mtime
    import os

    os.utime(old_db, (1_700_000_000, 1_700_000_000))
    os.utime(new_db, (1_800_000_000, 1_800_000_000))
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)

    assert BetterTouchToolApp.discover_preset_names() == ["CurrentPreset"]


def test_discover_preset_names_ignores_wal_and_shm_siblings(tmp_path, monkeypatch):
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()
    db = btt_dir / "btt_data_store.version_6_306_build_2026032508"
    _make_btt_db(db, ["MyPreset"])
    # SQLite write-ahead log siblings — must not be picked as the DB
    (btt_dir / "btt_data_store.version_6_306_build_2026032508-shm").write_bytes(
        b"\x00" * 32
    )
    (btt_dir / "btt_data_store.version_6_306_build_2026032508-wal").write_bytes(
        b"\x00" * 32
    )

    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)
    assert BetterTouchToolApp.discover_preset_names() == ["MyPreset"]


def test_discover_preset_names_returns_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", tmp_path / "does_not_exist")
    assert BetterTouchToolApp.discover_preset_names() == []


def test_discover_preset_names_returns_empty_when_no_db_files(tmp_path, monkeypatch):
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()  # exists but empty
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)
    assert BetterTouchToolApp.discover_preset_names() == []


def test_discover_preset_names_returns_empty_on_corrupt_db(tmp_path, monkeypatch):
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()
    bad = btt_dir / "btt_data_store.version_6_306_build_2026032508"
    bad.write_bytes(b"this is not a sqlite database")
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)
    assert BetterTouchToolApp.discover_preset_names() == []


def test_discover_preset_names_returns_empty_on_unexpected_schema(
    tmp_path, monkeypatch
):
    """If the schema lacks Z_PRIMARYKEY or ZBTTBASEENTITY, we don't crash."""
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()
    db = btt_dir / "btt_data_store.version_6_306_build_2026032508"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE Foo (bar TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)
    assert BetterTouchToolApp.discover_preset_names() == []


def test_discover_preset_names_filters_null_and_empty(tmp_path, monkeypatch):
    """ZNAME3 values that are NULL or empty string are not real preset names."""
    btt_dir = tmp_path / "btt"
    btt_dir.mkdir()
    db = btt_dir / "btt_data_store.version_6_306_build_2026032508"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME VARCHAR)")
    cur.execute(
        "CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER, Z_ENT INTEGER, ZNAME3 VARCHAR)"
    )
    cur.execute(
        "INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)", (12, "Preset")
    )
    cur.execute("INSERT INTO ZBTTBASEENTITY VALUES (1, 12, NULL)")
    cur.execute("INSERT INTO ZBTTBASEENTITY VALUES (2, 12, '')")
    cur.execute("INSERT INTO ZBTTBASEENTITY VALUES (3, 12, 'Real')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(BetterTouchToolApp, "DATA_DIR", btt_dir)
    assert BetterTouchToolApp.discover_preset_names() == ["Real"]


def test_btt_from_config_reads_presets(tmp_path):
    from dotsync.apps.bettertouchtool import BetterTouchToolApp
    from dotsync.config import Config

    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        bettertouchtool_presets=["Alpha", "Beta"],
    )
    app = BetterTouchToolApp.from_config(cfg)
    assert app.presets == ["Alpha", "Beta"]


def test_btt_from_config_falls_back_to_default_when_unset(tmp_path):
    from dotsync.apps.bettertouchtool import BetterTouchToolApp
    from dotsync.config import Config, DEFAULT_BTT_PRESETS

    cfg = Config(dir=tmp_path, apps=[])  # bettertouchtool_presets defaults
    app = BetterTouchToolApp.from_config(cfg)
    assert app.presets == list(DEFAULT_BTT_PRESETS)


def test_btt_from_config_reads_app_options(tmp_path):
    """BTT prefers app_options['bettertouchtool']['presets'] when present."""
    from dotsync.apps.bettertouchtool import BetterTouchToolApp
    from dotsync.config import Config

    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["FromOptions1", "FromOptions2"]}},
    )
    app = BetterTouchToolApp.from_config(cfg)
    assert app.presets == ["FromOptions1", "FromOptions2"]


@pytest.mark.parametrize(
    "preset",
    ["", "../Escape", "Folder/Name", "Bad\\Name", 'Bad"Name', "Line\nBreak"],
)
def test_btt_rejects_unsafe_preset_names(preset):
    with pytest.raises(ValueError, match="preset"):
        BetterTouchToolApp(presets=[preset])


def test_btt_from_config_rejects_unsafe_preset_names(tmp_path):
    from dotsync.config import Config

    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ['Bad"Name']}},
    )

    with pytest.raises(ValueError, match="preset"):
        BetterTouchToolApp.from_config(cfg)


def test_btt_from_config_falls_back_to_legacy_field_when_app_options_empty(tmp_path):
    """Existing dotsync.toml with bettertouchtool_presets only (no [options.bettertouchtool])
    must keep working without manual migration."""
    from dotsync.apps.bettertouchtool import BetterTouchToolApp
    from dotsync.config import Config

    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        bettertouchtool_presets=["Legacy"],  # no app_options
    )
    app = BetterTouchToolApp.from_config(cfg)
    assert app.presets == ["Legacy"]


def test_btt_extra_init_args_registers_presets_flag():
    import argparse
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    parser = argparse.ArgumentParser()
    BetterTouchToolApp.extra_init_args(parser)
    args = parser.parse_args(["--btt-presets", "Foo,Bar"])
    assert args.btt_presets == "Foo,Bar"


def test_btt_picker_annotation_when_detected(monkeypatch):
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    monkeypatch.setattr(
        BetterTouchToolApp,
        "discover_preset_names",
        classmethod(lambda cls: ["A", "B", "C"]),
    )
    assert BetterTouchToolApp.picker_annotation(detected=True) == "3 presets"


def test_btt_picker_annotation_one_preset_singular(monkeypatch):
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    monkeypatch.setattr(
        BetterTouchToolApp, "discover_preset_names", classmethod(lambda cls: ["Only"])
    )
    assert BetterTouchToolApp.picker_annotation(detected=True) == "1 preset"


def test_btt_picker_annotation_none_when_not_detected():
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    assert BetterTouchToolApp.picker_annotation(detected=False) is None


def test_btt_resolve_options_explicit_flag(tmp_path, monkeypatch):
    import argparse
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    args = argparse.Namespace(btt_presets="X,Y", yes=False)
    opts = BetterTouchToolApp.resolve_options(
        args,
        prev_apps=[],
        new_apps=["bettertouchtool"],
        interactive=False,
    )
    assert opts == {"presets": ["X", "Y"]}


def test_btt_resolve_options_returns_none_when_btt_not_in_new_apps():
    import argparse
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    args = argparse.Namespace(btt_presets=None, yes=False)
    opts = BetterTouchToolApp.resolve_options(
        args,
        prev_apps=[],
        new_apps=["zsh"],
        interactive=False,
    )
    assert opts is None  # don't touch app_options if BTT isn't tracked
