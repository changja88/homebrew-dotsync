"""Describe sync effects before mutating user files."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

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


def plan_file_copy(label: str, source: Path, dest: Path) -> Change:
    if not source.exists():
        return Change(label=label, kind="missing-source", source=source, dest=dest)
    if not dest.exists():
        return Change(label=label, kind="create", source=source, dest=dest)
    if source.is_file() and dest.is_file() and _hash(source) == _hash(dest):
        return Change(label=label, kind="unchanged", source=source, dest=dest)
    return Change(label=label, kind="update", source=source, dest=dest)


def _tree_files(root: Path, ignored_top_dirs: Iterable[str] = ()) -> set[Path]:
    ignored = tuple(ignored_top_dirs)
    if not root.exists():
        return set()
    return {
        f.relative_to(root)
        for f in root.rglob("*")
        if f.is_file()
        and not (
            f.relative_to(root).parts and f.relative_to(root).parts[0] in ignored
        )
    }


def plan_tree_mirror(
    label: str,
    source: Path,
    dest: Path,
    ignored_top_dirs: Iterable[str] = (),
) -> Change:
    if not source.exists():
        return Change(label=label, kind="missing-source", source=source, dest=dest)

    source_files = _tree_files(source, ignored_top_dirs)
    dest_files = _tree_files(dest, ignored_top_dirs)
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
    return Change(
        label=label,
        kind=kind,
        source=source,
        dest=dest,
        details=", ".join(parts),
    )
