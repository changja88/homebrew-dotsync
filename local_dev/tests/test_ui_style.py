from local_dev.serena_mcp_management.ui import MINT, PINK, PURPLE, style_count


def test_palette_uses_charm_truecolor_hexes():
    # PINK = #FF06B7, PURPLE = #874BFD, MINT = #00D7AF -- charm/huh tones
    assert PINK == "38;2;255;6;183"
    assert PURPLE == "38;2;135;75;253"
    assert MINT == "38;2;0;215;175"


def test_style_count_colors_digits_pink():
    result = style_count("0 to delete . 103 to keep")
    assert f"\x1b[{PINK}m0\x1b[0m" in result
    assert f"\x1b[{PINK}m103\x1b[0m" in result


def test_style_count_colors_keywords_purple():
    result = style_count("0 to delete . 103 to keep")
    assert f"\x1b[{PURPLE}mto delete\x1b[0m" in result
    assert f"\x1b[{PURPLE}mto keep\x1b[0m" in result


def test_style_count_colors_files_reset_keyword():
    result = style_count("0 files to reset")
    assert f"\x1b[{PURPLE}mfiles to reset\x1b[0m" in result
    assert f"\x1b[{PINK}m0\x1b[0m" in result


def test_style_count_colors_deleted_and_kept():
    result = style_count("2 deleted . 10 memory files reset")
    assert f"\x1b[{PURPLE}mdeleted\x1b[0m" in result
    assert f"\x1b[{PURPLE}mmemory files reset\x1b[0m" in result


def test_style_count_colors_scan_skipped():
    result = style_count("scan skipped (jq missing)")
    assert f"\x1b[{PURPLE}mscan skipped\x1b[0m" in result


def test_style_count_passes_through_unmatched():
    assert style_count("") == ""
