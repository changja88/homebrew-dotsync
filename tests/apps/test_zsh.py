import pytest
from dotsync.apps.zsh import ZshApp


def test_sync_from_copies_zshrc_to_target(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("export FOO=1\n")
    target = tmp_path / "configs"
    target.mkdir()

    ZshApp().sync_from(target)

    assert (target / "zsh" / ".zshrc").read_text() == "export FOO=1\n"


def test_sync_from_missing_zshrc_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with pytest.raises(FileNotFoundError, match=".zshrc"):
        ZshApp().sync_from(target)


def test_sync_to_backs_up_then_overwrites(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("OLD\n")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("NEW\n")

    ZshApp().sync_to(target)

    assert (fake_home / ".zshrc").read_text() == "NEW\n"


def test_sync_to_missing_target_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    with pytest.raises(FileNotFoundError, match="zsh/.zshrc"):
        ZshApp().sync_to(target)


def test_status_clean_when_files_match(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("X")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("X")
    assert ZshApp().status(target).state == "clean"


def test_status_dirty_when_content_differs(fake_home, tmp_path):
    (fake_home / ".zshrc").write_text("OLD")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("NEW")
    assert ZshApp().status(target).state == "dirty"


def test_status_missing_when_either_absent(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    assert ZshApp().status(target).state == "missing"


# ----- the marked DOTSYNC_DIR lines belong to this Mac --------------------


def _block(path):
    from dotsync.shellrc import MARKER, export_line

    return f"{MARKER}\n{export_line(path)}\n"


def _setup(fake_home, tmp_path, local, stored):
    (fake_home / ".zshrc").write_text(local)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text(stored)
    return target


def test_status_clean_when_only_dotsync_dir_differs(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="alias ll='ls -la'\n\n" + _block("/Users/me/B"),
        stored="alias ll='ls -la'\n\n" + _block("/Users/me/A"),
    )
    assert ZshApp().status(target).state == "clean"


def test_status_still_dirty_when_other_lines_differ(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="alias ll='ls -la'\n" + _block("/B"),
        stored="alias ll='ls -lah'\n" + _block("/A"),
    )
    assert ZshApp().status(target).state == "dirty"


def test_pull_keeps_local_dotsync_dir_lines(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="export OLD=1\n\n" + _block("/Users/me/B") + "# local tail\n",
        stored="export NEW=1\n\n" + _block("/Users/me/A") + "# stored tail\n",
    )

    ZshApp().sync_to(target)

    # This Mac's block goes at the end, where `dotsync init` puts it.
    assert (fake_home / ".zshrc").read_text() == (
        "export NEW=1\n# stored tail\n\n" + _block("/Users/me/B")
    )


_IF = "if [ -f ~/.fzf.zsh ]; then\n  source ~/.fzf.zsh\nfi\n"


def test_pull_keeps_block_out_of_an_if_when_folder_copy_is_longer(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="alias a=1\n" + _IF + "\n" + _block("/Users/me/B"),
        stored="alias a=1\nalias b=2\nalias c=3\n" + _IF + "\n" + _block("/Users/me/A"),
    )

    ZshApp().sync_to(target)

    assert (fake_home / ".zshrc").read_text() == (
        "alias a=1\nalias b=2\nalias c=3\n" + _IF + "\n" + _block("/Users/me/B")
    )


def test_pull_keeps_block_out_of_an_if_when_folder_copy_is_shorter(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="alias a=1\nalias b=2\nalias c=3\n\n" + _block("/Users/me/B") + _IF,
        stored="alias a=1\n" + _IF,
    )

    ZshApp().sync_to(target)

    assert (fake_home / ".zshrc").read_text() == "alias a=1\n" + _IF + "\n" + _block("/Users/me/B")


def test_pull_preview_leaves_out_the_block_lines(fake_home, tmp_path):
    from dotsync.cli_json import change_diff_text

    target = _setup(fake_home, tmp_path, local="X\n\n" + _block("/B"), stored="Y\n\n" + _block("/A"))

    change = ZshApp().plan_to(target).changes[0]

    assert change.details == "+1 −1"
    diff = change_diff_text(change)
    assert "-X" in diff and "+Y" in diff
    assert "-export DOTSYNC_DIR" not in diff and "+export DOTSYNC_DIR" not in diff


def test_pull_drops_stored_block_when_local_has_none(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="export OLD=1\n",
        stored="export NEW=1\n\n" + _block("/Users/me/A"),
    )

    ZshApp().sync_to(target)

    assert (fake_home / ".zshrc").read_text() == "export NEW=1\n"


def test_plan_to_unchanged_when_only_block_differs(fake_home, tmp_path):
    target = _setup(
        fake_home, tmp_path,
        local="X\n" + _block("/B"),
        stored="X\n" + _block("/A"),
    )
    assert ZshApp().plan_to(target).changes[0].kind == "unchanged"
    assert ZshApp().plan_from(target).changes[0].kind == "unchanged"


def test_plan_to_update_when_other_lines_differ(fake_home, tmp_path):
    target = _setup(fake_home, tmp_path, local="X\n" + _block("/B"), stored="Y\n" + _block("/A"))
    assert ZshApp().plan_to(target).changes[0].kind == "update"


def test_pull_refuses_symlinked_zshrc(fake_home, tmp_path):
    real = tmp_path / "real-zshrc"
    real.write_text("X\n")
    (fake_home / ".zshrc").symlink_to(real)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Y\n")

    with pytest.raises(RuntimeError, match="symlink"):
        ZshApp().sync_to(target)
    assert real.read_text() == "X\n"
