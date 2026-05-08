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

# Tailwind violet 300→800 truecolor gradient, top→bottom across the six logo rows.
LOGO_GRADIENT = [
    "\033[38;2;196;181;253m",  # violet-300
    "\033[38;2;167;139;250m",  # violet-400
    "\033[38;2;139;92;246m",   # violet-500
    "\033[38;2;124;58;237m",   # violet-600
    "\033[38;2;109;40;217m",   # violet-700
    "\033[38;2;91;33;182m",    # violet-800
]

SPARKLE_TOP =    "  ❖  ✷                                                          ⋆  ✷"
SPARKLE_BOTTOM = "  ⋆              ✷                                              ❖"
TAGLINE_DECO = "∿∿∿"


def format_welcome(version: str = __version__) -> str:
    """Return the welcome banner as a single string (color-aware)."""
    def c(color: str, text: str) -> str:
        return ui._wrap(color, text)

    primary = ui.PRIMARY
    dim = ui.DIM_ANSI
    bold = ui.BOLD

    gradient_logo = "\n".join(
        c(LOGO_GRADIENT[i], line) for i, line in enumerate(LOGO_LINES)
    )

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
        f"   {c(primary, '▶')}  {c(bold, 'Quickstart')} "
        f"{c(dim, '— in this order:')}",
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
