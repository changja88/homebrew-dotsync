from pathlib import Path


def test_homebrew_formula_installs_serena_agent_tools():
    formula = Path("Formula/dotsync.rb").read_text()

    assert 'libexec.install "tools"' in formula
