import io

import pytest

from dotsync.ui_picker import PickerState, _read_key


ITEMS = ["claude", "ghostty", "bettertouchtool", "zsh"]


def test_state_starts_with_preselected():
    s = PickerState(ITEMS, preselected={"claude", "zsh"})
    assert s.selected == {"claude", "zsh"}
    assert s.cursor == 0
    assert not s.done
    assert not s.cancelled


def test_state_preselected_filters_unknown_items():
    """A preselected name not in items is silently ignored."""
    s = PickerState(ITEMS, preselected={"claude", "atom"})
    assert s.selected == {"claude"}


def test_state_down_moves_cursor():
    s = PickerState(ITEMS, preselected=set())
    s.handle("down")
    assert s.cursor == 1
    s.handle("down")
    assert s.cursor == 2


def test_state_down_wraps_at_end():
    s = PickerState(ITEMS, preselected=set())
    for _ in range(len(ITEMS)):
        s.handle("down")
    assert s.cursor == 0


def test_state_up_wraps_at_start():
    s = PickerState(ITEMS, preselected=set())
    s.handle("up")
    assert s.cursor == len(ITEMS) - 1


def test_state_space_toggles_on_then_off():
    s = PickerState(ITEMS, preselected=set())
    s.handle("space")
    assert "claude" in s.selected
    s.handle("space")
    assert "claude" not in s.selected


def test_state_space_toggles_only_at_cursor():
    s = PickerState(ITEMS, preselected=set())
    s.handle("down")  # cursor at ghostty
    s.handle("space")
    assert s.selected == {"ghostty"}


def test_state_enter_marks_done():
    s = PickerState(ITEMS, preselected={"claude"})
    s.handle("enter")
    assert s.done
    assert not s.cancelled


def test_state_cancel_marks_cancelled():
    s = PickerState(ITEMS, preselected={"claude"})
    s.handle("cancel")
    assert s.cancelled
    assert not s.done


def test_state_result_preserves_input_order():
    """Result is items in their input order, not selection order."""
    s = PickerState(ITEMS, preselected=set())
    s.handle("down")             # cursor at ghostty
    s.handle("down")             # cursor at btt
    s.handle("space")            # select btt first
    s.handle("up")               # back to ghostty
    s.handle("up")               # back to claude
    s.handle("space")            # then select claude
    s.handle("enter")
    assert s.result == ["claude", "bettertouchtool"]


def test_state_cancel_result_is_none():
    s = PickerState(ITEMS, preselected={"claude"})
    s.handle("cancel")
    assert s.result is None


def test_state_unknown_key_is_noop():
    s = PickerState(ITEMS, preselected=set())
    s.handle("xyzzy")
    assert s.cursor == 0
    assert s.selected == set()
    assert not s.done
    assert not s.cancelled


def _stdin_with(seq: str, monkeypatch):
    """Mock sys.stdin with the given byte sequence + select.select that
    always reports stdin readable (so escape-sequence peek doesn't block)."""
    fake = io.StringIO(seq)
    fake.fileno = lambda: 0  # any int; select isn't actually called on it
    monkeypatch.setattr("dotsync.ui_picker.sys.stdin", fake)
    monkeypatch.setattr(
        "dotsync.ui_picker.select.select",
        lambda r, w, x, t: (r, [], []),
    )


def test_read_key_space(monkeypatch):
    _stdin_with(" ", monkeypatch)
    assert _read_key() == "space"


def test_read_key_enter_lf(monkeypatch):
    _stdin_with("\n", monkeypatch)
    assert _read_key() == "enter"


def test_read_key_enter_cr(monkeypatch):
    _stdin_with("\r", monkeypatch)
    assert _read_key() == "enter"


def test_read_key_q_cancels(monkeypatch):
    _stdin_with("q", monkeypatch)
    assert _read_key() == "cancel"


def test_read_key_uppercase_q_cancels(monkeypatch):
    _stdin_with("Q", monkeypatch)
    assert _read_key() == "cancel"


def test_read_key_arrow_up(monkeypatch):
    _stdin_with("\x1b[A", monkeypatch)
    assert _read_key() == "up"


def test_read_key_arrow_down(monkeypatch):
    _stdin_with("\x1b[B", monkeypatch)
    assert _read_key() == "down"


def test_read_key_bare_escape_cancels(monkeypatch):
    """ESC alone (no `[A`/`[B` follow-up) is treated as cancel."""
    fake = io.StringIO("\x1b")
    fake.fileno = lambda: 0
    monkeypatch.setattr("dotsync.ui_picker.sys.stdin", fake)
    # select reports nothing readable → no follow-up byte
    monkeypatch.setattr(
        "dotsync.ui_picker.select.select",
        lambda r, w, x, t: ([], [], []),
    )
    assert _read_key() == "cancel"


def test_read_key_ctrl_c_raises_keyboardinterrupt(monkeypatch):
    _stdin_with("\x03", monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _read_key()


def test_read_key_unknown_byte_returns_none(monkeypatch):
    _stdin_with("z", monkeypatch)
    assert _read_key() is None
