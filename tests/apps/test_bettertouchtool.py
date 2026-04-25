from pathlib import Path
from unittest.mock import patch
import pytest
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
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done) as run:
        BetterTouchToolApp(preset="Master_bt").sync_from(target)
    assert run.called
    assert (target / "bettertouchtool" / "presets" / "Master_bt.bttpreset").exists()


def test_sync_from_uses_custom_preset_name(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done):
        BetterTouchToolApp(preset="MyPreset").sync_from(target)
    assert (target / "bettertouchtool" / "presets" / "MyPreset.bttpreset").exists()


def test_sync_from_failure_raises(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    class Fail:
        returncode = 1
        stdout = ""
        stderr = "BTT not running"
    with patch("dotsync.apps.bettertouchtool.subprocess.run", return_value=Fail()):
        with pytest.raises(RuntimeError, match="osascript"):
            BetterTouchToolApp(preset="Master_bt").sync_from(target)


def test_sync_to_imports_preset(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done_no_export) as run:
        BetterTouchToolApp(preset="Master_bt").sync_to(target, backup)

    calls = [c.args[0] for c in run.call_args_list]
    assert any("import_preset" in " ".join(c) for c in calls)


def test_sync_to_missing_preset_raises(tmp_path):
    target = tmp_path / "configs"
    (target / "bettertouchtool" / "presets").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="bttpreset"):
        BetterTouchToolApp(preset="Master_bt").sync_to(target, backup)


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
        result = BetterTouchToolApp(preset="Master_bt").status(target)
    assert result.state == "clean"


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
        result = BetterTouchToolApp(preset="Master_bt").status(target)
    assert result.state == "dirty"


def test_status_missing_when_stored_absent(tmp_path):
    target = tmp_path / "configs"
    (target / "bettertouchtool" / "presets").mkdir(parents=True)
    # no .bttpreset stored
    result = BetterTouchToolApp(preset="Master_bt").status(target)
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
        result = BetterTouchToolApp(preset="Master_bt").status(target)
    assert result.state == "unknown"
    assert "running" in result.details.lower() or "btt" in result.details.lower()
