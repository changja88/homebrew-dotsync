"""Tests for the single-choice (radio) picker used to switch Claude accounts.

The pure `SingleChoiceState` carries all logic; `pick_one` is TTY glue that
returns None in non-TTY environments (pytest) so an account is NEVER switched
unattended.
"""
import io

from dotsync import ui_picker


def test_render_one_writes_to_given_stream(capsys):
    """The radio picker must be able to draw to a caller-chosen stream (stderr),
    so `account pick` can put UI on the tty while stdout carries only the name."""
    s = ui_picker.SingleChoiceState(["work", "personal"], current="work")
    buf = io.StringIO()
    ui_picker._render_one(s, "Pick account", None, first=True, out=buf)
    rendered = buf.getvalue()
    assert "Pick account" in rendered
    assert "work" in rendered and "personal" in rendered
    assert capsys.readouterr().out == ""  # nothing leaked to stdout


def test_pick_one_non_tty_returns_none_with_stream():
    # pytest streams aren't TTYs → radio picker returns None (no selection made)
    assert ui_picker.pick_one(["a", "b"], stream=io.StringIO()) is None


def test_initial_cursor_on_current():
    s = ui_picker.SingleChoiceState(["work", "personal", "test"], current="personal")
    assert s.cursor == 1


def test_initial_cursor_defaults_to_zero_when_current_absent():
    s = ui_picker.SingleChoiceState(["work", "personal"], current=None)
    assert s.cursor == 0


def test_arrows_move_and_wrap():
    s = ui_picker.SingleChoiceState(["a", "b", "c"], current="a")
    s.handle("up")
    assert s.cursor == 2  # wrapped
    s.handle("down")
    assert s.cursor == 0


def test_space_is_noop_for_radio():
    s = ui_picker.SingleChoiceState(["a", "b"], current="a")
    s.handle("space")
    assert s.cursor == 0 and not s.done and not s.cancelled


def test_enter_selects_cursor_item():
    s = ui_picker.SingleChoiceState(["a", "b", "c"], current="a")
    s.handle("down")
    s.handle("enter")
    assert s.done is True
    assert s.result == "b"


def test_cancel_yields_none():
    s = ui_picker.SingleChoiceState(["a", "b"], current="a")
    s.handle("cancel")
    assert s.cancelled is True
    assert s.result is None


def test_pick_one_returns_none_without_tty():
    # pytest captures stdio -> not a TTY -> never auto-selects.
    assert ui_picker.pick_one(["a", "b"], current="a") is None


def test_pick_one_empty_returns_none():
    assert ui_picker.pick_one([], current=None) is None
