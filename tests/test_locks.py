"""Tool locks shared by the terminal and the dotsync app."""

import fcntl

import pytest

from dotsync import locks


def _hold(path, mode):
    handle = open(path, "a")
    fcntl.flock(handle, mode | fcntl.LOCK_NB)
    return handle


def test_exclusive_blocks_shared(fake_home):
    with locks.tool_lock("codex", shared=False):
        with pytest.raises(locks.LockBusy):
            with locks.tool_lock("codex", shared=True, wait=0.2):
                pass


def test_shared_allows_shared(fake_home):
    with locks.tool_lock("codex", shared=True):
        with locks.tool_lock("codex", shared=True, wait=0.2):
            pass


def test_shared_blocks_exclusive(fake_home):
    with locks.tool_lock("codex", shared=True):
        with pytest.raises(locks.LockBusy):
            with locks.tool_lock("codex", shared=False, wait=0.2):
                pass


def test_busy_after_wait(fake_home):
    locks.lock_dir().mkdir(parents=True, exist_ok=True)
    holder = _hold(locks.lock_dir() / "codex.lock", fcntl.LOCK_EX)
    try:
        with pytest.raises(locks.LockBusy) as err:
            with locks.tool_lock("codex", shared=True, wait=0.2):
                pass
        assert err.value.code == "busy"
        assert err.value.name == "codex"
    finally:
        holder.close()


def test_lock_released_after_block(fake_home):
    with locks.tool_lock("claude-sessions", shared=False):
        pass
    with locks.tool_lock("claude-sessions", shared=False, wait=0.1):
        pass


def test_lock_dir_created_private(fake_home):
    with locks.tool_lock("codex", shared=True):
        pass
    d = fake_home / "Library" / "Application Support" / "dotsync" / "locks"
    assert d.is_dir()
    assert (d.stat().st_mode & 0o777) == 0o700
