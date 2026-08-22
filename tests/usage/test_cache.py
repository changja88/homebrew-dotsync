from __future__ import annotations

import json
import stat
import uuid

import pytest

from dotsync.app_paths import AppPaths
from dotsync.private_fs import UnsafePrivatePath, atomic_write_json
from dotsync.usage import UsageSnapshot, UsageWindow


def test_save_round_trips_validated_non_secret_snapshot(tmp_path):
    from dotsync.usage.cache import UsageCache

    paths = AppPaths(tmp_path / "DotSync")
    cache = UsageCache(paths)
    snapshot = _snapshot()

    cache.save(snapshot)

    assert cache.load(snapshot.account_id) == snapshot
    assert json.loads(_cache_file(paths, snapshot.account_id).read_text()) == {
        "account_id": snapshot.account_id,
        "provider": "codex",
        "windows": [
            {
                "name": "five_hour",
                "limit_id": "primary",
                "label": "Five hour",
                "used_percent": 42.0,
                "duration_minutes": 300,
                "resets_at": "2026-08-21T05:00:00Z",
            }
        ],
        "observed_at": "2026-08-21T00:00:00Z",
        "source": "codex_app_server",
        "provider_version": "1.2.3",
    }


def test_cache_contains_only_snapshot_fields_without_identity_path_or_auth(tmp_path):
    from dotsync.usage.cache import UsageCache

    paths = AppPaths(tmp_path / "DotSync")
    snapshot = _snapshot()
    UsageCache(paths).save(snapshot)

    payload = json.loads(_cache_file(paths, snapshot.account_id).read_text())

    assert set(payload) == {
        "account_id",
        "provider",
        "windows",
        "observed_at",
        "source",
        "provider_version",
    }
    assert set(payload["windows"][0]) == {
        "name",
        "limit_id",
        "label",
        "used_percent",
        "duration_minutes",
        "resets_at",
    }


def test_cache_file_and_directories_are_private(tmp_path):
    from dotsync.usage.cache import UsageCache

    paths = AppPaths(tmp_path / "DotSync")
    snapshot = _snapshot()

    UsageCache(paths).save(snapshot)

    cache_file = _cache_file(paths, snapshot.account_id)
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.usage.stat().st_mode) == 0o700


def test_missing_snapshot_returns_none_without_creating_cache_tree(tmp_path):
    from dotsync.usage.cache import UsageCache

    paths = AppPaths(tmp_path / "DotSync")

    assert UsageCache(paths).load(str(uuid.uuid4())) is None
    assert not paths.usage.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(profile_path="/Users/example/.codex"),
        lambda payload: payload.pop("provider_version"),
        lambda payload: payload["windows"][0].update(used_percent=True),
        lambda payload: payload.update(windows={}),
        lambda payload: payload.update(account_id=str(uuid.uuid4())),
    ],
)
def test_load_fails_closed_on_invalid_schema_or_account_correlation(
    tmp_path, mutate
):
    from dotsync.usage.cache import UsageCache, UsageCacheError

    paths = AppPaths(tmp_path / "DotSync")
    snapshot = _snapshot()
    payload = _payload(snapshot)
    mutate(payload)
    _write_cache(paths, snapshot.account_id, payload)

    with pytest.raises(UsageCacheError, match="cache"):
        UsageCache(paths).load(snapshot.account_id)

    assert json.loads(_cache_file(paths, snapshot.account_id).read_text()) == payload


def test_load_fails_closed_on_invalid_json_without_rewriting_it(tmp_path):
    from dotsync.usage.cache import UsageCache, UsageCacheError

    paths = AppPaths(tmp_path / "DotSync")
    account_id = str(uuid.uuid4())
    cache_file = _cache_file(paths, account_id)
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("{not-json", encoding="utf-8")

    with pytest.raises(UsageCacheError, match="cache") as captured:
        UsageCache(paths).load(account_id)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert cache_file.read_text(encoding="utf-8") == "{not-json"


def test_failed_atomic_save_preserves_last_successful_snapshot(tmp_path, monkeypatch):
    from dotsync.usage.cache import UsageCache
    import dotsync.usage.cache as cache_module

    paths = AppPaths(tmp_path / "DotSync")
    cache = UsageCache(paths)
    first = _snapshot(used_percent=42.0)
    cache.save(first)

    def interrupt_save(*args, **kwargs):
        raise OSError("interrupted write")

    monkeypatch.setattr(cache_module, "atomic_write_json", interrupt_save)

    with pytest.raises(OSError, match="interrupted write"):
        cache.save(_snapshot(account_id=first.account_id, used_percent=99.0))

    assert cache.load(first.account_id) == first


def test_delete_removes_only_the_validated_account_cache_tree(tmp_path):
    from dotsync.usage.cache import UsageCache

    paths = AppPaths(tmp_path / "DotSync")
    cache = UsageCache(paths)
    deleted = _snapshot()
    retained = _snapshot(account_id=str(uuid.uuid4()), used_percent=12.0)
    cache.save(deleted)
    cache.save(retained)

    cache.delete(deleted.account_id)

    assert cache.load(deleted.account_id) is None
    assert cache.load(retained.account_id) == retained


def test_delete_rejects_symlink_in_cache_tree_without_touching_target(tmp_path):
    from dotsync.usage.cache import UsageCache

    paths = AppPaths(tmp_path / "DotSync")
    account_id = str(uuid.uuid4())
    cache_root = _cache_file(paths, account_id).parent
    cache_root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    (cache_root / "link").symlink_to(outside)

    with pytest.raises(UnsafePrivatePath, match="symlink"):
        UsageCache(paths).delete(account_id)

    assert outside.read_text(encoding="utf-8") == "keep"
    assert cache_root.exists()


@pytest.mark.parametrize("account_id", ["../outside", "NOT-A-UUID", ""])
def test_cache_operations_reject_noncanonical_account_ids(tmp_path, account_id):
    from dotsync.usage.cache import UsageCache, UsageCacheError

    cache = UsageCache(AppPaths(tmp_path / "DotSync"))

    with pytest.raises(UsageCacheError, match="account id"):
        cache.load(account_id)
    with pytest.raises(UsageCacheError, match="account id"):
        cache.delete(account_id)


def _snapshot(
    *,
    account_id: str | None = None,
    used_percent: float = 42.0,
) -> UsageSnapshot:
    return UsageSnapshot(
        account_id=account_id or str(uuid.uuid4()),
        provider="codex",
        windows=(
            UsageWindow(
                name="five_hour",
                limit_id="primary",
                label="Five hour",
                used_percent=used_percent,
                duration_minutes=300,
                resets_at="2026-08-21T05:00:00Z",
            ),
        ),
        observed_at="2026-08-21T00:00:00Z",
        source="codex_app_server",
        provider_version="1.2.3",
    )


def _cache_file(paths: AppPaths, account_id: str):
    return paths.usage / account_id / "snapshot.json"


def _payload(snapshot: UsageSnapshot) -> dict[str, object]:
    return {
        "account_id": snapshot.account_id,
        "provider": snapshot.provider,
        "windows": [
            {
                "name": window.name,
                "limit_id": window.limit_id,
                "label": window.label,
                "used_percent": window.used_percent,
                "duration_minutes": window.duration_minutes,
                "resets_at": window.resets_at,
            }
            for window in snapshot.windows
        ],
        "observed_at": snapshot.observed_at,
        "source": snapshot.source,
        "provider_version": snapshot.provider_version,
    }


def _write_cache(paths: AppPaths, account_id: str, payload: object) -> None:
    atomic_write_json(_cache_file(paths, account_id), payload, root=paths.root)
