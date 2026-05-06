from pathlib import Path


def test_homebrew_formula_does_not_install_local_dev_tools():
    formula = Path("Formula/dotsync.rb").read_text()

    assert 'libexec.install "local_dev"' not in formula
    assert 'libexec.install "serena_mcp_management"' not in formula
