"""Formula/dotsync.rb declares the Homebrew dependencies dotsync shells out to."""
import re
from pathlib import Path

FORMULA = Path(__file__).resolve().parent.parent / "Formula" / "dotsync.rb"


def test_formula_depends_on_zstd():
    # `sessions` reads .zst Codex rollouts with the `zstd` CLI.
    assert re.search(r'^\s*depends_on "zstd"\s*$', FORMULA.read_text(), re.M)
