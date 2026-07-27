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

# 8x12 pixel letterforms ("#" = lit) for the client banners, rendered two
# pixel rows per terminal row with half blocks. Clean geometric shapes:
# a uniform 2-pixel stroke and stepped corner rounding, no serif flares.
# Solid pixel mass keeps the banner readable on a light background where the
# old shadow-line block font (██╗ …) fell apart into thin outlines.
_BANNER_GLYPHS: dict[str, tuple[str, ...]] = {
    "A": (
        "..####..",
        ".######.",
        "##....##",
        "##....##",
        "##....##",
        "########",
        "########",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
    ),
    "C": (
        "..######",
        ".#######",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        ".#######",
        "..######",
    ),
    "D": (
        "######..",
        "#######.",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "#######.",
        "######..",
    ),
    "E": (
        "########",
        "########",
        "##......",
        "##......",
        "##......",
        "#######.",
        "#######.",
        "##......",
        "##......",
        "##......",
        "########",
        "########",
    ),
    "L": (
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "##......",
        "########",
        "########",
    ),
    "O": (
        "..####..",
        ".######.",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        ".######.",
        "..####..",
    ),
    "U": (
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        ".######.",
        "..####..",
    ),
    "X": (
        "##....##",
        "##....##",
        ".##..##.",
        ".##..##.",
        "..####..",
        "..####..",
        "..####..",
        "..####..",
        ".##..##.",
        ".##..##.",
        "##....##",
        "##....##",
    ),
}

_BANNER_WORDS: dict[str, str] = {"codex": "CODEX", "claude": "CLAUDE"}
_GLYPH_GAP = 1  # blank pixel columns between letters (tight, wordmark-style)

# Hero band behind the banner (Gemini-CLI style): a dark indigo backdrop that
# spans the full box width, so the glyph gradient glows even on a light
# terminal instead of floating on bare white. Glyphs cast a darker flat drop
# shadow down-right onto the band, and use the original bright huh hues —
# the AA-darkened accents are for white, these sit on the dark band.
_BANNER_BG_RGB = (27, 24, 48)  # #1B1830
_BANNER_SHADOW_RGB = (13, 11, 26)  # #0D0B1A
_BANNER_PINK_RGB = (247, 128, 226)  # #F780E2
_BANNER_MID_RGB = (192, 105, 240)  # #C069F0
_BANNER_PURPLE_RGB = (117, 113, 249)  # #7571F9
_BAND_PAD_ROWS = 2  # pixel rows of band above the glyphs / below the shadow
_SHADOW_DROP_ROWS = 2  # pixel rows the drop shadow falls below the glyphs
_SHADOW_DROP_COLS = 1  # cells the drop shadow falls to the right

# Band texture. A flat fill reads as monotonous, so the band soaks up a
# fraction of the glyph gradient (a diagonal tint sweep at terminal-row
# granularity — both pixel halves of a cell match, keeping band cells cheap
# spaces) and carries a sparse deterministic starfield at pixel granularity.
_BAND_TINT = 0.16
_STAR_DIM_RGB = (96, 88, 148)  # #605894 — faint half-pixel sparkle
_STAR_BRIGHT_RGB = (156, 146, 208)  # #9C92D0 — rare brighter sparkle


def _band_color(col: int, term_row: int) -> tuple[int, int, int]:
    accent = _banner_gradient_color(col + term_row * 2 * _HALF_ROW_DRIFT)
    return _lerp_rgb(_BANNER_BG_RGB, accent, _BAND_TINT)


def _star_at(col: int, row: int) -> tuple[int, int, int] | None:
    """Deterministic sparse starfield for band pixels (no randomness).

    Small coordinates need the avalanche finalizer — a bare multiply-xor
    clusters hits along the low-index edges of the band.
    """
    h = (col * 73856093) ^ (row * 19349663)
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    if h % 211 == 7:
        return _STAR_BRIGHT_RGB
    if h % 79 == 0:
        return _STAR_DIM_RGB
    return None

# Light-terminal palette. The hues are charmbracelet/huh's ThemeCharm accents
# (fuchsia #F780E2, indigo #7571F9, green #02BF87), but huh tunes those for a
# dark background: on white they fall to 2.3-3.8:1 and the banner washes out.
# Each accent below keeps its hue and saturation and is darkened only until it
# clears WCAG AA (4.5:1) against white. AMBER and GRAY replace ANSI yellow and
# bright-black — both follow the terminal palette and routinely wash out on
# light themes. Verified by tests/test_ui_style.py.
PINK = "38;2;216;14;181"  # #D80EB5, 4.52:1 on white (cursor / button accent)
PURPLE = "38;2;102;97;248"  # #6661F8, 4.54:1 on white (title / focused tone)
MINT = "38;2;1;135;96"  # #018760, 4.53:1 on white (legible label)
AMBER = "38;2;180;83;9"  # #B45309, 5.02:1 on white (warn / destructive count)
GRAY = "38;2;110;106;133"  # #6E6A85, 5.17:1 on white (muted / tree lines)

