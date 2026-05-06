from dotsync.welcome import format_welcome


def test_format_welcome_includes_ascii_logo_block_chars():
    out = format_welcome("0.1.0")
    assert "█" in out  # logo uses block drawing chars


def test_format_welcome_includes_version():
    out = format_welcome("9.9.9")
    assert "9.9.9" in out


def test_format_welcome_marks_init_as_starting_point():
    """Welcome should signal that `init` is where the user starts.

    We changed away from a hard 'required' wording. The key signal is now:
    init is listed first AND has a 'start here' hint.
    """
    out = format_welcome("0.1.0")
    assert "Quickstart" in out
    init_pos = out.find("dotsync init")
    from_pos = out.find("dotsync from")
    to_pos = out.find("dotsync to")
    assert 0 <= init_pos < from_pos < to_pos
    assert "start here" in out.lower()


def test_format_welcome_lists_basic_commands():
    out = format_welcome("0.1.0")
    assert "dotsync init" in out
    assert "from --all" in out
    assert "to --all" in out


def test_format_welcome_no_color_strips_ansi(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = format_welcome("0.1.0")
    assert "\033[" not in out


def test_format_welcome_uses_default_version_when_omitted():
    from dotsync import __version__
    out = format_welcome()
    assert __version__ in out
