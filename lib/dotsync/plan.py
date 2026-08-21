"""Describe sync effects before mutating user files."""

from __future__ import annotations

import hashlib
import os
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

    def to_dict(self, *, relative_to: Path | None = None) -> dict[str, object]:
        """Return a JSON-safe plan record without including file contents."""
        base = (relative_to or Path.cwd()).absolute()
        return {
            "label": self.label,
            "kind": self.kind,
            "source": _path_snapshot(self.source, relative_to=base),
            "dest": _path_snapshot(self.dest, relative_to=base),
            "details": self.details,
            "file_changes": list(self.file_changes),
            "diffable": self.diffable,
        }


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

    def to_dict(self, *, relative_to: Path | None = None) -> dict[str, object]:
        """Return a JSON-safe app plan in its declared change order."""
        return {
            "app": self.app,
            "direction": self.direction,
            "description": self.description,
            "changes": [
                change.to_dict(relative_to=relative_to) for change in self.changes
            ],
        }


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _relative_path(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path.absolute(), start=base)).as_posix()


def _path_snapshot(
    path: Path | None, *, relative_to: Path
) -> dict[str, object] | None:
    """Describe the filesystem state used by a plan without storing bytes."""
    if path is None:
        return None

    snapshot: dict[str, object] = {
        "path": _relative_path(path, relative_to),
    }
    if path.is_symlink():
        stat = path.lstat()
        snapshot.update(kind="symlink", mtime_ns=stat.st_mtime_ns)
        return snapshot
    if not path.exists():
        snapshot["kind"] = "missing"
        return snapshot

    stat = path.stat()
    snapshot["mtime_ns"] = stat.st_mtime_ns
    if path.is_file():
        snapshot.update(kind="file", sha256=_hash(path))
        return snapshot
    if path.is_dir():
        files: list[dict[str, object]] = []
        for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
            if child.is_symlink():
                child_stat = child.lstat()
                files.append(
                    {
                        "path": child.relative_to(path).as_posix(),
                        "kind": "symlink",
                        "mtime_ns": child_stat.st_mtime_ns,
                    }
                )
            elif child.is_file():
                child_stat = child.stat()
                files.append(
                    {
                        "path": child.relative_to(path).as_posix(),
                        "kind": "file",
                        "mtime_ns": child_stat.st_mtime_ns,
                        "sha256": _hash(child),
                    }
                )
        snapshot.update(kind="directory", files=files)
        return snapshot

    snapshot["kind"] = "other"
    return snapshot


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


def _tree_files(root: Path, ignored_top_dirs: Iterable[str] = ()) -> set[Path]:
    ignored = tuple(ignored_top_dirs)
    if not root.exists():
        return set()
    if root.is_symlink():
        raise ValueError(f"{root} is a symlink")
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    files: set[Path] = set()
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if rel.parts and rel.parts[0] in ignored:
            continue
        if f.is_symlink():
            raise ValueError(f"{f} is a symlink")
        if f.is_file():
            files.add(rel)
    return files


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
        source_files = _tree_files(source, ignored_top_dirs)
        dest_files = _tree_files(dest, ignored_top_dirs)
    except ValueError as exc:
        return Change(
            label=label, kind="unknown", source=source, dest=dest, details=str(exc)
        )
    creates = source_files - dest_files
    removes = dest_files - source_files
    common = source_files & dest_files
    updates = {rel for rel in common if _hash(source / rel) != _hash(dest / rel)}

    parts: list[str] = []
    if creates:
        parts.append(f"{len(creates)} create")
    if updates:
        parts.append(f"{len(updates)} update")
    if removes:
        parts.append(f"{len(removes)} remove")

    if not parts:
        return Change(label=label, kind="unchanged", source=source, dest=dest)
    kind: ChangeKind = "create" if creates and not updates and not removes else "update"
    entries = [f"+ {rel.as_posix()}" for rel in sorted(creates)]
    entries += [f"~ {rel.as_posix()}" for rel in sorted(updates)]
    entries += [f"− {rel.as_posix()}" for rel in sorted(removes)]
    return Change(
        label=label,
        kind=kind,
        source=source,
        dest=dest,
        details=", ".join(parts),
        file_changes=tuple(entries),
    )
