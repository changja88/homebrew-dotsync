import os

import pytest

from dotsync.cli import main
from dotsync.sessions import roots, safety

VALID = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"


@pytest.mark.parametrize(
    "bad", ["", ".", "..", "../x", VALID.upper(), VALID + "x", None, 123]
)
def test_validate_id_rejects(bad, monkeypatch):
    with pytest.raises(safety.InvalidSessionId) as caught:
        safety.validate_id(bad)
    assert caught.value.code == "invalid_session_id"

    def no_fs(*args, **kwargs):
        raise AssertionError("filesystem touched")

    monkeypatch.setattr(os, "stat", no_fs)
    with pytest.raises(safety.InvalidSessionId):
        safety.validate_id(bad)


def test_validate_id_accepts_a_valid_id():
    assert safety.is_valid_id(VALID)
    assert safety.validate_id(VALID) == VALID


def test_child_joins_a_name_under_the_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert safety.child(root, "x") == root / "x"


def test_child_refuses_symlinked_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    root = tmp_path / "root"
    root.symlink_to(real)
    with pytest.raises(safety.UnsafePath):
        safety.child(root, "x")


@pytest.mark.parametrize("name", ["a/b", "", ".", ".."])
def test_child_refuses_bad_names(tmp_path, name):
    with pytest.raises(safety.UnsafePath):
        safety.child(tmp_path, name)


def test_remove_unlinks_a_symlink_not_its_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert safety.remove(link) is None

    assert not link.is_symlink()
    assert (target / "keep.txt").exists()


def test_remove_deletes_a_file_and_a_directory(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "g").write_text("y")

    assert safety.remove(f) is None
    assert safety.remove(d) is None
    assert not f.exists() and not d.exists()


def test_remove_missing_is_fine(tmp_path):
    assert safety.remove(tmp_path / "nope") is None


def test_remove_reports_errors(tmp_path):
    parent = tmp_path / "parent"
    victim = parent / "victim"
    victim.mkdir(parents=True)
    (victim / "f").write_text("x")
    parent.chmod(0o500)
    try:
        message = safety.remove(victim)
    finally:
        parent.chmod(0o700)
    assert message is not None
    assert str(victim) in message
    assert "\n" not in message


def test_roots_follow_home_and_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    assert roots.claude_home() == home / ".claude"
    assert roots.codex_home() == home / ".codex"
    assert roots.log_dir() == home / "Library" / "Logs" / "dotsync"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ignored"))
    assert roots.codex_home() == tmp_path / "cx"
    assert roots.claude_home() == home / ".claude"


def test_claude_temp_root_is_uid_scoped_and_off_with_tmpdir(monkeypatch):
    monkeypatch.undo()  # the autouse stub replaced claude_temp_root
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    assert roots.claude_temp_root().as_posix() == f"/private/tmp/claude-{os.getuid()}"
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", "/somewhere")
    assert roots.claude_temp_root() is None


def test_sessions_needs_a_subcommand():
    with pytest.raises(SystemExit) as caught:
        main(["sessions"])
    assert caught.value.code == 2
