from local_dev.serena_mcp_management.ui import (
    MINT,
    PINK,
    PURPLE,
    style_cleanup_segments,
    style_count,
    style_criteria,
    style_session_counts,
)


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


def test_style_session_counts_colors_complete_totals_pink():
    result = style_session_counts("codex 58 groups · 855 records")

    assert f"\x1b[{PINK}m58 groups\x1b[0m" in result
    assert f"\x1b[{PINK}m855 records\x1b[0m" in result


def test_style_cleanup_segments_colors_each_meaning():
    result = style_cleanup_segments(
        "inactive longer than 5 days",
        "delete 35 groups / 358 records",
        "keep 23 groups / 497 records",
    )

    assert f"\x1b[{PURPLE}minactive longer than 5 days\x1b[0m" in result
    assert "\x1b[33mdelete 35 groups / 358 records\x1b[0m" in result
    assert f"\x1b[{MINT}mkeep 23 groups / 497 records\x1b[0m" in result


def test_style_criteria_dims_policy_text():
    assert style_criteria("sessions: same cwd + older than 3d") == (
        "\x1b[90msessions: same cwd + older than 3d\x1b[0m"
    )
