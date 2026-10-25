"""Group sessions by the project they belong to, from each session's cwd."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess

from dotsync.sessions import procs, roots

GIT_TIMEOUT = 5  # seconds, per cwd
OUTSIDE = "outside"
UNKNOWN = "unknown"
OUTSIDE_NAME = "프로젝트 밖"
UNKNOWN_NAME = "원본 미확인"


@dataclass(frozen=True)
class Place:
    key: str  # an absolute repo-root path, "outside" or "unknown"
    status: str  # "exists", "deleted" or "unreadable"


class RepoResolver:
    """Finds the main work tree's root for a cwd. A linked worktree maps to
    the repository it was added from. Runs git once per distinct cwd."""

    def __init__(self, run: Callable[..., CompletedProcess[str]] = procs.run) -> None:
        self._run = run
        self._cache: dict[str, str | None] = {}

    def root_of(self, cwd: str) -> str | None:
        if cwd not in self._cache:
            self._cache[cwd] = self._ask_git(cwd)
        return self._cache[cwd]

    def _ask_git(self, cwd: str) -> str | None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        argv = ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"]
        try:
            result = self._run(argv, timeout=GIT_TIMEOUT, env=env)
        except procs.ToolFailure:
            return None
        common = result.stdout.strip()
        if result.returncode != 0 or not common:
            return None
        path = Path(common)
        return str(path.parent) if path.name == ".git" else str(path)


def cwd_status(cwd: str) -> str:
    try:
        os.stat(cwd)
    except (FileNotFoundError, NotADirectoryError):
        return "deleted"
    except PermissionError:
        return "unreadable"
    return "exists"


def _is_under(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


class Locator:
    def __init__(
        self,
        resolver: RepoResolver,
        home: Path,
        temp_roots: list[Path],
        tool_homes: list[Path],
    ) -> None:
        self._resolver = resolver
        self._home = str(home)
        self._temp_roots = [str(p) for p in temp_roots]
        self._tool_homes = [str(p) for p in tool_homes]

    @staticmethod
    def default(resolver: RepoResolver | None = None) -> Locator:
        temp_roots = [Path("/private/tmp"), Path("/tmp"), Path("/var/folders")]
        if os.environ.get("TMPDIR"):
            temp_roots.append(Path(os.environ["TMPDIR"]).resolve())
        return Locator(
            resolver or RepoResolver(),
            Path.home(),
            temp_roots,
            [roots.claude_home(), roots.codex_home()],
        )

    def known_roots(self, cwds: Iterable[str]) -> set[str]:
        found: set[str] = set()
        for cwd in set(cwds):
            if cwd_status(cwd) == "exists":
                root = self._resolver.root_of(cwd)
                if root is not None:
                    found.add(root)
        return found

    def place(self, cwd: str, roots: set[str]) -> Place:
        status = cwd_status(cwd)
        if status == "unreadable":
            return Place(UNKNOWN, status)
        if status == "exists":
            return Place(self._resolver.root_of(cwd) or OUTSIDE, status)
        if self._is_scratch(cwd):
            return Place(OUTSIDE, status)
        herdr_root = self._herdr_lane_root(cwd, roots)
        if herdr_root is not None:
            return Place(herdr_root, status)
        containing = [r for r in roots if cwd.startswith(r.rstrip("/") + "/")]
        if containing:
            return Place(max(containing, key=len), status)
        return Place(UNKNOWN, status)

    def _is_scratch(self, cwd: str) -> bool:
        return (
            cwd == self._home
            or any(_is_under(cwd, t) for t in self._temp_roots)
            or any(_is_under(cwd, t) for t in self._tool_homes)
        )

    def _herdr_lane_root(self, cwd: str, known: set[str]) -> str | None:
        prefix = f"{self._home}/.herdr/worktrees/"
        if not cwd.startswith(prefix):
            return None
        parts = cwd[len(prefix):].split("/")
        if len(parts) < 2 or not parts[0]:  # needs <repo>/<lane>
            return None
        matches = [r for r in known if os.path.basename(r) == parts[0]]
        return matches[0] if len(matches) == 1 else None


def groups(items: list[dict], places: dict[str, Place]) -> list[dict]:
    """Bucket `items` by place. `last_activity` is an ISO 8601 UTC string, so
    sorting the strings sorts by time."""
    buckets: dict[str, dict] = {}
    cwd_status_by_key: dict[str, dict[str, str]] = {}
    for item in items:
        place = places[item["cwd"]]
        bucket = buckets.setdefault(
            place.key,
            {"key": place.key, "name": _name(place.key), "bytes": 0, "running": 0, "items": []},
        )
        cwd_status_by_key.setdefault(place.key, {})[item["cwd"]] = place.status
        if place.key == UNKNOWN:
            reason = "cwd_unreadable" if place.status == "unreadable" else "not_found"
            item = {**item, "place_reason": reason}
        bucket["items"].append(item)
        bucket["bytes"] += item["bytes"]
        bucket["running"] += item["state"] == "running"
    result: list[dict] = []
    for key, bucket in buckets.items():
        statuses = cwd_status_by_key[key]
        bucket["items"].sort(key=lambda i: i["last_activity"], reverse=True)
        result.append(
            {
                "key": key,
                "name": bucket["name"],
                "folders": len(statuses),
                "folders_deleted": sum(s == "deleted" for s in statuses.values()),
                "sessions": len(bucket["items"]),
                "bytes": bucket["bytes"],
                "running": bucket["running"],
                "items": bucket["items"],
            }
        )
    result.sort(key=lambda g: g["bytes"], reverse=True)
    return result


def _name(key: str) -> str:
    if key == OUTSIDE:
        return OUTSIDE_NAME
    if key == UNKNOWN:
        return UNKNOWN_NAME
    return os.path.basename(key)
