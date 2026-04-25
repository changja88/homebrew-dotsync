import io

import pytest

from dotsync.ui_picker import (
    PickerState,
    _fallback_per_app,
    _interactive_supported,
    _read_key,
    _render,
    pick_apps,
)


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


def test_render_first_pass_shows_title_and_all_items(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    state = PickerState(ITEMS, preselected={"claude", "zsh"})
    _render(state, "Pick apps to track", first=True)
    out = capsys.readouterr().out
    assert "Pick apps to track" in out
    assert "claude" in out and "ghostty" in out
    assert "bettertouchtool" in out and "zsh" in out
    # Hint row is printed
    assert "space toggle" in out
    assert "enter submit" in out


def test_render_marks_selected_items(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    state = PickerState(ITEMS, preselected={"claude", "zsh"})
    _render(state, "Pick apps", first=True)
    lines = capsys.readouterr().out.splitlines()
    claude_line = next(l for l in lines if "claude" in l)
    ghostty_line = next(l for l in lines if "ghostty" in l)
    assert "[x]" in claude_line
    assert "[ ]" in ghostty_line


def test_render_marks_cursor_position(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    state = PickerState(ITEMS, preselected=set())
    state.handle("down")    # cursor on ghostty
    _render(state, "Pick apps", first=True)
    lines = capsys.readouterr().out.splitlines()
    ghostty_line = next(l for l in lines if "ghostty" in l)
    claude_line = next(l for l in lines if "claude" in l)
    # ▸ marker appears on the cursor row, not on others
    assert "▸" in ghostty_line
    assert "▸" not in claude_line


def test_render_redraw_emits_cursor_up_and_clear(capsys, monkeypatch):
    """Subsequent renders must move the cursor up and clear so the picker
    stays in place rather than scrolling."""
    monkeypatch.setenv("NO_COLOR", "1")
    state = PickerState(ITEMS, preselected=set())
    _render(state, "Pick apps", first=True)
    capsys.readouterr()  # discard first
    _render(state, "Pick apps", first=False)
    out = capsys.readouterr().out
    # Cursor up by (items + 2) and clear-to-end-of-screen
    assert f"\x1b[{len(ITEMS) + 2}A" in out
    assert "\x1b[J" in out


def test_interactive_supported_false_when_stdin_not_tty(monkeypatch):
    class FakeStream:
        def isatty(self):
            return False
    monkeypatch.setattr("dotsync.ui_picker.sys.stdin", FakeStream())
    monkeypatch.setattr("dotsync.ui_picker.sys.stdout", FakeStream())
    assert _interactive_supported() is False


def test_interactive_supported_true_when_both_are_ttys(monkeypatch):
    class FakeStream:
        def isatty(self):
            return True
    monkeypatch.setattr("dotsync.ui_picker.sys.stdin", FakeStream())
    monkeypatch.setattr("dotsync.ui_picker.sys.stdout", FakeStream())
    assert _interactive_supported() is True


def test_fallback_default_yes_for_preselected(monkeypatch):
    """Bare Enter on a preselected app keeps it tracked."""
    answers = iter(["", "", "", ""])  # accept defaults for all 4
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = _fallback_per_app(ITEMS, preselected={"claude", "zsh"})
    assert result == ["claude", "zsh"]


def test_fallback_default_no_for_unpreselected(monkeypatch):
    """Bare Enter on an unselected app leaves it untracked."""
    answers = iter(["", "", "", ""])  # accept defaults for all 4
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = _fallback_per_app(ITEMS, preselected=set())
    assert result == []


def test_fallback_explicit_y_overrides_default(monkeypatch):
    answers = iter(["y", "n", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = _fallback_per_app(ITEMS, preselected=set())
    assert result == ["claude", "bettertouchtool"]


def test_fallback_explicit_n_overrides_preselected(monkeypatch):
    answers = iter(["n", "", "", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = _fallback_per_app(ITEMS, preselected={"claude", "zsh"})
    assert result == []  # claude said n, ghostty/btt not preselected, zsh said n


def test_pick_apps_uses_fallback_when_not_tty(monkeypatch):
    """Under pytest, isatty() is False → fallback path runs."""
    answers = iter(["", "n", "", "n"])  # claude=keep, ghostty=skip, btt=keep, zsh=skip
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = pick_apps(
        ["claude", "ghostty", "bettertouchtool", "zsh"],
        preselected={"claude", "bettertouchtool", "zsh"},
    )
    assert result == ["claude", "bettertouchtool"]


def test_pick_apps_fallback_returns_empty_list_when_all_skipped(monkeypatch):
    """All-no answers return [] (untrack everything), not None."""
    answers = iter(["n", "n", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = pick_apps(
        ["claude", "ghostty", "bettertouchtool", "zsh"],
        preselected={"claude"},
    )
    assert result == []


def test_pick_apps_tty_path_drives_state_to_completion(monkeypatch):
    """Smoke-test the TTY branch: with _interactive_supported faked True,
    a scripted key sequence produces the expected result. termios setup
    and rendering are stubbed out — we only exercise the loop."""
    monkeypatch.setattr("dotsync.ui_picker._interactive_supported", lambda: True)
    monkeypatch.setattr("dotsync.ui_picker._enter_raw_mode", lambda: object())
    monkeypatch.setattr("dotsync.ui_picker._restore_terminal", lambda token: None)
    monkeypatch.setattr("dotsync.ui_picker._render", lambda *a, **kw: None)

    keys = iter(["down", "space", "enter"])
    monkeypatch.setattr("dotsync.ui_picker._read_key", lambda: next(keys))

    result = pick_apps(
        ["claude", "ghostty", "bettertouchtool", "zsh"],
        preselected=set(),
    )
    assert result == ["ghostty"]


def test_pick_apps_cancel_returns_none(monkeypatch):
    monkeypatch.setattr("dotsync.ui_picker._interactive_supported", lambda: True)
    monkeypatch.setattr("dotsync.ui_picker._enter_raw_mode", lambda: object())
    monkeypatch.setattr("dotsync.ui_picker._restore_terminal", lambda token: None)
    monkeypatch.setattr("dotsync.ui_picker._render", lambda *a, **kw: None)
    monkeypatch.setattr("dotsync.ui_picker._read_key", lambda: "cancel")

    result = pick_apps(["claude", "ghostty"], preselected=set())
    assert result is None


def test_pick_apps_keyboard_interrupt_returns_none_and_restores(monkeypatch):
    """Ctrl+C during the loop must always restore terminal state and
    return None — never leave the user with a dead terminal."""
    monkeypatch.setattr("dotsync.ui_picker._interactive_supported", lambda: True)

    sentinel = object()
    restored = []
    monkeypatch.setattr("dotsync.ui_picker._enter_raw_mode", lambda: sentinel)
    monkeypatch.setattr(
        "dotsync.ui_picker._restore_terminal",
        lambda token: restored.append(token),
    )
    monkeypatch.setattr("dotsync.ui_picker._render", lambda *a, **kw: None)

    def boom():
        raise KeyboardInterrupt
    monkeypatch.setattr("dotsync.ui_picker._read_key", boom)

    result = pick_apps(["claude", "ghostty"], preselected=set())
    assert result is None
    assert restored == [sentinel]   # terminal was restored exactly once
