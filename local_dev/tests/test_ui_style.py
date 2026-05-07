from local_dev.serena_mcp_management.ui import style_count


def test_style_count_colors_digits_cyan():
    result = style_count("0 to delete . 103 to keep")
    # cyan = "\x1b[36m"
    assert "\x1b[36m0\x1b[0m" in result
    assert "\x1b[36m103\x1b[0m" in result


def test_style_count_colors_keywords_yellow():
    result = style_count("0 to delete . 103 to keep")
    # yellow = "\x1b[33m"
    assert "\x1b[33mto delete\x1b[0m" in result
    assert "\x1b[33mto keep\x1b[0m" in result


def test_style_count_colors_files_reset_keyword():
    result = style_count("0 files to reset")
    assert "\x1b[33mfiles to reset\x1b[0m" in result
    assert "\x1b[36m0\x1b[0m" in result


def test_style_count_colors_deleted_and_kept():
    result = style_count("2 deleted . 10 memory files reset")
    assert "\x1b[33mdeleted\x1b[0m" in result
    assert "\x1b[33mmemory files reset\x1b[0m" in result


def test_style_count_colors_scan_skipped():
    result = style_count("scan skipped (jq missing)")
    assert "\x1b[33mscan skipped\x1b[0m" in result


def test_style_count_passes_through_unmatched():
    # Empty or unrelated strings should be returned unchanged structure-wise
    assert style_count("") == ""
