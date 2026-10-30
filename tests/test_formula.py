"""Formula/dotsync.rb runs dotsync on the Python it needs."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMULA = ROOT / "Formula" / "dotsync.rb"


def test_formula_runs_dotsync_on_python_3_14():
    # `sessions` reads .zst Codex rollouts with `compression.zstd` (3.14+).
    formula = FORMULA.read_text()
    assert re.search(r'^\s*depends_on "python@3\.14"', formula, re.M)
    assert "python3.12" not in formula and "python3.13" not in formula
    assert 'requires-python = ">=3.14"' in (ROOT / "pyproject.toml").read_text()


def test_formula_needs_no_zstd_cli():
    assert not re.search(r'^\s*depends_on "zstd"', FORMULA.read_text(), re.M)
