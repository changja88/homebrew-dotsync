"""Unit tests for the shellrc module — detection + idempotent rc edits."""
from pathlib import Path

import pytest

from dotsync.shellrc import (
    MARKER,
    ShellRcResult,
    detect_rc_path,
    export_line,
    update_shell_rc,
)


# ---------------- detect_rc_path -----------------------------------------

def test_detect_rc_path_zsh(tmp_path):
    assert detect_rc_path(shell="/bin/zsh", home=tmp_path) == tmp_path / ".zshrc"


def test_detect_rc_path_zsh_homebrew_path(tmp_path):
    """Some users have non-standard zsh paths — only the basename should matter."""
    assert detect_rc_path(shell="/opt/homebrew/bin/zsh", home=tmp_path) == tmp_path / ".zshrc"


def test_detect_rc_path_bash_prefers_bash_profile_when_present(tmp_path):
    (tmp_path / ".bash_profile").write_text("# existing\n")
    assert detect_rc_path(shell="/bin/bash", home=tmp_path) == tmp_path / ".bash_profile"


def test_detect_rc_path_bash_falls_back_to_bashrc_when_only_bashrc_exists(tmp_path):
    (tmp_path / ".bashrc").write_text("# existing\n")
    assert detect_rc_path(shell="/bin/bash", home=tmp_path) == tmp_path / ".bashrc"


def test_detect_rc_path_bash_default_when_neither_exists(tmp_path):
    """macOS convention: login shells read ~/.bash_profile, so default to that."""
    assert detect_rc_path(shell="/bin/bash", home=tmp_path) == tmp_path / ".bash_profile"


def test_detect_rc_path_unsupported_shell_returns_none(tmp_path):
    assert detect_rc_path(shell="/usr/local/bin/fish", home=tmp_path) is None
    assert detect_rc_path(shell="/usr/bin/csh", home=tmp_path) is None
    assert detect_rc_path(shell="/usr/local/bin/nu", home=tmp_path) is None


def test_detect_rc_path_empty_shell_returns_none(tmp_path):
    assert detect_rc_path(shell="", home=tmp_path) is None


def test_detect_rc_path_falls_back_to_env_shell(tmp_path, monkeypatch):
    """shell=None means 'read $SHELL from env'."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert detect_rc_path(shell=None, home=tmp_path) == tmp_path / ".zshrc"

    monkeypatch.delenv("SHELL", raising=False)
    assert detect_rc_path(shell=None, home=tmp_path) is None


# ---------------- export_line --------------------------------------------

def test_export_line_quotes_path():
    line = export_line(Path("/Users/me/Desktop/dotsync_config"))
    assert line == 'export DOTSYNC_DIR="/Users/me/Desktop/dotsync_config"'


def test_export_line_handles_path_with_spaces():
    line = export_line(Path("/Users/me/My Drive/dotsync"))
    assert line == 'export DOTSYNC_DIR="/Users/me/My Drive/dotsync"'


# ---------------- update_shell_rc ----------------------------------------

def test_update_shell_rc_missing_file_does_not_create(tmp_path):
    rc = tmp_path / ".zshrc"
    result = update_shell_rc(rc, tmp_path / "sync")
    assert result.action == "rc_missing"
    assert result.rc_path == rc
    assert not rc.exists()


def test_update_shell_rc_empty_file_appends_marker_and_line(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("")
    result = update_shell_rc(rc, tmp_path / "sync")
    assert result.action == "added"
    text = rc.read_text()
    assert MARKER in text
    assert 'export DOTSYNC_DIR="' in text
    assert str(tmp_path / "sync") in text


def test_update_shell_rc_existing_content_appends_at_eof(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("alias ll='ls -la'\nexport FOO=bar\n")
    result = update_shell_rc(rc, tmp_path / "sync")
    assert result.action == "added"
    text = rc.read_text()
    # original lines preserved, in order
    lines = text.splitlines()
    assert lines[0] == "alias ll='ls -la'"
    assert lines[1] == "export FOO=bar"
    # marker and export are after
    assert MARKER in text
    idx_marker = lines.index(MARKER)
    assert lines[idx_marker + 1].startswith("export DOTSYNC_DIR=")


def test_update_shell_rc_idempotent_when_exact_line_present(tmp_path):
    rc = tmp_path / ".zshrc"
    sync = tmp_path / "sync"
    line = export_line(sync)
    rc.write_text(f"alias ll='ls -la'\n{line}\n")

    result = update_shell_rc(rc, sync)
    assert result.action == "already_set"
    # file untouched (exactly one occurrence, no marker added)
    text = rc.read_text()
    assert text.count(line) == 1
    assert MARKER not in text


def test_update_shell_rc_calling_twice_is_safe(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("# header\n")
    sync = tmp_path / "sync"

    r1 = update_shell_rc(rc, sync)
    r2 = update_shell_rc(rc, sync)
    assert r1.action == "added"
    assert r2.action == "already_set"
    text = rc.read_text()
    line = export_line(sync)
    assert text.count(line) == 1
    assert text.count(MARKER) == 1


def test_update_shell_rc_updates_when_path_changed(tmp_path):
    rc = tmp_path / ".zshrc"
    old_sync = tmp_path / "old"
    new_sync = tmp_path / "new"
    rc.write_text(f"alias ll='ls -la'\n{export_line(old_sync)}\n# tail\n")

    result = update_shell_rc(rc, new_sync)
    assert result.action == "updated"
    text = rc.read_text()
    new_line = export_line(new_sync)
    old_line = export_line(old_sync)
    assert new_line in text
    assert old_line not in text
    # surrounding lines preserved in order
    lines = text.splitlines()
    assert lines[0] == "alias ll='ls -la'"
    assert lines[-1] == "# tail" or "# tail" in lines


def test_update_shell_rc_preserves_trailing_newline(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("export FOO=bar\n")
    update_shell_rc(rc, tmp_path / "sync")
    assert rc.read_text().endswith("\n")


def test_update_shell_rc_returns_typed_result(tmp_path):
    """ShellRcResult is the contract — callers depend on .action / .rc_path / .line."""
    rc = tmp_path / ".zshrc"
    rc.write_text("")
    result = update_shell_rc(rc, tmp_path / "sync")
    assert isinstance(result, ShellRcResult)
    assert hasattr(result, "action")
    assert hasattr(result, "rc_path")
    assert hasattr(result, "line")
    assert result.line == export_line(tmp_path / "sync")
