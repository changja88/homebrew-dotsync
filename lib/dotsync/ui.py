"""ANSI-colored output helpers. Honors NO_COLOR env var (https://no-color.org)."""
import os
import sys

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False


def _wrap(color: str, text: str) -> str:
    if _color_enabled():
        return f"{color}{text}{RESET}"
    return text


def format_step(msg: str) -> str:
    return f"{_wrap(CYAN, '▶')} {msg}"


def format_sub(msg: str) -> str:
    return f"  {_wrap(YELLOW, '↳')} {msg}"


def format_ok(msg: str) -> str:
    return f"  {_wrap(GREEN, '✓')} {msg}"


def format_warn(msg: str) -> str:
    return f"  {_wrap(YELLOW, '⚠')} {msg}"


def format_error(msg: str) -> str:
    return f"  {_wrap(RED, '✗')} {msg}"


def format_done(msg: str) -> str:
    return f"{_wrap(GREEN, '✔')} {msg}"


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
