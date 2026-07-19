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
