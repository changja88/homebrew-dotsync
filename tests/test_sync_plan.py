from pathlib import Path

from dotsync.plan import (
    Change,
    AppPlan,
    plan_file_copy,
    plan_tree_mirror,
)


def test_plan_file_copy_reports_create_when_destination_missing(tmp_path):
    src = tmp_path / "local" / "config"
    dst = tmp_path / "stored" / "config"
    src.parent.mkdir()
    src.write_text("A")

    change = plan_file_copy("config", src, dst)

    assert change == Change(
        label="config",
        kind="create",
        source=src,
        dest=dst,
        details="",
    )


def test_plan_file_copy_reports_update_when_bytes_differ(tmp_path):
    src = tmp_path / "local" / "config"
    dst = tmp_path / "stored" / "config"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_text("A")
    dst.write_text("B")

    change = plan_file_copy("config", src, dst)

    assert change.kind == "update"
    assert change.label == "config"


def test_plan_file_copy_reports_unchanged_when_bytes_match(tmp_path):
    src = tmp_path / "local" / "config"
    dst = tmp_path / "stored" / "config"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_text("A")
    dst.write_text("A")

    change = plan_file_copy("config", src, dst)

    assert change.kind == "unchanged"


def test_plan_file_copy_reports_missing_source(tmp_path):
    src = tmp_path / "local" / "missing"
    dst = tmp_path / "stored" / "config"

    change = plan_file_copy("config", src, dst)

    assert change.kind == "missing-source"
    assert change.label == "config"


def test_plan_tree_mirror_summarizes_create_update_and_remove(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "new.txt").write_text("new")
    (src / "same.txt").write_text("same")
    (dst / "same.txt").write_text("same")
    (src / "changed.txt").write_text("new")
    (dst / "changed.txt").write_text("old")
    (dst / "removed.txt").write_text("remove")

    change = plan_tree_mirror("rules/", src, dst)

    assert change.kind == "update"
    assert change.label == "rules/"
    assert change.details == "1 create, 1 update, 1 remove"


def test_plan_tree_mirror_reports_unchanged_for_matching_trees(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "same.txt").write_text("same")
    (dst / "same.txt").write_text("same")

    change = plan_tree_mirror("rules/", src, dst)

    assert change.kind == "unchanged"
    assert change.details == ""


def test_app_plan_changed_excludes_unchanged_but_includes_missing_source(tmp_path):
    plan = AppPlan(
        app="zsh",
        direction="from",
        changes=[
            Change("same", "unchanged", tmp_path / "a", tmp_path / "b"),
            Change("missing", "missing-source", tmp_path / "c", tmp_path / "d"),
        ],
    )

    assert plan.has_changes
    assert plan.changed_labels() == ["missing"]
