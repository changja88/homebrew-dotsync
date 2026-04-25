from pathlib import Path
import time
from dotsync.backup import new_backup_session, rotate_backups


def test_new_backup_session_creates_unique_dir(tmp_path):
    s1 = new_backup_session(tmp_path)
    time.sleep(1.01)
    s2 = new_backup_session(tmp_path)
    assert s1.exists()
    assert s2.exists()
    assert s1 != s2
    assert s1.parent == tmp_path
    # timestamp format YYYYMMDD_HHMMSS
    assert len(s1.name) == 15
    assert s1.name[8] == "_"


def test_new_backup_session_creates_parent(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    s = new_backup_session(deep)
    assert s.parent == deep
    assert s.exists()


def test_rotate_keeps_n_newest(tmp_path):
    for name in ["20260101_000000", "20260102_000000", "20260103_000000",
                 "20260104_000000", "20260105_000000"]:
        (tmp_path / name).mkdir()
    rotate_backups(tmp_path, keep=3)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["20260103_000000", "20260104_000000", "20260105_000000"]


def test_rotate_zero_keep_keeps_all(tmp_path):
    for name in ["20260101_000000", "20260102_000000"]:
        (tmp_path / name).mkdir()
    rotate_backups(tmp_path, keep=0)
    assert len(list(tmp_path.iterdir())) == 2


def test_rotate_ignores_nonbackup_dirs(tmp_path):
    (tmp_path / "20260101_000000").mkdir()
    (tmp_path / "20260102_000000").mkdir()
    (tmp_path / "not-a-backup").mkdir()
    (tmp_path / "20260103_000000").mkdir()
    rotate_backups(tmp_path, keep=1)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert "not-a-backup" in remaining
    assert "20260103_000000" in remaining
    assert "20260101_000000" not in remaining