# RGB endpoints for the cell-by-cell title gradient. Mid is the perceptual
# midpoint between the pink and purple accents, used so the gradient never
# crosses through gray. Same light-background contrast floor applies.
_PINK_RGB = (216, 14, 181)
_MID_RGB = (173, 61, 236)
_PURPLE_RGB = (102, 97, 248)

# Length of one full pink → mid → purple → mid → pink cycle, in cells. Picked
# slightly larger than the widest banner (~50 cells) so a single line shows
# roughly one and a quarter cycles.
_GRADIENT_PERIOD = 80

# Gradient phase added per banner pixel row (half a terminal row). Each pixel
# row starts a little further into the cycle, turning the horizontal sweep
# into a diagonal one. The cycle is seamless (pink → purple → pink), so the
# widest banner simply sweeps through roughly one full cycle corner to corner.
_HALF_ROW_DRIFT = 2


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
            f"{_ansi(GRAY, branch)} {_ansi(MINT, label_text)}"
            f"{_ansi(PINK, f'{total} total')} · "
            f"{_ansi(AMBER, f'{delete} to delete')} · "
            f"{_ansi(MINT, f'{keep} to keep')}"
        )

    lines = [client]
    if groups is not None:
        lines.append(stats_line("├─", "groups", groups))
    lines.append(stats_line("├─", "records", records))
    cleanup = condition if not cleanup_note else f"{condition} · {cleanup_note}"
    cleanup_label = f"{'cleanup':<9}"
    lines.append(
        f"{_ansi(GRAY, '└─')} {_ansi(MINT, cleanup_label)}"
        f"{_ansi(PURPLE, cleanup)}"
    )
    return "\n".join(lines)


