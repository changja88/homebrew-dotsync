"""Describe sync effects before mutating user files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from dotsync.diffinfo import summarize_pair

ChangeKind = Literal[
    "create",
    "update",
    "remove",
    "unchanged",
    "missing-source",
    "unknown",
]
Direction = Literal["from", "to"]


@dataclass(frozen=True)
class Change:
    label: str
    kind: ChangeKind
    source: Path | None = None
    dest: Path | None = None
    details: str = ""
    file_changes: tuple[str, ...] = ()
    diffable: bool = True

    @property
    def is_change(self) -> bool:
        return self.kind != "unchanged"


@dataclass(frozen=True)
class AppPlan:
    app: str
    direction: Direction
    changes: list[Change]
    description: str = ""

    @property
    def has_changes(self) -> bool:
        return any(c.is_change for c in self.changes)

    def changed_labels(self) -> list[str]:
        return [c.label for c in self.changes if c.is_change]


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _root_safety_error(path: Path, root: Path | None) -> str:
    if root is None:
        return ""
    root_abs = root.absolute()
    path_abs = path.absolute()
    try:
        rel = path_abs.relative_to(root_abs)
    except ValueError:
        return f"{path} is outside {root}"

    root_real = root.resolve()
    path_real = path.resolve()
    if path_real != root_real and root_real not in path_real.parents:
        return f"{path} escapes {root} via symlink"

    current = root_abs
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return f"{current} is a symlink"
    return ""


def plan_file_copy(
    label: str,
    source: Path,
    dest: Path,
    *,
    source_root: Path | None = None,
    dest_root: Path | None = None,
) -> Change:
    source_error = _root_safety_error(source, source_root)
    if source_error:
        return Change(
            label=label, kind="unknown", source=source, dest=dest, details=source_error
        )
    dest_error = _root_safety_error(dest, dest_root)
    if dest_error:
        return Change(
            label=label, kind="unknown", source=source, dest=dest, details=dest_error
        )
    if source.is_symlink():
        return Change(
            label=label,
            kind="unknown",
            source=source,
            dest=dest,
            details="source is a symlink",
        )
    if dest.is_symlink():
        return Change(
            label=label,
            kind="unknown",
            source=source,
            dest=dest,
            details="destination is a symlink",
        )
    if not source.exists():
        return Change(label=label, kind="missing-source", source=source, dest=dest)
    if not dest.exists():
        return Change(label=label, kind="create", source=source, dest=dest)
    if source.is_file() and dest.is_file() and _hash(source) == _hash(dest):
        return Change(label=label, kind="unchanged", source=source, dest=dest)
    return Change(
        label=label,
        kind="update",
        source=source,
        dest=dest,
        details=summarize_pair(source, dest),
    )


@dataclass(frozen=True)
class TreeScan:
    """One directory walked without following links (paths relative to root)."""

    files: frozenset[Path]
    symlinks: frozenset[Path]


def scan_tree(root: Path, ignored_top_dirs: Iterable[str] = ()) -> TreeScan:
    """Classify every entry under `root` as a regular file or a symlink.

    rglob does not descend into symlinked directories, so nothing beneath a
    link is visited and the scan never reads through one. Raises ValueError
    when `root` itself is a symlink or not a directory; an absent root scans
    empty.
    """
    ignored = tuple(ignored_top_dirs)
    if not root.exists():
        return TreeScan(frozenset(), frozenset())
    if root.is_symlink():
        raise ValueError(f"{root} is a symlink")
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    files: set[Path] = set()
    symlinks: set[Path] = set()
    for entry in root.rglob("*"):
        rel = entry.relative_to(root)
        if rel.parts and rel.parts[0] in ignored:
            continue
        if entry.is_symlink():
            symlinks.add(rel)
        elif entry.is_file():
            files.add(rel)
    return TreeScan(frozenset(files), frozenset(symlinks))


def blocked_by_symlink(
    files: Iterable[Path], symlinks: frozenset[Path]
) -> dict[Path, Path]:
    """Map each file that is, or sits under, a symlinked entry to that link."""
    blocked: dict[Path, Path] = {}
    for rel in files:
        for candidate in (rel, *rel.parents):
            if candidate in symlinks:
                blocked[rel] = candidate
                break
    return blocked


@dataclass(frozen=True)
class TreeDiff:
    """What mirroring `source` onto `dest` would touch; links are left alone."""

    creates: frozenset[Path]
    updates: frozenset[Path]
    removes: frozenset[Path]
    skipped: frozenset[Path]  # symlinked entries on either side


def diff_trees(
    source: Path, dest: Path, ignored_top_dirs: Iterable[str] = ()
) -> TreeDiff:
    src = scan_tree(source, ignored_top_dirs)
    dst = scan_tree(dest, ignored_top_dirs)
    src_files = src.files - set(blocked_by_symlink(src.files, dst.symlinks))
    dst_files = dst.files - set(blocked_by_symlink(dst.files, src.symlinks))
    common = src_files & dst_files
    return TreeDiff(
        creates=frozenset(src_files - dst_files),
        updates=frozenset(
            rel for rel in common if _hash(source / rel) != _hash(dest / rel)
        ),
        removes=frozenset(dst_files - src_files),
        skipped=src.symlinks | dst.symlinks,
    )


def plan_tree_mirror(
    label: str,
    source: Path,
    dest: Path,
    ignored_top_dirs: Iterable[str] = (),
    *,
    source_root: Path | None = None,
    dest_root: Path | None = None,
) -> Change:
    source_error = _root_safety_error(source, source_root)
    if source_error:
        return Change(
            label=label, kind="unknown", source=source, dest=dest, details=source_error
        )
    dest_error = _root_safety_error(dest, dest_root)
    if dest_error:
        return Change(
            label=label, kind="unknown", source=source, dest=dest, details=dest_error
        )
    if not source.exists():
        return Change(label=label, kind="missing-source", source=source, dest=dest)

    try:
        diff = diff_trees(source, dest, ignored_top_dirs)
    except ValueError as exc:
        return Change(
            label=label, kind="unknown", source=source, dest=dest, details=str(exc)
        )

    parts: list[str] = []
    if diff.creates:
        parts.append(f"{len(diff.creates)} create")
    if diff.updates:
        parts.append(f"{len(diff.updates)} update")
    if diff.removes:
        parts.append(f"{len(diff.removes)} remove")
    if diff.skipped:
        parts.append(f"{len(diff.skipped)} symlink skipped")

    if not (diff.creates or diff.updates or diff.removes):
        return Change(
            label=label,
            kind="unchanged",
            source=source,
            dest=dest,
            details=", ".join(parts),
        )
    kind: ChangeKind = (
        "create" if diff.creates and not diff.updates and not diff.removes else "update"
    )
    entries = [f"+ {rel.as_posix()}" for rel in sorted(diff.creates)]
    entries += [f"~ {rel.as_posix()}" for rel in sorted(diff.updates)]
    entries += [f"− {rel.as_posix()}" for rel in sorted(diff.removes)]
    entries += [
        f"↷ {rel.as_posix()} (symlink, skipped)" for rel in sorted(diff.skipped)
    ]
    return Change(
        label=label,
        kind=kind,
        source=source,
        dest=dest,
        details=", ".join(parts),
        file_changes=tuple(entries),
    )
