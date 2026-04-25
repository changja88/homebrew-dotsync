import pytest
from dotsync.apps.base import App, AppStatus, diff_files


def test_app_is_abstract():
    with pytest.raises(TypeError):
        App()


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
