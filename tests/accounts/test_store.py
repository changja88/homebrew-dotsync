from __future__ import annotations

import json
import stat
import threading
import uuid

import pytest

from dotsync.app_paths import AppPaths
from dotsync.private_fs import atomic_write_json


def test_create_account_allocates_uuid_and_private_profile(tmp_path):
    from dotsync.accounts import AccountStore

    paths = AppPaths(tmp_path / "DotSync")
    store = AccountStore(paths)

    account = store.create(provider="claude", label="Personal")

    assert uuid.UUID(account.id)
    assert account.provider == "claude"
    assert account.label == "Personal"
    assert account.state == "logged_out"
    assert paths.account_home(account.provider, account.id).is_dir()
    assert stat.S_IMODE(paths.account_home(account.provider, account.id).stat().st_mode) == 0o700


def test_create_persists_only_the_non_secret_registry_model(tmp_path):
    from dotsync.accounts import AccountStore

    paths = AppPaths(tmp_path / "DotSync")
    account = AccountStore(paths).create(provider="codex", label=" Work ")

    assert json.loads((paths.root / "accounts.json").read_text()) == {
        "schema_version": 1,
        "accounts": [
            {
                "id": account.id,
                "provider": "codex",
                "label": "Work",
                "state": "logged_out",
                "identity": {
                    "display_name": None,
                    "email": None,
                    "plan": None,
                },
                "created_at": account.created_at,
            }
        ],
    }


def test_registry_rejects_duplicate_label_within_provider(tmp_path):
    from dotsync.accounts import AccountConflict, AccountStore

    store = AccountStore(AppPaths(tmp_path / "DotSync"))
    store.create(provider="codex", label="Work")

    with pytest.raises(AccountConflict, match="Work"):
        store.create(provider="codex", label="work")


@pytest.mark.parametrize("label", ["", "   ", "a" * 81, "Name\nwith break"])
def test_create_rejects_invalid_labels(tmp_path, label):
    from dotsync.accounts import AccountStore, AccountStoreError

    with pytest.raises(AccountStoreError, match="label"):
        AccountStore(AppPaths(tmp_path / "DotSync")).create(
            provider="claude", label=label
        )


def test_list_sorts_accounts_by_provider_then_creation_time(tmp_path):
    from dotsync.accounts import AccountStore

    store = AccountStore(AppPaths(tmp_path / "DotSync"))
    codex = store.create(provider="codex", label="Codex")
    claude_first = store.create(provider="claude", label="Claude first")
    claude_second = store.create(provider="claude", label="Claude second")

    assert store.list() == [claude_first, claude_second, codex]


def test_registry_rejects_malformed_provider_without_rewriting_it(tmp_path):
    from dotsync.accounts import AccountStore, AccountStoreError

    paths = AppPaths(tmp_path / "DotSync")
    malformed = {"schema_version": 1, "accounts": [_record(provider="other")]}
    _write_registry(paths, malformed)

    with pytest.raises(AccountStoreError, match="provider"):
        AccountStore(paths).list()

    assert _read_registry(paths) == malformed


def test_registry_rejects_noncanonical_uuid_without_rewriting_it(tmp_path):
    from dotsync.accounts import AccountStore, AccountStoreError

    paths = AppPaths(tmp_path / "DotSync")
    malformed = {"schema_version": 1, "accounts": [_record(id="not-a-uuid")]}
    _write_registry(paths, malformed)

    with pytest.raises(AccountStoreError, match="id"):
        AccountStore(paths).list()

    assert _read_registry(paths) == malformed


def test_registry_rejects_injected_path_field_without_rewriting_it(tmp_path):
    from dotsync.accounts import AccountStore, AccountStoreError

    paths = AppPaths(tmp_path / "DotSync")
    record = _record()
    record["profile_path"] = "/Users/example/.claude"
    malformed = {"schema_version": 1, "accounts": [record]}
    _write_registry(paths, malformed)

    with pytest.raises(AccountStoreError, match="schema"):
        AccountStore(paths).list()

    assert _read_registry(paths) == malformed


def test_concurrent_create_allows_only_one_duplicate_label(tmp_path):
    from dotsync.accounts import AccountConflict, AccountStore

    store = AccountStore(AppPaths(tmp_path / "DotSync"))
    barrier = threading.Barrier(2)
    created = []
    failures = []

    def create_duplicate() -> None:
        barrier.wait()
        try:
            created.append(store.create(provider="claude", label="Personal"))
        except AccountConflict as error:
            failures.append(error)

    threads = [threading.Thread(target=create_duplicate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert len(failures) == 1
    assert store.list() == created


def test_failed_atomic_save_preserves_last_valid_registry(tmp_path, monkeypatch):
    from dotsync.accounts import AccountStore
    import dotsync.accounts.store as store_module

    paths = AppPaths(tmp_path / "DotSync")
    store = AccountStore(paths)
    account = store.create(provider="claude", label="Personal")
    before = _read_registry(paths)

    def interrupt_save(*args, **kwargs):
        raise OSError("interrupted write")

    monkeypatch.setattr(store_module, "atomic_write_json", interrupt_save)

    with pytest.raises(OSError, match="interrupted write"):
        store.rename(account.id, "Renamed")

    assert _read_registry(paths) == before
    assert store.get(account.id).label == "Personal"


def test_mutators_replace_metadata_and_delete_only_the_record(tmp_path):
    from dotsync.accounts import AccountStore, ProviderIdentity

    store = AccountStore(AppPaths(tmp_path / "DotSync"))
    account = store.create(provider="claude", label="Personal")
    identity = ProviderIdentity(
        display_name="Ada Lovelace", email="ada@example.com", plan="Pro"
    )

    identified = store.set_identity(account.id, identity, "ready")
    renamed = store.rename(account.id, "Personal Claude")
    state_changed = store.set_state(account.id, "reauth_required")
    store.delete_metadata(account.id)

    assert identified.identity == identity
    assert identified.state == "ready"
    assert renamed.label == "Personal Claude"
    assert state_changed.state == "reauth_required"
    assert store.list() == []


def _write_registry(paths: AppPaths, payload: object) -> None:
    atomic_write_json(paths.root / "accounts.json", payload, root=paths.root)


def _read_registry(paths: AppPaths) -> object:
    return json.loads((paths.root / "accounts.json").read_text())


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "provider": "claude",
        "label": "Personal",
        "state": "logged_out",
        "identity": {"display_name": None, "email": None, "plan": None},
        "created_at": "2026-08-21T00:00:00+00:00",
    }
    record.update(overrides)
    return record
