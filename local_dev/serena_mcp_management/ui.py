"""UI primitives for the Serena agent launcher TUI.

This module provides a single-responsibility split of the launcher's screen
concerns:

* State    -- BoxModel / Item dataclasses (this section)
* Renderer -- BoxModel -> ANSI text + in-place updates (later task)
* Progress -- spinner ticker thread (later task)
* Prompt   -- yes/no confirmation (later task)
"""
from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TextIO

try:
    import termios
    import tty
    _RAW_TTY_AVAILABLE = True
except ImportError:  # pragma: no cover - non-Unix
    _RAW_TTY_AVAILABLE = False


PhaseKind = Literal["preflight", "serena-init", "launch-prep", "summary"]
ItemStatus = Literal["pending", "spin", "done", "warn", "skip", "info"]


@dataclass
class Item:
    id: str
    label: str
    value: str
    status: ItemStatus = "pending"


@dataclass(frozen=True)
class SelectOption:
    value: str
    label: str


@dataclass
class BoxModel:
    phase: PhaseKind
    title: str
    items: list[Item] = field(default_factory=list)

    def replace_item(self, new: Item) -> None:
        for index, existing in enumerate(self.items):
            if existing.id == new.id:
                self.items[index] = new
                return
        raise KeyError(f"unknown item id: {new.id}")


# Renderer implementation

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_BOX_WIDTH = 60

# Pre-rendered block banners for the known agent clients. Kept as raw string
# tuples so box rendering stays stdlib-only and deterministic.
_HEADER_ART: dict[str, tuple[str, ...]] = {
    "codex": (
        r"  ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗",
        r" ██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝",
        r" ██║     ██║   ██║██║  ██║█████╗   ╚███╔╝ ",
        r" ██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗ ",
        r" ╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗",
        r"  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝",
    ),
    "claude": (
        r"  ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗",
        r" ██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝",
        r" ██║     ██║     ███████║██║   ██║██║  ██║█████╗  ",
        r" ██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝  ",
        r" ╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗",
        r"  ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝",
    ),
}

# charmbracelet/huh dark theme (ThemeCharm). Truecolor escapes pinned to
# the exact hex values from huh/theme.go so the accents match the screenshots
# in the huh README rather than 256-colour approximations.
PINK = "38;2;247;128;226"  # #F780E2, huh fuchsia (cursor / button accent)
PURPLE = "38;2;117;113;249"  # #7571F9, huh indigo (title / focused tone)
MINT = "38;2;2;191;135"  # #02BF87, huh selected-option green (legible label)
YELLOW = "33"

# RGB endpoints for the cell-by-cell title gradient. Mid is the perceptual
# midpoint between huh fuchsia and indigo, used so the gradient never crosses
# through gray.
_PINK_RGB = (247, 128, 226)
_MID_RGB = (192, 105, 240)
_PURPLE_RGB = (117, 113, 249)

# Length of one full pink → mid → purple → mid → pink cycle, in cells. Picked
# slightly larger than the widest banner (~50 cells) so a single line shows
# roughly one and a quarter cycles.
_GRADIENT_PERIOD = 80


