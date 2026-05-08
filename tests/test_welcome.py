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


def test_format_welcome_includes_sparkle_frame():
    """Logo should be wrapped with sparkle decorations on top and bottom."""
    out = format_welcome("0.1.0")
    assert "❖" in out
    assert "✷" in out
    assert "⋆" in out


def test_format_welcome_tagline_has_decoration():
    """Tagline should be flanked with ∿∿∿ decorations."""
    out = format_welcome("0.1.0")
    assert "∿∿∿" in out
    # Decoration appears on both sides of the tagline
    assert out.count("∿∿∿") >= 2


def test_format_welcome_uses_pink_to_purple_gradient_when_color_enabled(monkeypatch):
    """Logo gradient should move from pink at the top to purple at the bottom."""
    monkeypatch.setattr("dotsync.ui._color_enabled", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = format_welcome("0.1.0")
    # Top of gradient (pink-300) and bottom (purple-700) both appear.
    assert "\033[38;2;249;168;212m" in out
    assert "\033[38;2;126;34;206m" in out
