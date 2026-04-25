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