def _ansi(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m"


def style_action_value(value: str, *, accent: str) -> str:
    return _ansi(accent, value)


def style_spinner(frame: int) -> str:
    """Return the spinner glyph for ``frame`` styled with the purple accent."""
    glyph = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    return _ansi(PURPLE, glyph)


_COUNT_KEYWORDS = sorted(
    [
        "memory files reset",
        "sessions deleted",
    ],
    key=len,
    reverse=True,
)


def style_count(phrase: str) -> str:
    """Colorize digits (pink) and count keywords (purple) using the huh palette.

    Plain phrase in, ANSI-formatted phrase out. Unmatched substrings pass through.
    Used by the launcher summary cleanup row.
    """
    if not phrase:
        return phrase
    result = re.sub(r"\d+", lambda m: _ansi(PINK, m.group(0)), phrase)
    for kw in _COUNT_KEYWORDS:
        result = result.replace(kw, _ansi(PURPLE, kw))
    return result


def style_session_tree(
    *,
    client: str,
    groups: tuple[int, int, int] | None,
    records: tuple[int, int, int],
    condition: str,
    cleanup_note: str = "",
) -> str:
    def stats_line(
        branch: str,
        label: str,
        stats: tuple[int, int, int],
    ) -> str:
        total, delete, keep = stats
        label_text = f"{label:<9}"
        return (
            f"{_ansi('90', branch)} {_ansi(MINT, label_text)}"
            f"{_ansi(PINK, f'{total} total')} · "
            f"{_ansi('33', f'{delete} to delete')} · "
            f"{_ansi(MINT, f'{keep} to keep')}"
        )

    lines = [client]
    if groups is not None:
        lines.append(stats_line("├─", "groups", groups))
    lines.append(stats_line("├─", "records", records))
    cleanup = condition if not cleanup_note else f"{condition} · {cleanup_note}"
    cleanup_label = f"{'cleanup':<9}"
    lines.append(
        f"{_ansi('90', '└─')} {_ansi(MINT, cleanup_label)}"
        f"{_ansi(PURPLE, cleanup)}"
    )
    return "\n".join(lines)


def style_memory_tree(*, client: str, stores: int, files: int, scope: str) -> str:
    """Render memory inventory with distinct label, count, and scope roles."""

    def inventory_line(branch: str, label: str, value: str, color: str) -> str:
        return (
            f"{_ansi('90', branch)} {_ansi(MINT, f'{label:<9}')}{_ansi(color, value)}"
        )

    return "\n".join(
        [
            client,
            inventory_line("├─", "stores", f"{stores} found", PINK),
            inventory_line("├─", "files", str(files), PINK),
            inventory_line("└─", "scope", scope, PURPLE),
        ]
    )


def style_mcp_inventory(
    *,
    ps_servers: int,
    managed_servers: int,
    orphan_servers: int,
    leases: int,
    stale_leases: int,
) -> str:
    """Colorize the global Serena MCP preflight inventory."""

    def normal(label: str, value: int) -> str:
        return f"{_ansi(PURPLE, label)}[{_ansi(PINK, str(value))}]"

    def risk(label: str, value: int) -> str:
        if value > 0:
            return f"{_ansi('33', label)}[{_ansi('33', str(value))}]"
        return f"{_ansi('90', label)}[{_ansi('90', str(value))}]"

    return (
        f"{normal('server processes', ps_servers)} "
        f"{_ansi('90', '→')} "
        f"{_ansi(MINT, 'managed servers')}[{_ansi(PINK, str(managed_servers))}] · "
        f"{risk('orphaned servers', orphan_servers)} · "
        f"{normal('leases', leases)} · "
        f"{risk('stale leases', stale_leases)}"
    )


def _marker_for(
    status: ItemStatus,
    *,
    spin_frame: int = 0,
    accent: str = PURPLE,
) -> str:
    if status == "spin":
        frame = SPINNER_FRAMES[spin_frame % len(SPINNER_FRAMES)]
        return _ansi(accent, frame)
    if status == "done":
        return _ansi(PINK, "✓")
    if status == "warn":
        return _ansi(YELLOW, "!")
    if status == "skip":
        return _ansi("90", "-")
    if status == "info":
        return _ansi("90", "·")
    return _ansi("90", "o")  # pending


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _gradient_color(pos: int) -> tuple[int, int, int]:
    """Pink → mid → purple → mid → pink, indexed by cell position."""
    p = (pos % _GRADIENT_PERIOD) / _GRADIENT_PERIOD
    if p < 0.25:
        return _lerp_rgb(_PINK_RGB, _MID_RGB, p / 0.25)
    if p < 0.5:
        return _lerp_rgb(_MID_RGB, _PURPLE_RGB, (p - 0.25) / 0.25)
    if p < 0.75:
        return _lerp_rgb(_PURPLE_RGB, _MID_RGB, (p - 0.5) / 0.25)
    return _lerp_rgb(_MID_RGB, _PINK_RGB, (p - 0.75) / 0.25)


def _gradient_line(line: str) -> str:
    """Paint one art line cell-by-cell with the static gradient.

    Whitespace is left uncolored so the box clip area stays visually clean,
    and consecutive cells of the same color collapse into a single escape to
    keep the rendered byte count low.
    """
    out: list[str] = []
    last: tuple[int, int, int] | None = None
    colored = False
    for i, ch in enumerate(line):
        if ch == " ":
            out.append(" ")
            continue
        color = _gradient_color(i)
        if color != last:
            out.append(f"\x1b[1;38;2;{color[0]};{color[1]};{color[2]}m")
            last = color
            colored = True
        out.append(ch)
    if colored:
        out.append("\x1b[0m")
    return "".join(out)


def render_inline_row(
    label: str,
    value: str,
    *,
    status: ItemStatus,
    accent: str | None = None,
) -> str:
    """Render one BoxModel-style row as a standalone line (no surrounding box).

    Used by the launcher to surface post-install state changes below the
    preflight overview. Redrawing the full box would flash the banner art
    again and push the original overview out of view; an inline row keeps
    the chronological flow intact and matches the row format inside the
    box so the visual style stays consistent.
    """
    marker = _marker_for(status, accent=accent or PURPLE)
    label_color = accent or MINT
    label_text = _ansi(label_color, f"{label:<10}")
    value_text = _ansi(accent, value) if accent is not None else value
    return f"  {marker} {label_text}  {value_text}\n"


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI_ESCAPE_RE.sub("", text))


