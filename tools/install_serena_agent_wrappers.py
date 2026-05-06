"""Install Codex and Claude wrapper scripts for scoped Serena MCP."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def wrapper_text(
    client_type: str,
    launcher_path: Path,
    real_binary: str,
    python_executable: str,
) -> str:
    """Return a POSIX shell wrapper script."""

    env_name = f"SERENA_REAL_{client_type.upper()}"
    return (
        "#!/bin/sh\n"
        f"export SERENA_AGENT_CLIENT={client_type}\n"
        f"export {env_name}={real_binary}\n"
        f'exec "{python_executable}" "{launcher_path}" "$@"\n'
    )


def install_wrappers(
    *,
    bin_dir: Path,
    launcher_path: Path,
    real_binaries: dict[str, str],
    python_executable: str | None = None,
) -> None:
    """Install wrapper scripts into a bin directory."""

    python_executable = python_executable or sys.executable
    bin_dir.mkdir(parents=True, exist_ok=True)
    for client_type in ("codex", "claude"):
        path = bin_dir / client_type
        path.write_text(
            wrapper_text(
                client_type,
                launcher_path.resolve(),
                real_binaries[client_type],
                python_executable,
            )
        )
        path.chmod(path.stat().st_mode | 0o755)


def main() -> int:
    """Install wrappers into the selected bin directory."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-dir", default="~/.local/bin")
    args = parser.parse_args()
    launcher = Path(__file__).resolve().parent / "serena_agent_launcher.py"
    real_binaries = {
        name: shutil.which(name) or f"/opt/homebrew/bin/{name}"
        for name in ("codex", "claude")
    }
    install_wrappers(
        bin_dir=Path(args.bin_dir).expanduser(),
        launcher_path=launcher,
        real_binaries=real_binaries,
    )
    print(f"installed codex/claude wrappers into {Path(args.bin_dir).expanduser()}")
    print("ensure this directory appears before /opt/homebrew/bin in PATH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
