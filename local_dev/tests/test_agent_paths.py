from pathlib import Path

import pytest

from local_dev.serena_mcp_management.agent_paths import (
    canonical_codex_homes,
    effective_claude_config_dir,
    paths_refer_to_same_file,
)


def test_canonical_codex_homes_deduplicates_default_active_and_orca(tmp_path):
    home = tmp_path / "home"
    default = home / ".codex"
    orca = home / "Library/Application Support/orca/codex-runtime-home/home"

    homes, default_home, orca_home = canonical_codex_homes(
        home=home,
        codex_home=default,
        orca_codex_home=orca,
    )

    assert homes == (default.resolve(), orca.resolve())
    assert default_home == default.resolve()
    assert orca_home == orca.resolve()


def test_effective_claude_config_dir_requires_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="claude_config_dir must be absolute"):
        effective_claude_config_dir(
            home=tmp_path,
            claude_config_dir=Path("relative"),
        )


def test_path_identity_uses_samefile_for_existing_aliases(tmp_path, monkeypatch):
    first = tmp_path / "UserHome"
    second = tmp_path / "userhome"
    first.mkdir()
    calls = []

    def fake_samefile(path, other):
        calls.append((path, other))
        return True

    monkeypatch.setattr(Path, "samefile", fake_samefile)

    assert paths_refer_to_same_file(first, second)
    assert calls == [(first, second)]


def test_path_identity_uses_case_sensitive_lexical_fallback_for_missing_paths(
    tmp_path,
):
    missing = tmp_path / "MissingHome"

    assert paths_refer_to_same_file(missing, missing)
    assert not paths_refer_to_same_file(
        missing,
        tmp_path / "missinghome",
    )