def _render_item_lines(item: Item, *, spin_frame: int) -> list[str]:
    value_lines = item.value.splitlines() or [""]
    marker = _marker_for(item.status, spin_frame=spin_frame)
    label = _ansi(MINT, f"{item.label:<10}")
    lines = [f"  {marker} {label}  {value_lines[0]}"]
    value_indent = " " * _visible_len(f"  {marker} {item.label:<10}  ")
    lines.extend(f"{value_indent}{line}" for line in value_lines[1:])
    return lines


def _box_width_for(model: BoxModel) -> int:
    width = _BOX_WIDTH
    for item in model.items:
        for row in _render_item_lines(item, spin_frame=0):
            width = max(width, _visible_len(row) - 2)
    return width


def render_box(model: BoxModel, *, spin_frame: int = 0) -> str:
    box_width = _box_width_for(model)
    lines: list[str] = []
    # Double border (pink + purple) — outline-offset look from concept 02.
    lines.append("  " + _ansi(PINK, "─" * box_width))
    lines.append("  " + _ansi(PURPLE, "─" * box_width))
    art = _HEADER_ART.get(model.title)
    if art is not None:
        for art_line in art:
            lines.append("  " + _gradient_line(art_line))
        phase_label = _ansi(PINK, f"·  {model.phase}")
        pad = max(0, box_width - len(art[-1]) - len(model.phase) - 4)
        lines.append("  " + " " * (len(art[-1]) + pad) + phase_label)
    else:
        header = f"{model.title}  ·  {model.phase}"
        lines.append("  " + _ansi(f"1;{PINK}", header))
    for item in model.items:
        lines.extend(_render_item_lines(item, spin_frame=spin_frame))
    lines.append("  " + _ansi(PURPLE, "─" * box_width))
    lines.append("  " + _ansi(PINK, "─" * box_width))
    return "\n".join(lines) + "\n"


