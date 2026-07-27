import re

from local_dev.serena_mcp_management.ui import (
    AMBER,
    GRAY,
    MINT,
    PINK,
    PURPLE,
    _BANNER_BG_RGB,
    _BANNER_MID_RGB,
    _BANNER_PINK_RGB,
    _BANNER_PURPLE_RGB,
    _MID_RGB,
    _PINK_RGB,
    _PURPLE_RGB,
    style_count,
    style_memory_tree,
    style_session_tree,
)


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _rgb_from_ansi(code: str) -> tuple[int, int, int]:
    """Parse ``38;2;R;G;B`` into an RGB triple."""
    _, _, r, g, b = code.split(";")
    return int(r), int(g), int(b)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance."""

    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_on_white(rgb: tuple[int, int, int]) -> float:
    return 1.05 / (_relative_luminance(rgb) + 0.05)


# WCAG AA for body text. The launcher renders these accents as small labels,
# not just large banner art, so the stricter threshold is the right one.
_MIN_CONTRAST = 4.5


def test_palette_uses_light_theme_truecolor_hexes():
    # Hues carried over from huh/theme.go ThemeCharm, darkened until each one
    # clears WCAG AA on a white terminal background. AMBER and GRAY replace the
    # palette-dependent ANSI yellow/bright-black, which wash out on light
    # terminal themes.
    assert PINK == "38;2;216;14;181"
    assert PURPLE == "38;2;102;97;248"
    assert MINT == "38;2;1;135;96"
    assert AMBER == "38;2;180;83;9"
    assert GRAY == "38;2;110;106;133"


def test_palette_accents_are_legible_on_light_background():
    for name, code in (
        ("PINK", PINK),
        ("PURPLE", PURPLE),
        ("MINT", MINT),
        ("AMBER", AMBER),
        ("GRAY", GRAY),
    ):
        ratio = _contrast_on_white(_rgb_from_ansi(code))
        assert ratio >= _MIN_CONTRAST, f"{name} contrast {ratio:.2f}:1 on white"


def test_border_gradient_endpoints_are_legible_on_light_background():
    # These endpoints paint the border ribbons directly on the light
    # terminal background, so they carry the same AA floor as the accents.
    for name, rgb in (
        ("_PINK_RGB", _PINK_RGB),
        ("_MID_RGB", _MID_RGB),
        ("_PURPLE_RGB", _PURPLE_RGB),
    ):
        ratio = _contrast_on_white(rgb)
        assert ratio >= _MIN_CONTRAST, f"{name} contrast {ratio:.2f}:1 on white"


def test_banner_glyphs_are_legible_on_hero_band():
    # Banner glyphs render on the dark hero band, not on white: the bright
    # endpoints must clear the WCAG large-art floor (3:1) against the band.
    def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
        lighter = max(_relative_luminance(a), _relative_luminance(b))
        darker = min(_relative_luminance(a), _relative_luminance(b))
        return (lighter + 0.05) / (darker + 0.05)

    for name, rgb in (
        ("_BANNER_PINK_RGB", _BANNER_PINK_RGB),
        ("_BANNER_MID_RGB", _BANNER_MID_RGB),
        ("_BANNER_PURPLE_RGB", _BANNER_PURPLE_RGB),
    ):
        ratio = contrast(rgb, _BANNER_BG_RGB)
        assert ratio >= 3.0, f"{name} contrast {ratio:.2f}:1 on the band"


def test_style_count_colors_digits_pink():
    result = style_count("2 sessions deleted . 10 memory files reset")
    assert f"\x1b[{PINK}m2\x1b[0m" in result
    assert f"\x1b[{PINK}m10\x1b[0m" in result


def test_style_count_colors_summary_keywords_purple():
    result = style_count("2 sessions deleted . 10 memory files reset")
    assert f"\x1b[{PURPLE}msessions deleted\x1b[0m" in result
    assert f"\x1b[{PURPLE}mmemory files reset\x1b[0m" in result


def test_style_count_passes_through_unmatched():
    assert style_count("") == ""


def test_style_session_tree_colors_counts_and_policy_by_meaning():
    result = style_session_tree(
        client="codex",
        groups=(58, 35, 23),
        records=(855, 358, 497),
        condition="inactive longer than 5 days",
    )

    assert _strip_ansi(result) == (
        "codex\n"
        "├─ groups   58 total · 35 to delete · 23 to keep\n"
        "├─ records  855 total · 358 to delete · 497 to keep\n"
        "└─ cleanup  inactive longer than 5 days"
    )
    assert f"\x1b[{PINK}m58 total\x1b[0m" in result
    assert f"\x1b[{AMBER}m35 to delete\x1b[0m" in result
    assert f"\x1b[{MINT}m23 to keep\x1b[0m" in result
    assert f"\x1b[{PURPLE}minactive longer than 5 days\x1b[0m" in result
    assert f"\x1b[{GRAY}m├─\x1b[0m" in result
    assert f"\x1b[{MINT}mgroups   \x1b[0m" in result


def test_style_memory_tree_assigns_distinct_color_roles():
    value = style_memory_tree(
        client="codex", stores=2, files=17, scope="all known Codex homes"
    )

    assert _strip_ansi(value).splitlines() == [
        "codex",
        "├─ stores   2 found",
        "├─ files    17",
        "└─ scope    all known Codex homes",
    ]
    assert f"\x1b[{PINK}m2 found\x1b[0m" in value
    assert f"\x1b[{MINT}mstores   \x1b[0m" in value
    assert f"\x1b[{PURPLE}mall known Codex homes\x1b[0m" in value
