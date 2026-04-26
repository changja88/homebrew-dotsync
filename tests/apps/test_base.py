import pytest
from dotsync.apps.base import App, AppStatus, diff_files


def test_app_base_has_no_tracked_files_and_raises_not_implemented(tmp_path):
    """App() can be instantiated (no abstract methods), but calling sync_from
    or sync_to on a bare App with no tracked_files raises NotImplementedError."""
    app = App()
    with pytest.raises(NotImplementedError):
        app.sync_from(tmp_path)


def test_concrete_subclass_works(tmp_path):
    class FakeApp(App):
        name = "fake"
        description = "fake app"

        def sync_from(self, target_dir):
            (target_dir / self.name).mkdir(parents=True, exist_ok=True)
            (target_dir / self.name / "f.txt").write_text("hi")

        def sync_to(self, target_dir, backup_dir):
            pass

    app = FakeApp()
    app.sync_from(tmp_path)
    assert (tmp_path / "fake" / "f.txt").read_text() == "hi"


def test_status_default_is_unknown(tmp_path):
    class MinimalApp(App):
        name = "minimal"
        description = ""

        def sync_from(self, target_dir): pass
        def sync_to(self, target_dir, backup_dir): pass

    s = MinimalApp().status(tmp_path)
    assert s.state == "unknown"


def test_appstatus_states():
    assert AppStatus(state="clean").state == "clean"
    assert AppStatus(state="dirty", details="x").details == "x"