class BoxRenderer:
    """Renders a BoxModel and updates it in-place on subsequent draws.

    Uses ANSI cursor-up and erase-below escape sequences to overwrite the
    previous box in-place. Thread-safe via internal lock.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._last_line_count = 0
        self._lock = threading.Lock()

    def draw(self, model: BoxModel, *, spin_frame: int = 0) -> None:
        """Draw the box, updating in-place if previously drawn."""
        text = render_box(model, spin_frame=spin_frame)
        line_count = text.count("\n")
        with self._lock:
            if self._last_line_count > 0:
                self._stream.write(f"\x1b[{self._last_line_count}A\x1b[J")
            self._stream.write(text)
            self._stream.flush()
            self._last_line_count = line_count

    def clear(self) -> None:
        """Clear the box by moving cursor up and erasing."""
        with self._lock:
            if self._last_line_count > 0:
                self._stream.write(f"\x1b[{self._last_line_count}A\x1b[J")
                self._stream.flush()
                self._last_line_count = 0


class SpinnerTicker:
    """Periodically calls ``on_tick`` from a daemon thread until stopped."""

    def __init__(
        self,
        *,
        on_tick: Callable[[int], None],
        interval: float = 0.1,
    ) -> None:
        self._on_tick = on_tick
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)

    def _loop(self) -> None:
        frame = 0
        while not self._stop_event.wait(self._interval):
            frame += 1
            self._on_tick(frame)


# Prompt implementation


def _tty_fd() -> int | None:
    if not _RAW_TTY_AVAILABLE:
        return None
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return None
    if fd < 0 or not os.isatty(fd):
        return None
    return fd


def _read_select_arrow(
    question: str,
    *,
    options: tuple[SelectOption, ...],
    cursor: int,
    stream: TextIO,
    fd: int,
    shortcuts: dict[str, int] | None = None,
    accent: str = PURPLE,
) -> str:
    """Read one option with a huh-inspired raw-terminal arrow selector.

    Renders one line per option, lets the user move with up/down arrows (or
    k/j), and confirms with Enter. The selected option value is returned.
    """
    block_line_count = len(options) + 1

    def render(initial: bool) -> None:
        if not initial:
            # Move cursor back to the start of the prompt block and erase.
            stream.write(f"\x1b[{block_line_count}A\x1b[J")
        stream.write(f"  \x1b[{accent}m?\x1b[0m {question}\n")
        for index, option in enumerate(options):
            if index == cursor:
                stream.write(
                    f"    \x1b[{accent}m▶\x1b[0m "
                    f"\x1b[{accent}m{option.label}\x1b[0m\n"
                )
            else:
                stream.write(f"      \x1b[90m{option.label}\x1b[0m\n")
        stream.flush()

    render(initial=True)
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = os.read(fd, 1).decode(errors="replace")
            if ch == "\x1b":
                seq = os.read(fd, 2).decode(errors="replace")
                if seq == "[A" and cursor > 0:
                    cursor -= 1
                    render(initial=False)
                elif seq == "[B" and cursor < len(options) - 1:
                    cursor += 1
                    render(initial=False)
            elif ch in ("k", "K") and cursor > 0:
                cursor -= 1
                render(initial=False)
            elif ch in ("j", "J") and cursor < len(options) - 1:
                cursor += 1
                render(initial=False)
            elif ch in ("\r", "\n"):
                break
            elif shortcuts is not None and ch.lower() in shortcuts:
                cursor = shortcuts[ch.lower()]
                break
            elif ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        stream.write(f"\x1b[{block_line_count}A\x1b[J")
        stream.flush()
        raise
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    # Collapse the prompt block to a single confirmation line.
    stream.write(f"\x1b[{block_line_count}A\x1b[J")
    chosen = options[cursor]
    stream.write(
        f"  \x1b[{accent}m?\x1b[0m {question} "
        f"\x1b[{accent}m{chosen.label}\x1b[0m\n"
    )
    stream.flush()
    return chosen.value


def _read_yes_no_arrow(
    question: str,
    *,
    default: bool,
    stream: TextIO,
    fd: int,
) -> bool:
    """Read a yes/no choice while preserving the existing y/n shortcuts."""
    options = (
        SelectOption("yes", "Yes"),
        SelectOption("no", "No"),
    )
    selected = _read_select_arrow(
        question,
        options=options,
        cursor=0 if default else 1,
        stream=stream,
        fd=fd,
        shortcuts={"y": 0, "n": 1},
    )
    return selected == "yes"


def _read_select_line(
    question: str,
    *,
    options: tuple[SelectOption, ...],
    default_index: int,
    stream: TextIO,
    input_fn: Callable[[], str],
    accent: str = PURPLE,
) -> str:
    stream.write(f"  {_ansi(accent, '>')} {question}\n")
    for index, option in enumerate(options, start=1):
        stream.write(f"    {_ansi(accent, f'{index}. {option.label}')}\n")

    numbered_values = {
        str(index): option.value for index, option in enumerate(options, start=1)
    }
    while True:
        stream.write(
            f"  {_ansi(accent, '>')} "
            f"Select [1-{len(options)}] (default {default_index + 1}): "
        )
        stream.flush()
        reply = input_fn().strip()
        if not reply:
            return options[default_index].value
        selected = numbered_values.get(reply)
        if selected is not None:
            return selected
        stream.write(f"  ! Enter a number from 1 to {len(options)}.\n")


def select_option(
    question: str,
    *,
    options: tuple[SelectOption, ...],
    default_index: int = 0,
    accent: str = PURPLE,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> str:
    """Prompt for one reusable labeled option and return its stable value."""
    if not options:
        raise ValueError("options must not be empty")
    if not 0 <= default_index < len(options):
        raise ValueError("default_index out of range")

    out = stream if stream is not None else sys.stdout
    fd = _tty_fd() if input_fn is None else None
    if fd is not None:
        return _read_select_arrow(
            question,
            options=options,
            cursor=default_index,
            stream=out,
            fd=fd,
            accent=accent,
        )
    return _read_select_line(
        question,
        options=options,
        default_index=default_index,
        stream=out,
        input_fn=input_fn or input,
        accent=accent,
    )


def confirm(
    question: str,
    *,
    default: bool,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> bool:
    """Prompt for a yes/no confirmation.

    When ``input_fn`` is left at its default and stdin is a TTY, render a
    huh-inspired arrow-key selector (Up/Down/k/j to move, Enter to confirm,
    y/n shortcuts also accepted). Otherwise fall back to a single-line
    text prompt that reads from ``input_fn`` (defaults to builtin input).

    Args:
        question: The prompt text.
        default: The default value if user presses Enter without input.
        stream: Output stream (defaults to sys.stdout).
        input_fn: Optional input function. Passing one forces line-input
            mode (used by tests and non-interactive callers).

    Returns:
        True if user answered yes, False otherwise.
    """
    out = stream if stream is not None else sys.stdout

    fd = _tty_fd() if input_fn is None else None
    if fd is not None:
        return _read_yes_no_arrow(question, default=default, stream=out, fd=fd)

    reader = input_fn if input_fn is not None else input
    suffix = "[Y/n]" if default else "[y/N]"
    out.write(f"  > {question} {suffix} ")
    out.flush()
    reply = reader().strip().lower()
    if not reply:
        return default
    return reply in {"y", "yes"}
