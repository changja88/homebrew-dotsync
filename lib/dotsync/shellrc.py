"""Shell rc helpers — detect the user's rc file and idempotently insert the
`export DOTSYNC_DIR=...` line that lets dotsync run from any cwd.

Pure functions only — the caller (cli.py) decides *when* to call us based on
user consent (interactive prompt or `--yes`).
"""

from __future__ import annotations

import contextlib
import os
import shlex
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, Optional


ENV_VAR = "DOTSYNC_DIR"

# Comment we drop one line above the export so future runs can recognize and
# (if the path changed) replace cleanly.
MARKER = "# Added by `dotsync init` — points dotsync at your sync folder."


Action = Literal["added", "updated", "already_set", "rc_missing", "unsupported_shell"]


class ShellRcResult(NamedTuple):
    action: Action
    rc_path: Optional[Path]
    line: str


def export_line(dotsync_dir: Path) -> str:
    """The exact shell-escaped line we maintain in the rc file."""
    return f"export {ENV_VAR}={shlex.quote(str(dotsync_dir))}"


def detect_rc_path(
    shell: Optional[str] = None,
    home: Optional[Path] = None,
) -> Optional[Path]:
    """Return the rc file we should edit for the user's login shell, or None
    for shells we don't know how to update safely (fish, nu, csh, ...).

    Bash on macOS reads `~/.bash_profile` for login shells, so we prefer it
    when it exists. Otherwise fall back to `~/.bashrc`. If neither exists,
    default to `~/.bash_profile` (the macOS convention) — the caller will see
    `rc_missing` and back off rather than create the file silently.
    """
    home = home or Path.home()
    if shell is None:
        shell = os.environ.get("SHELL", "")
    if not shell:
        return None
    name = Path(shell).name
    if name == "zsh":
        return home / ".zshrc"
    if name == "bash":
        bp = home / ".bash_profile"
        br = home / ".bashrc"
        if bp.exists():
            return bp
        if br.exists():
            return br
        return bp
    return None


def rc_candidates(
    shell: Optional[str] = None,
    home: Optional[Path] = None,
) -> list[Path]:
    """The rc files that may hold dotsync's marked export line, most likely
    first. A process started from Finder may have no $SHELL, so the usual
    zsh and bash files follow the detected one."""
    home = home or Path.home()
    candidates: list[Path] = []
    for path in (
        detect_rc_path(shell=shell, home=home),
        home / ".zshrc",
        home / ".bash_profile",
        home / ".bashrc",
    ):
        if path is not None and path not in candidates:
            candidates.append(path)
    return candidates


def marked_dir(rc_path: Path) -> Optional[Path]:
    """The folder named by the export line right after MARKER in rc_path, or
    None. Parsed with shlex like the shell would, never sourced. With several
    marked lines the last one wins, as it does in the shell."""
    try:
        lines = rc_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    found: Optional[Path] = None
    for marker, line in zip(lines, lines[1:]):
        if marker.strip() != MARKER:
            continue
        line = line.strip()
        if not line.startswith(f"export {ENV_VAR}="):
            continue
        try:
            words = shlex.split(line[len("export ") :])
        except ValueError:
            continue
        if len(words) == 1 and words[0].startswith(f"{ENV_VAR}="):
            found = Path(words[0][len(ENV_VAR) + 1 :])
    return found


def update_shell_rc(rc_path: Path, dotsync_dir: Path) -> ShellRcResult:
    """Idempotently ensure rc_path contains the export line for dotsync_dir.

    The line to keep is the one right after MARKER (the last such, as in the
    shell); without a marked line, the first unmarked export line.

    Outcomes:
      - rc file missing       → "rc_missing" (we never create rc files)
      - exact line present    → "already_set"
      - existing export with
        a different path      → "updated" (replace in place)
      - otherwise             → "added"   (append marker + line at EOF)

    Every write goes through _write_atomic: other shells may source the file
    at any moment.
    """
    export = export_line(dotsync_dir)
    if not rc_path.exists():
        return ShellRcResult("rc_missing", rc_path, export)

    text = rc_path.read_text()
    lines = text.splitlines()

    target_idx = _marked_export_index(lines)
    if target_idx is None:
        for i, rc_line in enumerate(lines):
            if rc_line.strip().startswith(f"export {ENV_VAR}="):
                target_idx = i
                break

    if target_idx is not None:
        if lines[target_idx].strip() == export:
            return ShellRcResult("already_set", rc_path, export)
        lines[target_idx] = export
        new_text = "\n".join(lines)
        if text.endswith("\n"):
            new_text += "\n"
        _write_atomic(rc_path, new_text)
        return ShellRcResult("updated", rc_path, export)

    # Append: separate from previous content with a blank line so the marker
    # stands out on `cat ~/.zshrc`.
    if text == "":
        block = f"{MARKER}\n{export}\n"
    elif text.endswith("\n"):
        block = f"\n{MARKER}\n{export}\n"
    else:
        block = f"\n\n{MARKER}\n{export}\n"
    _write_atomic(rc_path, text + block)
    return ShellRcResult("added", rc_path, export)


def _marked_export_index(lines: list[str]) -> Optional[int]:
    """Index of the export line right after the last MARKER line, or None."""
    found = None
    for i in range(len(lines) - 1):
        if lines[i].strip() == MARKER and lines[i + 1].strip().startswith(f"export {ENV_VAR}="):
            found = i + 1
    return found


def _write_atomic(path: Path, text: str) -> None:
    """Replace path's content in one step: a temp file next to the real file,
    fsync, then os.replace. Keeps the file mode; through a symlink, writes the
    link's target and leaves the link."""
    real = path.resolve() if path.is_symlink() else path
    mode = real.stat().st_mode & 0o7777 if real.exists() else 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{real.name}.", suffix=".dotsync-tmp", dir=real.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
