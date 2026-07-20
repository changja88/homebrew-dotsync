import re

from local_dev.serena_mcp_management.ui import (
    MINT,
    PINK,
    PURPLE,
    style_count,
    style_session_tree,
)


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def test_palette_uses_huh_truecolor_hexes():
    # huh/theme.go ThemeCharm dark: indigo #7571F9, fuchsia #F780E2,
    # selected-option green #02BF87.
    assert PINK == "38;2;247;128;226"
    assert PURPLE == "38;2;117;113;249"
    assert MINT == "38;2;2;191;135"


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
    assert "\x1b[33m35 to delete\x1b[0m" in result
    assert f"\x1b[{MINT}m23 to keep\x1b[0m" in result
    assert f"\x1b[{PURPLE}minactive longer than 5 days\x1b[0m" in result
    assert "\x1b[90m├─\x1b[0m" in result
    assert f"\x1b[{MINT}mgroups   \x1b[0m" in result
