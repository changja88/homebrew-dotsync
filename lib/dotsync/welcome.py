"""Welcome banner shown by `dotsync welcome` and at the start of `dotsync init`."""

from __future__ import annotations
from dotsync import __version__, ui


# Six-line ANSI-Shadow ASCII logo: "DOTSYNC"
LOGO_LINES = [
    "  ██████╗  ██████╗ ████████╗███████╗██╗   ██╗███╗   ██╗ ██████╗",
    "  ██╔══██╗██╔═══██╗╚══██╔══╝██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝",
    "  ██║  ██║██║   ██║   ██║   ███████╗ ╚████╔╝ ██╔██╗ ██║██║     ",
    "  ██║  ██║██║   ██║   ██║   ╚════██║  ╚██╔╝  ██║╚██╗██║██║     ",
    "  ██████╔╝╚██████╔╝   ██║   ███████║   ██║   ██║ ╚████║╚██████╗",
    "  ╚═════╝  ╚═════╝    ╚═╝   ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝",
]
LOGO = "\n".join(LOGO_LINES)

SPARKLE_TOP = "  ❖  ✷                                                          ⋆  ✷"
SPARKLE_BOTTOM = "  ⋆              ✷                                              ❖"
TAGLINE_DECO = "∿∿∿"


def format_welcome(version: str = __version__) -> str:
    """Return the welcome banner as a single string (color-aware)."""

    def c(color: str, text: str) -> str:
        return ui._wrap(color, text)

    primary = ui.PRIMARY
    dim = ui.DIM_ANSI
    bold = ui.BOLD

    # Cell-by-cell pink ↔ purple gradient — same palette as the launcher TUI
    # banner so dotsync welcome and `serena_agent_launcher` look like one
    # visual family.
    gradient_logo = "\n".join(ui.gradient_line(line) for line in LOGO_LINES)

    rule = "─" * 40
    lines = [
        "",
        c(primary, SPARKLE_TOP),
        gradient_logo,
        c(primary, SPARKLE_BOTTOM),
        "",
        f"          {c(dim, TAGLINE_DECO)}  "
        f"sync your macOS configs {c(dim, '·')} one folder  "
        f"{c(dim, TAGLINE_DECO)}",
        c(dim, "   " + rule),
        f"   {c(dim, 'v' + version)}  {c(dim, '·')}  "
        f"{c(dim, 'brew install changja88/dotsync/dotsync')}",
        "",
        f"   {c(primary, '▶')}  {c(bold, 'Quickstart')} {c(dim, '— in this order:')}",
        "",
        f"       {c(bold, 'dotsync init')}            "
        f"{c(dim, '# start here: pick a sync folder + auto-detect apps')}",
        f"       {c(bold, 'dotsync from --all')}      "
        f"{c(dim, '# local apps → folder')}",
        f"       {c(bold, 'dotsync to --all')}        "
        f"{c(dim, '# folder → local apps  (with backups)')}",
        "",
        f"   {c(dim, 'See `dotsync --help` for all commands.')}",
        "",
    ]
    return "\n".join(lines)


def print_welcome(version: str = __version__) -> None:
    print(format_welcome(version))
