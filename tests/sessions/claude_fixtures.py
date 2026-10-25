"""Builds fake ~/.claude trees for the Claude session tests."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


def write_jsonl(path: Path, records: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def make_session(
    home: Path,
    project: str,
    sid: str,
    cwd: str,
    records: Iterable[dict],
    *,
    subagents: int = 0,
    extra: Iterable[str] = (),
) -> Path:
    """Write `home/projects/<project>/<sid>.jsonl` (user records without a
    `cwd` get `cwd`), `subagents` subagent transcripts, and one small file at
    each `extra` path relative to `home`. Returns the transcript path."""
    folder = home / "projects" / project
    transcript = folder / f"{sid}.jsonl"
    write_jsonl(
        transcript,
        [
            {"cwd": cwd, **r} if r.get("type") == "user" else r
            for r in records
        ],
    )
    for i in range(subagents):
        write_jsonl(folder / sid / "subagents" / f"agent-{i}.jsonl", [{"type": "user"}])
    for rel in extra:
        target = home / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x" * 10)
    return transcript


def user(text: str, **fields: object) -> dict:
    return {"type": "user", "message": {"content": text}, **fields}
