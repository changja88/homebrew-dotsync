"""Terminal output design system.

Tokens (ANSI escapes via stdlib only) + composable components.
Honors NO_COLOR (https://no-color.org).

Layout vocabulary:
  banner     — top rounded box framing a command run
  section    — per-app header with progress (▸ [n/N] name   description)
  step       — sub-header inside a section (▶ marketplace · plugin restore)
  ok / warn / error — bullets for individual results
  sub        — secondary line (  ↳ ...)
  dim        — muted info (  · ...)
  kv         — aligned key/value
  divider    — horizontal rule with optional label
  summary    — bottom rounded box: counts + duration
"""
import os
import sys

# --- color tokens -----------------------------------------------------------

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
PURPLE = "\033[38;2;167;139;250m"   # Tailwind violet-400 (truecolor)
PRIMARY = PURPLE                    # brand / heading / step bullets
CYAN = "\033[36m"                   # legacy; prefer PRIMARY for new code
DIM_ANSI = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# --- glyphs -----------------------------------------------------------------

GLYPH_STEP = "▸"           # section bullet
GLYPH_SUBSTEP = "▶"        # in-section sub-step
GLYPH_SUB = "↳"
GLYPH_OK = "✓"
GLYPH_WARN = "⚠"
GLYPH_ERROR = "✗"
GLYPH_DONE = "✔"
GLYPH_DIM = "·"
GLYPH_HORIZ = "─"
GLYPH_VERT = "│"
GLYPH_BOX_TL = "╭"
GLYPH_BOX_TR = "╮"
GLYPH_BOX_BL = "╰"
GLYPH_BOX_BR = "╯"

# --- internals --------------------------------------------------------------

BOX_WIDTH = 60   # inner width of banner / summary boxes


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False


def _wrap(color: str, text: str) -> str:
    if _color_enabled():
        return f"{color}{text}{RESET}"
    return text


def _box_line(content: str, width: int) -> str:
    """Inner line of a box: │ content_padded │"""
    # `content` may contain ANSI; pad based on visible length
    visible_len = _visible_len(content)
    pad = max(0, width - visible_len)
    return f"{_wrap(DIM_ANSI, GLYPH_VERT)} {content}{' ' * pad} {_wrap(DIM_ANSI, GLYPH_VERT)}"


def _visible_len(s: str) -> int:
    """Length of `s` ignoring ANSI escape sequences."""
    out = []
    in_esc = False
    for ch in s:
        if ch == "\033":
            in_esc = True
            continue
        if in_esc:
            if ch == "m":
                in_esc = False
            continue
        out.append(ch)
    return len("".join(out))


def _box_top(width: int) -> str:
    return _wrap(DIM_ANSI, f"{GLYPH_BOX_TL}{GLYPH_HORIZ * (width + 2)}{GLYPH_BOX_TR}")


def _box_bottom(width: int) -> str:
    return _wrap(DIM_ANSI, f"{GLYPH_BOX_BL}{GLYPH_HORIZ * (width + 2)}{GLYPH_BOX_BR}")


# --- format_* (return strings; testable) ------------------------------------

def format_step(msg: str) -> str:
    return f"{_wrap(PRIMARY, GLYPH_SUBSTEP)} {msg}"


def format_sub(msg: str) -> str:
    return f"  {_wrap(YELLOW, GLYPH_SUB)} {msg}"


def format_ok(msg: str) -> str:
    return f"  {_wrap(GREEN, GLYPH_OK)} {msg}"


def format_warn(msg: str) -> str:
    return f"  {_wrap(YELLOW, GLYPH_WARN)} {msg}"


def format_error(msg: str) -> str:
    return f"  {_wrap(RED, GLYPH_ERROR)} {msg}"


def format_done(msg: str) -> str:
    return f"{_wrap(GREEN, GLYPH_DONE)} {msg}"


def format_dim(msg: str) -> str:
    return f"  {_wrap(DIM_ANSI, GLYPH_DIM)} {_wrap(DIM_ANSI, msg)}"


def format_kv(key: str, value: str, key_width: int = 12) -> str:
    pad = max(0, key_width - len(key))
    return f"  {_wrap(DIM_ANSI, key)}{' ' * pad}  {value}"