def test_diff_files_clean_when_all_match(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("X")
    b = tmp_path / "b.txt"; b.write_text("X")
    s = diff_files([(a, b)])
    assert s.state == "clean"


def test_diff_files_dirty_when_content_differs(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("OLD")
    b = tmp_path / "b.txt"; b.write_text("NEW")
    s = diff_files([(a, b)])
    assert s.state == "dirty"
    assert "a.txt" in s.details


def test_diff_files_missing_when_either_side_absent(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("X")
    b = tmp_path / "missing.txt"   # not created
    s = diff_files([(a, b)])
    assert s.state == "missing"


def test_diff_files_reports_local_newer_when_local_mtime_greater(tmp_path):
    """When dirty and local was modified after stored, direction = local-newer."""
    import os
    from dotsync.apps.base import diff_files
    local = tmp_path / "a"
    stored = tmp_path / "b"
    stored.write_text("OLD")
    local.write_text("NEW")
    # force ordering: stored older than local
    os.utime(stored, (1000, 1000))
    os.utime(local, (2000, 2000))
    result = diff_files([(local, stored)])
    assert result.state == "dirty"
    assert result.direction == "local-newer"


def test_diff_files_reports_folder_newer_when_stored_mtime_greater(tmp_path):
    import os
    from dotsync.apps.base import diff_files
    local = tmp_path / "a"
    stored = tmp_path / "b"
    local.write_text("OLD")
    stored.write_text("NEW")
    os.utime(local, (1000, 1000))
    os.utime(stored, (2000, 2000))
    result = diff_files([(local, stored)])
    assert result.state == "dirty"
    assert result.direction == "folder-newer"


def test_diff_files_clean_has_empty_direction(tmp_path):
    from dotsync.apps.base import diff_files
    local = tmp_path / "a"
    stored = tmp_path / "b"
    local.write_text("SAME")
    stored.write_text("SAME")
    result = diff_files([(local, stored)])
    assert result.state == "clean"
    assert result.direction == ""


def test_finish_ok_emits_done_line(capsys, monkeypatch):
    """`App._finish_ok()` is the canonical 'this app is done' marker —
    a green ✓ line that closes a per-app section."""
    monkeypatch.setenv("NO_COLOR", "1")

    class FakeApp(App):
        name = "fake"
        description = ""
        def sync_from(self, target_dir): pass
        def sync_to(self, target_dir, backup_dir): pass

    FakeApp()._finish_ok()
    out = capsys.readouterr().out
    assert "✓" in out
    assert "done" in out


def test_finish_unchanged_emits_dim_line(capsys, monkeypatch):
    """`App._finish_unchanged()` is the canonical 'nothing to do here'
    marker for `dotsync to` when local and stored are byte-identical."""
    monkeypatch.setenv("NO_COLOR", "1")

    class FakeApp(App):
        name = "fake"
        description = ""
        def sync_from(self, target_dir): pass
        def sync_to(self, target_dir, backup_dir): pass

    FakeApp()._finish_unchanged()
    out = capsys.readouterr().out
    assert "unchanged" in out


def test_diff_files_reports_diverged_when_some_local_newer_some_stored_newer(tmp_path):
    """When the differs set contains both local-newer and folder-newer pairs,
    direction = diverged so the user knows neither side is fully ahead."""
    import os
    from dotsync.apps.base import diff_files
    a_local, a_stored = tmp_path / "a_local", tmp_path / "a_stored"
    b_local, b_stored = tmp_path / "b_local", tmp_path / "b_stored"
    a_local.write_text("Lnew"); a_stored.write_text("Lold")
    b_local.write_text("Bold"); b_stored.write_text("Bnew")
    os.utime(a_local, (2000, 2000)); os.utime(a_stored, (1000, 1000))   # local-newer
    os.utime(b_local, (1000, 1000)); os.utime(b_stored, (2000, 2000))   # folder-newer
    result = diff_files([(a_local, a_stored), (b_local, b_stored)])
    assert result.state == "dirty"
    assert result.direction == "diverged"


def test_app_from_config_default_returns_instance_with_no_args(tmp_path):
    """App.from_config(cfg) defaults to no-arg construction. Apps with config
    deps (BTT) override this classmethod."""
    from dotsync.apps.base import App
    from dotsync.config import Config

    class _Toy(App):
        name = "toy"
        def sync_from(self, target_dir): pass
        def sync_to(self, target_dir, backup_dir): pass

    cfg = Config(dir=tmp_path, apps=["toy"])
    instance = _Toy.from_config(cfg)
    assert isinstance(instance, _Toy)
    assert instance.name == "toy"


def test_app_tracked_files_default_returns_empty(tmp_path):
    from dotsync.apps.base import App

    class _Toy(App):
        name = "toy"
        def sync_from(self, target_dir): pass
        def sync_to(self, target_dir, backup_dir): pass

    assert _Toy().tracked_files(tmp_path) == []


def test_file_pair_is_a_dataclass_with_local_stored_label(tmp_path):
    from dotsync.apps.base import FilePair
    pair = FilePair(local=tmp_path / "a", stored=tmp_path / "b", label="x")
    assert pair.local == tmp_path / "a"
    assert pair.stored == tmp_path / "b"
    assert pair.label == "x"


def test_default_sync_from_copies_each_tracked_file(tmp_path):
    from dotsync.apps.base import App, FilePair
    home = tmp_path / "home"; home.mkdir()
    (home / "src.txt").write_text("ALPHA")

    class _Toy(App):
        name = "toy"
        def tracked_files(self, target_dir):
            return [FilePair(home / "src.txt", target_dir / "toy" / "dst.txt", "dst.txt")]

    target = tmp_path / "sync"; target.mkdir()
    _Toy().sync_from(target)
    assert (target / "toy" / "dst.txt").read_text() == "ALPHA"


def test_default_sync_from_raises_when_local_missing(tmp_path):
    from dotsync.apps.base import App, FilePair

    class _Toy(App):
        name = "toy"
        def tracked_files(self, target_dir):
            return [FilePair(tmp_path / "missing.txt", target_dir / "toy" / "dst.txt", "dst.txt")]

    target = tmp_path / "sync"; target.mkdir()
    with pytest.raises(FileNotFoundError, match="missing.txt"):
        _Toy().sync_from(target)


def test_default_sync_to_backs_up_then_copies_stored_over_local(tmp_path):
    from dotsync.apps.base import App, FilePair
    home = tmp_path / "home"; home.mkdir()
    (home / "live.txt").write_text("OLD")
    target = tmp_path / "sync"
    (target / "toy").mkdir(parents=True)
    (target / "toy" / "live.txt").write_text("NEW")
    backup = tmp_path / "bk"; backup.mkdir()

    class _Toy(App):
        name = "toy"
        def tracked_files(self, target_dir):
            return [FilePair(home / "live.txt", target_dir / "toy" / "live.txt", "live.txt")]

    _Toy().sync_to(target, backup)
    assert (home / "live.txt").read_text() == "NEW"
    assert (backup / "toy" / "live.txt").read_text() == "OLD"


def test_default_sync_to_raises_when_stored_missing(tmp_path):
    from dotsync.apps.base import App, FilePair
    home = tmp_path / "home"; home.mkdir()
    target = tmp_path / "sync"
    (target / "toy").mkdir(parents=True)
    backup = tmp_path / "bk"; backup.mkdir()

    class _Toy(App):
        name = "toy"
        def tracked_files(self, target_dir):
            return [FilePair(home / "x.txt", target_dir / "toy" / "x.txt", "x.txt")]

    with pytest.raises(FileNotFoundError, match="x.txt"):
        _Toy().sync_to(target, backup)


def test_default_status_uses_diff_files_over_tracked_pairs(tmp_path):
    from dotsync.apps.base import App, FilePair
    home = tmp_path / "home"; home.mkdir()
    (home / "a.txt").write_text("X")
    target = tmp_path / "sync"
    (target / "toy").mkdir(parents=True)
    (target / "toy" / "a.txt").write_text("X")

    class _Toy(App):
        name = "toy"
        def tracked_files(self, target_dir):
            return [FilePair(home / "a.txt", target_dir / "toy" / "a.txt", "a.txt")]

    s = _Toy().status(target)
    assert s.state == "clean"
