"""Canonical product paths shared by local launcher inventories."""
from __future__ import annotations

from pathlib import Path

_UNSAFE_SHARED_STORAGE_PREFIXES = (
    Path("/tmp"),
    Path("/private/tmp"),
    Path("/var"),
    Path("/private/var"),
    Path("/Users/Shared"),
    Path("/System"),
    Path("/Library"),
    Path("/Applications"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/etc"),
    Path("/dev"),
)


def paths_refer_to_same_file(first: Path, second: Path) -> bool:
    """Compare existing paths by identity and missing paths lexically."""
    try:
        return first.samefile(second)
    except FileNotFoundError:
        return first.resolve(strict=False) == second.resolve(strict=False)


def deduplicate_paths_by_identity(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Keep the first spelling of each filesystem object in stable order."""
    unique: list[Path] = []
    for path in paths:
        if any(paths_refer_to_same_file(path, seen) for seen in unique):
            continue
        unique.append(path)
    return tuple(unique)


def canonical_codex_homes(
    *,
    home: Path,
    codex_home: Path,
) -> tuple[tuple[Path, ...], Path]:
    homes, default_home = lexical_codex_homes(
        home=home,
        codex_home=codex_home,
    )
    default_home = default_home.resolve(strict=False)
    homes = deduplicate_paths_by_identity(
        tuple(candidate.resolve(strict=False) for candidate in homes)
    )
    return homes, default_home


def lexical_codex_homes(
    *,
    home: Path,
    codex_home: Path,
) -> tuple[tuple[Path, ...], Path]:
    active_home = codex_home.expanduser()
    if not active_home.is_absolute():
        raise ValueError("codex_home must be absolute")
    default_home = _absolute_without_resolving(home / ".codex")
    active_home = _absolute_without_resolving(active_home)
    homes = tuple(dict.fromkeys((default_home, active_home)))
    return homes, default_home


def lexical_claude_config_dir(
    *,
    home: Path,
    claude_config_dir: Path | None = None,
) -> Path:
    candidate = (claude_config_dir or home / ".claude").expanduser()
    if not candidate.is_absolute():
        raise ValueError("claude_config_dir must be absolute")
    return _absolute_without_resolving(candidate)


def is_unsafe_shared_storage_root(path: Path, *, home: Path) -> bool:
    """Reject shared/system roots while allowing dedicated user-owned leaves."""

    candidate = _absolute_without_resolving(path)
    user_home = _absolute_without_resolving(home)
    if user_home in candidate.parents:
        return False
    if len(candidate.parts) <= 3:
        return True
    return any(
        prefix == candidate or prefix in candidate.parents
        for prefix in _UNSAFE_SHARED_STORAGE_PREFIXES
    )


def _absolute_without_resolving(path: Path) -> Path:
    return path.absolute()
