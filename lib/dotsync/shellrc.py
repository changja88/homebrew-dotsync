"""Shell rc helpers — detect the user's rc file and idempotently insert the
`export DOTSYNC_DIR=...` line that lets dotsync run from any cwd.

Pure functions only — the caller (cli.py) decides *when* to call us based on
user consent (interactive prompt or `--yes`).
"""

from __future__ import annotations

import os
import shlex
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


def update_shell_rc(rc_path: Path, dotsync_dir: Path) -> ShellRcResult:
    """Idempotently ensure rc_path contains the export line for dotsync_dir.

    Outcomes:
      - rc file missing       → "rc_missing" (we never create rc files)
      - exact line present    → "already_set"
      - existing export with
        a different path      → "updated" (replace in place)
      - otherwise             → "added"   (append marker + line at EOF)
    """
    export = export_line(dotsync_dir)
    if not rc_path.exists():
        return ShellRcResult("rc_missing", rc_path, export)

    text = rc_path.read_text()
    lines = text.splitlines()

    target_idx = None
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
        rc_path.write_text(new_text)
        return ShellRcResult("updated", rc_path, export)

    # Append: separate from previous content with a blank line so the marker
    # stands out on `cat ~/.zshrc`.
    if text == "":
        block = f"{MARKER}\n{export}\n"
    elif text.endswith("\n"):
        block = f"\n{MARKER}\n{export}\n"
    else:
        block = f"\n\n{MARKER}\n{export}\n"
    rc_path.write_text(text + block)
    return ShellRcResult("added", rc_path, export)