def format_banner(title: str, subtitle: str = "") -> str:
    """A rounded top box framing the command being run."""
    width = BOX_WIDTH
    title_styled = _wrap(BOLD, title)
    lines = [_box_top(width), _box_line(title_styled, width)]
    if subtitle:
        lines.append(_box_line(_wrap(DIM_ANSI, subtitle), width))
    lines.append(_box_bottom(width))
    return "\n".join(lines)


def format_section(name: str, index: int = None, total: int = None, sub: str = "") -> str:
    """Per-app header. Examples:
        ▸ [1/4] claude               claude code
        ▸ ghostty
    """
    bullet = _wrap(PRIMARY, GLYPH_STEP)
    progress = ""
    if index is not None and total is not None:
        progress = _wrap(DIM_ANSI, f"[{index}/{total}] ")
    name_styled = _wrap(BOLD, name)
    line = f"{bullet} {progress}{name_styled}"
    if sub:
        # right-align (loose) the sub description
        visible = _visible_len(line)
        pad = max(2, 32 - visible)
        line = f"{line}{' ' * pad}{_wrap(DIM_ANSI, sub)}"
    return line


def format_divider(label: str = "") -> str:
    """Horizontal rule, with optional label in the middle."""
    if not label:
        return f"  {_wrap(DIM_ANSI, GLYPH_HORIZ * 40)}"
    bar = GLYPH_HORIZ * 4
    return f"  {_wrap(DIM_ANSI, bar)} {label} {_wrap(DIM_ANSI, GLYPH_HORIZ * 40)}"


def format_ask(question: str, default: str = "") -> str:
    """A high-visibility input prompt: `? question [default] › `

    Use for any line that requires user action — much more legible than a dim
    one-color prompt.
    """
    bullet = _wrap(PRIMARY, _wrap(BOLD, "?"))
    arrow = _wrap(PRIMARY, "›")
    if default:
        return f"{bullet} {question} {_wrap(DIM_ANSI, '[' + default + ']')} {arrow} "
    return f"{bullet} {question} {arrow} "


def ask(question: str, default: str = "") -> str:
    """Side-effect: render an accented prompt and return stripped user input."""
    return input(format_ask(question, default)).strip()


def format_summary(*, ok: int = 0, warn: int = 0, error: int = 0, duration_ms: int = 0) -> str:
    """Bottom rounded box with counts + elapsed time."""
    width = BOX_WIDTH
    duration = f"{duration_ms / 1000:.1f}s"
    parts = [
        f"{_wrap(GREEN, str(ok))} ok",
        f"{_wrap(YELLOW, str(warn))} warn",
        f"{_wrap(RED, str(error))} error",
        _wrap(DIM_ANSI, duration),
    ]
    sep = _wrap(DIM_ANSI, "  ·  ")
    body = sep.join(parts)
    return "\n".join([
        _box_top(width),
        _box_line(body, width),
        _box_bottom(width),
    ])


# --- side-effect printers (use in production code) -------------------------

def step(msg: str) -> None:
    print(format_step(msg))


def sub(msg: str) -> None:
    print(format_sub(msg))


def ok(msg: str) -> None:
    print(format_ok(msg))


def warn(msg: str) -> None:
    print(format_warn(msg))


def error(msg: str) -> None:
    print(format_error(msg), file=sys.stderr)


def done(msg: str) -> None:
    print(format_done(msg))


def dim(msg: str) -> None:
    print(format_dim(msg))


def kv(key: str, value: str, key_width: int = 12) -> None:
    print(format_kv(key, value, key_width))


def banner(title: str, subtitle: str = "") -> None:
    print(format_banner(title, subtitle))


def section(name: str, index: int = None, total: int = None, sub: str = "") -> None:
    print(format_section(name, index, total, sub))


def divider(label: str = "") -> None:
    print(format_divider(label))


def summary(*, ok: int = 0, warn: int = 0, error: int = 0, duration_ms: int = 0) -> None:
    print(format_summary(ok=ok, warn=warn, error=error, duration_ms=duration_ms))
