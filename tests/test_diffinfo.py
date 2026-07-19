from dotsync.diffinfo import summarize_pair


def test_summarize_pair_counts_added_and_removed_lines(tmp_path):
    dest = tmp_path / "stored.txt"
    source = tmp_path / "local.txt"
    dest.write_text("a\nb\n")
    source.write_text("a\nc\nd\n")

    assert summarize_pair(source, dest) == "+2 −1"


def test_summarize_pair_identical_content_reports_zero(tmp_path):
    dest = tmp_path / "stored.txt"
    source = tmp_path / "local.txt"
    dest.write_text("a\n")
    source.write_text("a")  # 해시는 다르지만 라인 내용 동일

    assert summarize_pair(source, dest) == "+0 −0"


def test_summarize_pair_binary_falls_back_to_sizes(tmp_path):
    source = tmp_path / "a.bttpreset"
    dest = tmp_path / "b.bttpreset"
    source.write_bytes(b"\xff\xfe" * 100)  # 200B, invalid UTF-8
    dest.write_bytes(b"\xff" * 50)  # 50B

    assert summarize_pair(source, dest) == "binary · 50B → 200B"


def test_summarize_pair_unreadable_file_returns_empty(tmp_path):
    source = tmp_path / "missing.txt"  # 존재하지 않음 → OSError
    dest = tmp_path / "stored.txt"
    dest.write_text("a\n")

    assert summarize_pair(source, dest) == ""


def test_summarize_pair_json_lists_changed_top_level_keys(tmp_path):
    dest = tmp_path / "settings.json"
    source = tmp_path / "local.json"
    dest.write_text('{"model": "opus", "keep": 1}')
    source.write_text('{"model": "fable", "keep": 1, "hooks": {"a": 1}}')

    assert summarize_pair(source, dest) == "+1 −1 · model, hooks"


def test_summarize_pair_json_includes_removed_keys(tmp_path):
    dest = tmp_path / "settings.json"
    source = tmp_path / "local.json"
    dest.write_text('{"gone": 1, "keep": 1}')
    source.write_text('{"keep": 1}')

    assert summarize_pair(source, dest) == "+1 −1 · gone"


def test_summarize_pair_key_list_caps_at_four(tmp_path):
    dest = tmp_path / "settings.json"
    source = tmp_path / "local.json"
    dest.write_text("{}")
    source.write_text('{"k1": 1, "k2": 2, "k3": 3, "k4": 4, "k5": 5, "k6": 6}')

    assert summarize_pair(source, dest) == "+1 −1 · k1, k2, k3, k4 …외 2"


def test_summarize_pair_toml_lists_changed_tables(tmp_path):
    dest = tmp_path / "config.toml"
    source = tmp_path / "local.toml"
    dest.write_text('notify = ["x"]\n\n[tui]\na = 1\n')
    source.write_text("[tui]\na = 2\n")

    out = summarize_pair(source, dest)
    assert out.endswith("· tui, notify")


def test_summarize_pair_invalid_json_falls_back_to_line_counts(tmp_path):
    dest = tmp_path / "broken.json"
    source = tmp_path / "local.json"
    dest.write_text("{not json")
    source.write_text("{not json either")

    assert summarize_pair(source, dest) == "+1 −1"
