from pathlib import Path

from local_dev.serena_mcp_management.agent_paths import (
    canonical_codex_homes,
    paths_refer_to_same_file,
)


def test_canonical_codex_homes_deduplicates_default_and_active(tmp_path):
    home = tmp_path / "home"
    default = home / ".codex"
    active = home / "active-codex"

    homes, default_home = canonical_codex_homes(
        home=home,
        codex_home=active,
    )

    assert homes == (default.resolve(), active.resolve())
    assert default_home == default.resolve()


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
