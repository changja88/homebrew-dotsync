import io

import pytest

from local_dev.serena_mcp_management import ui
from local_dev.serena_mcp_management.ui import SelectOption, confirm, select_option


MEMORY_OPTIONS = (
    SelectOption("keep", "Run with existing memory"),
    SelectOption("delete", "Delete all Codex auto-memory and run"),
    SelectOption("cancel", "Cancel"),
)


def test_confirm_returns_true_for_yes_input():
    stream = io.StringIO()
    answers = iter(["y"])
    assert confirm("Run codex?", default=False,
                   stream=stream, input_fn=lambda: next(answers)) is True


def test_confirm_returns_false_for_no_input():
    stream = io.StringIO()
    answers = iter(["n"])
    assert confirm("Run codex?", default=True,
                   stream=stream, input_fn=lambda: next(answers)) is False


def test_confirm_returns_default_on_empty_input():
    stream = io.StringIO()
    assert confirm("Run codex?", default=True,
                   stream=stream, input_fn=lambda: "") is True
    assert confirm("Run codex?", default=False,
                   stream=stream, input_fn=lambda: "") is False


def test_confirm_falls_back_to_line_mode_when_input_fn_supplied():
    # Even with input_fn=None style callers, providing input_fn explicitly
    # must keep the simple line prompt and never try to grab stdin in raw
    # mode -- the test environment has no controlling terminal.
    stream = io.StringIO()
    confirm("Run?", default=True, stream=stream, input_fn=lambda: "y")
    assert "[Y/n]" in stream.getvalue()
    # No huh-style ▶ marker should appear when not in arrow-select mode.
    assert "▶" not in stream.getvalue()


def test_select_option_line_mode_accepts_number_and_defaults_to_first():
    assert select_option(
        "Memory for codex?", options=MEMORY_OPTIONS, input_fn=lambda: "2"
    ) == "delete"
    assert select_option(
        "Memory for codex?", options=MEMORY_OPTIONS, input_fn=lambda: ""
    ) == "keep"


def test_select_option_line_mode_lists_options_and_retries_invalid_input():
    stream = io.StringIO()
    answers = iter(["delete", "4", "3"])

    result = select_option(
        "Memory for codex?",
        options=MEMORY_OPTIONS,
        stream=stream,
        input_fn=lambda: next(answers),
    )

    assert result == "cancel"
    assert stream.getvalue().splitlines()[:4] == [
        f"  \x1b[{ui.PURPLE}m>\x1b[0m Memory for codex?",
        f"    \x1b[{ui.PURPLE}m1. Run with existing memory\x1b[0m",
        f"    \x1b[{ui.PURPLE}m2. Delete all Codex auto-memory and run\x1b[0m",
        f"    \x1b[{ui.PURPLE}m3. Cancel\x1b[0m",
    ]


def test_select_option_honors_nonzero_default_index():
    assert select_option(
        "Memory for codex?",
        options=MEMORY_OPTIONS,
        default_index=2,
        input_fn=lambda: "",
    ) == "cancel"


def test_select_option_validates_options_and_default_index():
    with pytest.raises(ValueError, match="options must not be empty"):
        select_option("Memory?", options=(), input_fn=lambda: "")
    with pytest.raises(ValueError, match="default_index out of range"):
        select_option(
            "Memory?", options=MEMORY_OPTIONS, default_index=3, input_fn=lambda: ""
        )


def test_arrow_prompt_ctrl_c_erases_block_and_restores_terminal(monkeypatch):
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    restored: list[tuple[object, ...]] = []

    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)

    def interrupt_read(fd, size):
        raise KeyboardInterrupt

    monkeypatch.setattr(ui.os, "read", interrupt_read)
    monkeypatch.setattr(
        ui.termios,
        "tcsetattr",
        lambda *args: restored.append(args),
    )

    with pytest.raises(KeyboardInterrupt):
        ui._read_yes_no_arrow(
            "Run codex?",
            default=True,
            stream=stream,
            fd=7,
        )

    assert stream.getvalue().endswith("\x1b[3A\x1b[J")
    assert restored == [(7, ui.termios.TCSADRAIN, old_attrs)]


def test_select_option_ctrl_c_erases_four_line_block(monkeypatch):
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    restored = []
    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(
        ui.os, "read", lambda fd, size: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    monkeypatch.setattr(
        ui.termios, "tcsetattr", lambda *args: restored.append(args)
    )

    with pytest.raises(KeyboardInterrupt):
        ui._read_select_arrow(
            "Memory for codex?", options=MEMORY_OPTIONS, cursor=0, stream=stream, fd=7
        )

    assert stream.getvalue().endswith("\x1b[4A\x1b[J")
    assert restored == [(7, ui.termios.TCSADRAIN, old_attrs)]


def test_select_option_raw_navigation_collapses_selected_value(monkeypatch):
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    restored = []
    reads = iter((b"\x1b", b"[B", b"\r"))
    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(ui.os, "read", lambda fd, size: next(reads))
    monkeypatch.setattr(
        ui.termios, "tcsetattr", lambda *args: restored.append(args)
    )

    selected = ui._read_select_arrow(
        "Memory for codex?",
        options=MEMORY_OPTIONS,
        cursor=0,
        stream=stream,
        fd=7,
    )

    output = stream.getvalue()
    assert selected == "delete"
    assert output.count("\x1b[4A\x1b[J") == 1
    assert "\x1b[3A\r\x1b[2K" in output
    assert "\x1b[2A\r\x1b[2K" in output
    assert output.endswith(
        f"  \x1b[{ui.PURPLE}m?\x1b[0m Memory for codex? "
        f"\x1b[{ui.PURPLE}mDelete all Codex auto-memory and run\x1b[0m\n"
    )
    assert restored == [(7, ui.termios.TCSADRAIN, old_attrs)]


@pytest.mark.parametrize(
    ("shortcut", "expected", "label"),
    [(b"y", True, "Yes"), (b"n", False, "No")],
)
def test_legacy_raw_yes_no_shortcuts_collapse_selection(
    monkeypatch,
    shortcut,
    expected,
    label,
):
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    restored = []
    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(ui.os, "read", lambda fd, size: shortcut)
    monkeypatch.setattr(
        ui.termios, "tcsetattr", lambda *args: restored.append(args)
    )

    selected = ui._read_yes_no_arrow(
        "Run codex?",
        default=not expected,
        stream=stream,
        fd=7,
    )

    output = stream.getvalue()
    assert selected is expected
    assert output.count("\x1b[3A\x1b[J") == 1
    assert output.endswith(
        f"  \x1b[{ui.PURPLE}m?\x1b[0m Run codex? "
        f"\x1b[{ui.PURPLE}m{label}\x1b[0m\n"
    )
    assert restored == [(7, ui.termios.TCSADRAIN, old_attrs)]
