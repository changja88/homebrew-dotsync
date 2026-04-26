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
        BetterTouchToolApp(presets=["Master_bt"]).sync_from(target)
    assert run.called
    assert (target / "bettertouchtool" / "presets" / "Master_bt.bttpreset").exists()


def test_sync_from_uses_custom_preset_name(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done):
        BetterTouchToolApp(presets=["MyPreset"]).sync_from(target)
    assert (target / "bettertouchtool" / "presets" / "MyPreset.bttpreset").exists()


def test_sync_from_exports_every_preset(tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done):
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

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done) as run:
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


def test_sync_to_imports_preset(tmp_path):
    target = tmp_path / "configs"
    presets_dir = target / "bettertouchtool" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "Master_bt.bttpreset").write_text("<bttpreset/>")
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.bettertouchtool.subprocess.run", side_effect=_osascript_done_no_export) as run:
        BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)

    calls = [c.args[0] for c in run.call_args_list]
    assert any("import_preset" in " ".join(c) for c in calls)


def test_sync_to_missing_preset_raises(tmp_path):
    target = tmp_path / "configs"
    (target / "bettertouchtool" / "presets").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="bttpreset"):
        BetterTouchToolApp(presets=["Master_bt"]).sync_to(target, backup)


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
    line away or every from→to roundtrip falsely shows dirty."""
    target = tmp_path / "configs"
    presets = target / "bettertouchtool" / "presets"
    presets.mkdir(parents=True)
    stored_text = (
        '{\n'
        '  "BTTPresetVersion" : "4.0",\n'
        '  "BTTPresetUUID" : "AAAAAAAA-1111-2222-3333-444444444444",\n'
        '  "BTTPresetName" : "Master_bt"\n'
        '}\n'
    )
    live_text = (
        '{\n'
        '  "BTTPresetVersion" : "4.0",\n'
        '  "BTTPresetUUID" : "BBBBBBBB-9999-8888-7777-666666666666",\n'
        '  "BTTPresetName" : "Master_bt"\n'
        '}\n'
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
        '{\n'
        '  "BTTPresetUUID" : "AAAAAAAA-1111-2222-3333-444444444444",\n'
        '  "BTTPresetName" : "Master_bt",\n'
        '  "trigger" : "OLD"\n'
        '}\n'
    )
    live_text = (
        '{\n'
        '  "BTTPresetUUID" : "BBBBBBBB-9999-8888-7777-666666666666",\n'
        '  "BTTPresetName" : "Master_bt",\n'
        '  "trigger" : "NEW"\n'
        '}\n'
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


import sqlite3


def _make_btt_db(path: Path, preset_names: list[str]) -> None:
    """Create a minimal BTT-shaped SQLite DB with the given preset names
    in the ZNAME3 column of the ZBTTBASEENTITY table. Mirrors the schema
    fields our discover query relies on; ignores everything else."""
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME VARCHAR)")
    cur.execute("CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER, Z_ENT INTEGER, ZNAME3 VARCHAR)")
    cur.execute("INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)", (12, "Preset"))
    cur.execute("INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)", (8, "Gesture"))
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
    (btt_dir / "btt_data_store.version_6_306_build_2026032508-shm").write_bytes(b"\x00" * 32)
    (btt_dir / "btt_data_store.version_6_306_build_2026032508-wal").write_bytes(b"\x00" * 32)

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


def test_discover_preset_names_returns_empty_on_unexpected_schema(tmp_path, monkeypatch):
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
    cur.execute("CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER, Z_ENT INTEGER, ZNAME3 VARCHAR)")
    cur.execute("INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)", (12, "Preset"))
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
