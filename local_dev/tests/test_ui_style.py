from local_dev.serena_mcp_management.ui import (
    MINT,
    PINK,
    PURPLE,
    style_count,
    style_criteria,
    style_inventory_counts,
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


def test_style_inventory_counts_colors_delete_reset_and_keep_by_meaning():
    result = style_inventory_counts("codex 174 total . 92 to delete . 82 to keep")

    assert "\x1b[33m92 to delete\x1b[0m" in result
    assert f"\x1b[{MINT}m82 to keep\x1b[0m" in result
    assert f"\x1b[{PINK}m174\x1b[0m total" in result


def test_style_inventory_counts_colors_reset_by_meaning():
    result = style_inventory_counts("codex 3 total . 3 to reset . 0 to keep")

    assert "\x1b[33m3 to reset\x1b[0m" in result
    assert f"\x1b[{MINT}m0 to keep\x1b[0m" in result


def test_style_criteria_dims_policy_text():
    assert style_criteria("sessions: same cwd + older than 3d") == (
        "\x1b[90msessions: same cwd + older than 3d\x1b[0m"
    )
