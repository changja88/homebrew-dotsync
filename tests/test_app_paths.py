from __future__ import annotations

from uuid import uuid4

import pytest

from dotsync.app_paths import AppPaths, default_data_root


def test_default_data_root_uses_macos_application_support(tmp_path):
    paths = AppPaths.for_home(tmp_path)

    assert paths.root == tmp_path / "Library/Application Support/DotSync"
    assert paths.accounts == paths.root / "accounts"
    assert paths.usage == paths.root / "usage"


def test_default_data_root_uses_current_home(fake_home):
    assert default_data_root() == fake_home / "Library/Application Support/DotSync"


def test_account_root_rejects_non_uuid_identifier(tmp_path):
    paths = AppPaths.for_home(tmp_path)

    with pytest.raises(ValueError, match="account id"):
        paths.account_root("claude", "../../.claude")


def test_account_paths_stay_under_provider_account_root(tmp_path):
    paths = AppPaths.for_home(tmp_path)
    account_id = str(uuid4())
    root = paths.accounts / "codex" / account_id

    assert paths.account_root("codex", account_id) == root
    assert paths.account_home("codex", account_id) == root / "home"
    assert paths.account_probe("codex", account_id) == root / "probe"
    assert paths.account_tmp("codex", account_id) == root / "tmp"


def test_account_root_rejects_unknown_provider(tmp_path):
    with pytest.raises(ValueError, match="unsupported provider"):
        AppPaths.for_home(tmp_path).account_root("other", str(uuid4()))
