"""Welcome banner shown by `dotsync welcome` and at the start of `dotsync init`."""
from __future__ import annotations
from dotsync import __version__, ui


# Six-line ANSI-Shadow ASCII logo: "DOTSYNC"
LOGO = "\n".join([
    "  ██████╗  ██████╗ ████████╗███████╗██╗   ██╗███╗   ██╗ ██████╗",
    "  ██╔══██╗██╔═══██╗╚══██╔══╝██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝",
    "  ██║  ██║██║   ██║   ██║   ███████╗ ╚████╔╝ ██╔██╗ ██║██║     ",
    "  ██║  ██║██║   ██║   ██║   ╚════██║  ╚██╔╝  ██║╚██╗██║██║     ",
    "  ██████╔╝╚██████╔╝   ██║   ███████║   ██║   ██║ ╚████║╚██████╗",
    "  ╚═════╝  ╚═════╝    ╚═╝   ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝",
])


def format_welcome(version: str = __version__) -> str:
    """Return the welcome banner as a single string (color-aware)."""
    def c(color: str, text: str) -> str:
        return ui._wrap(color, text)

    primary = ui.PRIMARY
    dim = ui.DIM_ANSI
    bold = ui.BOLD

    rule = "─" * 40
    lines = [
        "",
        c(primary, LOGO),
        "",
        f"   sync your macOS configs {c(dim, '·')} one folder",
        c(dim, "   " + rule),
        f"   {c(dim, 'v' + version)}  {c(dim, '·')}  "
        f"{c(dim, 'brew install changja88/dotsync/dotsync')}",
        "",
        f"   {c(primary, '▶')}  {c(bold, '`dotsync init`')} "
        f"{c(dim, 'is required first — it picks the sync folder.')}",
        "",
        f"       {c(bold, 'dotsync init')}            "
        f"{c(dim, '# pick a folder, auto-detect apps')}",
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
