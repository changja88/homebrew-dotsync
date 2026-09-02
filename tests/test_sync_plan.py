from pathlib import Path

import pytest

from dotsync.plan import (
    Change,
    AppPlan,
    diff_trees,
    plan_file_copy,
    plan_tree_mirror,
    scan_tree,
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


def test_plan_file_copy_reports_unknown_for_symlink_source(tmp_path):
    target = tmp_path / "outside"
    target.write_text("secret")
    src = tmp_path / "src"
    src.symlink_to(target)
    dst = tmp_path / "dst"

    change = plan_file_copy("config", src, dst)

    assert change.kind == "unknown"
    assert "symlink" in change.details


def test_plan_file_copy_reports_unknown_for_symlink_destination(tmp_path):
    src = tmp_path / "src"
    src.write_text("safe")
    target = tmp_path / "outside"
    target.write_text("secret")
    dst = tmp_path / "dst"
    dst.symlink_to(target)

    change = plan_file_copy("config", src, dst)

    assert change.kind == "unknown"
    assert "symlink" in change.details


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


def test_plan_tree_mirror_skips_symlink_entries(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("secret")
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "leak.txt").symlink_to(outside)

    change = plan_tree_mirror("rules/", src, dst)

    assert change.kind == "unchanged"
    assert change.details == "1 symlink skipped"


def test_scan_tree_classifies_links_and_does_not_descend_into_them(tmp_path):
    root = tmp_path / "root"
    real = tmp_path / "real"
    (real / "deep").mkdir(parents=True)
    (real / "deep" / "hidden.md").write_text("hidden")
    (root / "plain").mkdir(parents=True)
    (root / "plain" / "SKILL.md").write_text("plain")
    (root / "linked").symlink_to(real, target_is_directory=True)
    (root / "file-link").symlink_to(real / "deep" / "hidden.md")

    scan = scan_tree(root)

    assert scan.files == frozenset({Path("plain/SKILL.md")})
    assert scan.symlinks == frozenset({Path("linked"), Path("file-link")})


def test_scan_tree_rejects_symlinked_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    root = tmp_path / "root"
    root.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        scan_tree(root)


def test_diff_trees_leaves_files_behind_a_link_on_either_side_alone(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "SKILL.md").write_text("canonical")
    local = tmp_path / "local"
    stored = tmp_path / "stored"
    local.mkdir()
    (local / "herdr").symlink_to(canonical, target_is_directory=True)
    (stored / "herdr").mkdir(parents=True)
    (stored / "herdr" / "SKILL.md").write_text("from another machine")

    backup = diff_trees(local, stored)  # local → stored
    apply = diff_trees(stored, local)  # stored → local

    assert backup.removes == frozenset()
    assert apply.creates == frozenset()
    assert backup.skipped == apply.skipped == frozenset({Path("herdr")})


def test_plan_tree_mirror_reports_unknown_for_file_source(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("not a directory")
    dst.mkdir()

    change = plan_tree_mirror("rules/", src, dst)

    assert change.kind == "unknown"
    assert "directory" in change.details


def test_plan_tree_mirror_reports_unknown_for_file_destination(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.write_text("not a directory")

    change = plan_tree_mirror("rules/", src, dst)

    assert change.kind == "unknown"
    assert "directory" in change.details


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


def test_change_defaults_to_empty_file_changes():
    change = Change(label="x", kind="unchanged")
    assert change.file_changes == ()


def test_plan_file_copy_update_carries_line_summary(tmp_path):
    src = tmp_path / "local" / "config"
    dst = tmp_path / "stored" / "config"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_text("a\nb\n")
    dst.write_text("a\n")

    change = plan_file_copy("config", src, dst)

    assert change.kind == "update"
    assert change.details == "+1 −0"


def test_plan_tree_mirror_lists_file_changes(tmp_path):
    src = tmp_path / "local"
    dst = tmp_path / "stored"
    src.mkdir()
    dst.mkdir()
    (src / "new.md").write_text("N")
    (src / "changed.md").write_text("AFTER")
    (dst / "changed.md").write_text("BEFORE")
    (dst / "gone.md").write_text("G")

    change = plan_tree_mirror("commands/", src, dst)

    assert change.kind == "update"
    assert change.file_changes == ("+ new.md", "~ changed.md", "− gone.md")