def style_memory_tree(*, client: str, stores: int, files: int, scope: str) -> str:
    """Render memory inventory with distinct label, count, and scope roles."""

    def inventory_line(branch: str, label: str, value: str, color: str) -> str:
        return (
            f"{_ansi(GRAY, branch)} {_ansi(MINT, f'{label:<9}')}{_ansi(color, value)}"
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
            return f"{_ansi(AMBER, label)}[{_ansi(AMBER, str(value))}]"
        return f"{_ansi(GRAY, label)}[{_ansi(GRAY, str(value))}]"

    return (
        f"{normal('server processes', ps_servers)} "
        f"{_ansi(GRAY, '→')} "
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
        return _ansi(AMBER, "!")
    if status == "skip":
        return _ansi(GRAY, "-")
    if status == "info":
        return _ansi(GRAY, "·")
    return _ansi(GRAY, "o")  # pending


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _cycle_color(
    pos: int,
    pink: tuple[int, int, int],
    mid: tuple[int, int, int],
    purple: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Pink → mid → purple → mid → pink, indexed by cell position."""
    p = (pos % _GRADIENT_PERIOD) / _GRADIENT_PERIOD
    if p < 0.25:
        return _lerp_rgb(pink, mid, p / 0.25)
    if p < 0.5:
        return _lerp_rgb(mid, purple, (p - 0.25) / 0.25)
    if p < 0.75:
        return _lerp_rgb(purple, mid, (p - 0.5) / 0.25)
    return _lerp_rgb(mid, pink, (p - 0.75) / 0.25)


def _gradient_color(pos: int) -> tuple[int, int, int]:
    """Border/decoration gradient — AA-darkened hues for the white ground."""
    return _cycle_color(pos, _PINK_RGB, _MID_RGB, _PURPLE_RGB)


def _banner_gradient_color(pos: int) -> tuple[int, int, int]:
    """Banner glyph gradient — bright hues for the dark hero band."""
    return _cycle_color(
        pos, _BANNER_PINK_RGB, _BANNER_MID_RGB, _BANNER_PURPLE_RGB
    )


def _gradient_line(line: str, *, phase: int = 0) -> str:
    """Paint one art line cell-by-cell with the static gradient.

    ``phase`` offsets where in the cycle the line starts, letting callers
    drift consecutive rows into a diagonal sweep. Whitespace is left
    uncolored so the box clip area stays visually clean, and consecutive
    cells of the same color collapse into a single escape to keep the
    rendered byte count low.
    """
    out: list[str] = []
    last: tuple[int, int, int] | None = None
    colored = False
    for i, ch in enumerate(line):
        if ch == " ":
            out.append(" ")
            continue
        color = _gradient_color(i + phase)
        if color != last:
            out.append(f"\x1b[1;38;2;{color[0]};{color[1]};{color[2]}m")
            last = color
            colored = True
        out.append(ch)
    if colored:
        out.append("\x1b[0m")
    return "".join(out)


def _gradient_rule(width: int, *, phase: int, glyph: str = "─") -> str:
    """A horizontal border ribbon painted with the banner gradient."""
    return _gradient_line(glyph * width, phase=phase)


def _banner_bitmap(word: str) -> list[str]:
    gap = "." * _GLYPH_GAP
    return [
        gap.join(_BANNER_GLYPHS[letter][row] for letter in word)
        for row in range(12)
    ]


def _banner_pixels(
    word: str, band_width: int
) -> list[list[tuple[int, int, int]]]:
    """Color grid for the banner: hero band, gradient glyphs, drop shadow.

    Every pixel carries a color — the band fills the full ``band_width`` and
    pads above/below, the glyph bitmap (centered) carries the bright
    gradient, and the bitmap offset down-right casts the flat shadow.
    """
    bitmap = _banner_bitmap(word)
    height, width = len(bitmap), len(bitmap[0])
    total_h = height + _SHADOW_DROP_ROWS + 2 * _BAND_PAD_ROWS
    left = max(0, (band_width - width - _SHADOW_DROP_COLS) // 2)

    def lit(glyph_row: int, glyph_col: int) -> bool:
        return (
            0 <= glyph_row < height
            and 0 <= glyph_col < width
            and bitmap[glyph_row][glyph_col] == "#"
        )

    grid: list[list[tuple[int, int, int]]] = []
    for row in range(total_h):
        glyph_row = row - _BAND_PAD_ROWS
        line: list[tuple[int, int, int]] = []
        for col in range(band_width):
            glyph_col = col - left
            if lit(glyph_row, glyph_col):
                line.append(
                    _banner_gradient_color(
                        glyph_col + glyph_row * _HALF_ROW_DRIFT
                    )
                )
            elif lit(
                glyph_row - _SHADOW_DROP_ROWS, glyph_col - _SHADOW_DROP_COLS
            ):
                line.append(_BANNER_SHADOW_RGB)
            else:
                line.append(_star_at(col, row) or _band_color(col, row // 2))
        grid.append(line)
    return grid


def _banner_lines(word: str, band_width: int) -> list[str]:
    """Render a word as half-block pixel art on the hero band.

    Each terminal row carries two pixel rows: cells whose halves differ are
    drawn as ``▀`` with the upper pixel as foreground and the lower pixel as
    background; uniform cells are a space over the background color.
    """
    grid = _banner_pixels(word, band_width)
    lines: list[str] = []
    for row in range(len(grid) // 2):
        upper_row, lower_row = grid[2 * row], grid[2 * row + 1]
        parts: list[str] = []
        cur_fg: tuple[int, int, int] | None = None
        cur_bg: tuple[int, int, int] | None = None
        for col in range(band_width):
            up, lo = upper_row[col], lower_row[col]
            if up == lo:
                if cur_bg != up:
                    parts.append(f"\x1b[48;2;{up[0]};{up[1]};{up[2]}m")
                    cur_bg = up
                parts.append(" ")
                continue
            if cur_fg != up:
                parts.append(f"\x1b[38;2;{up[0]};{up[1]};{up[2]}m")
                cur_fg = up
            if cur_bg != lo:
                parts.append(f"\x1b[48;2;{lo[0]};{lo[1]};{lo[2]}m")
                cur_bg = lo
            parts.append("▀")
        parts.append("\x1b[0m")
        lines.append("".join(parts))
    return lines


def render_inline_row(
    label: str,
    value: str,
    *,
    status: ItemStatus,
    accent: str | None = None,
    spin_frame: int = 0,
) -> str:
    """Render one BoxModel-style row as a standalone line (no surrounding box).

    Used by the launcher to surface post-install state changes below the
    preflight overview. Redrawing the full box would flash the banner art
    again and push the original overview out of view; an inline row keeps
    the chronological flow intact and matches the row format inside the
    box so the visual style stays consistent.
    """
    marker = _marker_for(
        status,
        spin_frame=spin_frame,
        accent=accent or PURPLE,
    )
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
    half_cycle = _GRADIENT_PERIOD // 2
    lines: list[str] = []
    # Double border — the outline-offset look from concept 02, painted as
    # gradient ribbons phase-offset half a cycle. The outer line is heavy and
    # starts at the pink endpoint; the inner hairline echo starts at purple.
    # Hairlines alone read as washed out against a light background.
    lines.append("  " + _gradient_rule(box_width, phase=0, glyph="━"))
    lines.append("  " + _gradient_rule(box_width, phase=half_cycle))
    word = _BANNER_WORDS.get(model.title)
    if word is not None:
        lines.extend(
            "  " + banner_line for banner_line in _banner_lines(word, box_width)
        )
        # Right-align the phase label to the border edge.
        phase_label = _ansi(PINK, f"·  {model.phase}")
        pad = max(0, box_width - len(model.phase) - 3)
        lines.append("  " + " " * pad + phase_label)
    else:
        header = f"{model.title}  ·  {model.phase}"
        lines.append("  " + _ansi(f"1;{PINK}", header))
    for item in model.items:
        lines.extend(_render_item_lines(item, spin_frame=spin_frame))
    lines.append("  " + _gradient_rule(box_width, phase=half_cycle))
    lines.append("  " + _gradient_rule(box_width, phase=0, glyph="━"))
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
                stream.write(f"      \x1b[{GRAY}m{option.label}\x1b[0m\n")
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
