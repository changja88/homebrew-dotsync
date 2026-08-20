from local_dev.serena_mcp_management import graphify_version


def test_installed_version_runs_resolved_cli(tmp_path):
    """버전 탐지는 launcher가 해석한 실제 executable의 출력을 읽는다."""
    executable = tmp_path / "graphify"
    executable.write_text("#!/bin/sh\nprintf 'graphify 0.9.44\\n'\n")
    executable.chmod(0o755)

    assert graphify_version.installed_version([str(executable)]) == "0.9.44"


def test_installed_version_ignores_official_skill_warning_on_stderr(tmp_path):
    executable = tmp_path / "graphify"
    executable.write_text(
        "#!/bin/sh\n"
        "printf 'warning: skill is from graphify 0.9.44, package is 0.9.47\\n' >&2\n"
        "printf 'graphify 0.9.47\\n'\n"
    )
    executable.chmod(0o755)

    assert graphify_version.installed_version([str(executable)]) == "0.9.47"


def test_version_key_compares_numeric_segments():
    assert graphify_version.version_key("0.9.10") > graphify_version.version_key(
        "0.9.9"
    )


def test_version_key_rejects_oversized_numeric_component():
    malformed = "0." + "9" * 5000

    assert graphify_version.version_key(malformed) is None


def test_latest_version_uses_day_cache(tmp_path):
    """같은 날의 launcher 실행은 PyPI 최신 버전을 한 번만 조회한다."""
    cache_path = tmp_path / "graphify-version.json"
    fetched = []

    def fetch_version():
        fetched.append(True)
        return "0.9.47"

    def fail_fetch():
        raise AssertionError("fresh cache must avoid PyPI")

    first = graphify_version.latest_version(
        cache_path=cache_path,
        now=100.0,
        fetch_version=fetch_version,
    )
    second = graphify_version.latest_version(
        cache_path=cache_path,
        now=100.0 + 23 * 60 * 60,
        fetch_version=fail_fetch,
    )

    assert first == "0.9.47"
    assert second == "0.9.47"
    assert fetched == [True]


def test_latest_version_caches_offline_result_without_blocking(tmp_path):
    cache_path = tmp_path / "graphify-version.json"
    fetched = []

    def offline():
        fetched.append(True)
        raise OSError("offline")

    first = graphify_version.latest_version(
        cache_path=cache_path,
        now=100.0,
        fetch_version=offline,
    )
    second = graphify_version.latest_version(
        cache_path=cache_path,
        now=200.0,
        fetch_version=offline,
    )

    assert first is None
    assert second is None
    assert fetched == [True]


def test_latest_version_ignores_unresolvable_xdg_cache_home(monkeypatch):
    monkeypatch.setenv(
        "XDG_CACHE_HOME", "~dotsync-user-that-does-not-exist/cache"
    )

    latest = graphify_version.latest_version(fetch_version=lambda: "0.9.47")

    assert latest == "0.9.47"


def test_latest_version_ignores_relative_xdg_when_home_is_unavailable(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/cache")

    def unavailable_home(cls):
        raise RuntimeError("home directory is unavailable")

    monkeypatch.setattr(
        graphify_version.Path, "home", classmethod(unavailable_home)
    )

    latest = graphify_version.latest_version(fetch_version=lambda: "0.9.47")

    assert latest == "0.9.47"


def test_latest_version_ignores_unexpected_cache_write_failure(monkeypatch, tmp_path):
    def fail_write(*args, **kwargs):
        raise RuntimeError("cache backend failed")

    monkeypatch.setattr(graphify_version, "_write_cache", fail_write)

    latest = graphify_version.latest_version(
        cache_path=tmp_path / "graphify-version.json",
        fetch_version=lambda: "0.9.47",
    )

    assert latest == "0.9.47"
