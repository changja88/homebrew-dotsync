from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.render_cask import render_cask


VALID_VERSION = "0.3.0"
VALID_SHA256 = "a" * 64
VALID_URL = (
    "https://github.com/changja88/homebrew-dotsync/releases/download/"
    "v0.3.0/DotSync-0.3.0-macOS.zip"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release_macos_app.sh"
RENDERER_SCRIPT = REPO_ROOT / "scripts" / "render_cask.py"
CASK_TEMPLATE = REPO_ROOT / "packaging" / "dotsync-app.rb.in"
REPOSITORY_SLUG = "changja88/homebrew-dotsync"
TEST_IDENTITY = "Developer ID Application: Release Test (TEAMID1234)"
TEST_NOTARY_PROFILE = "dotsync-release-test"
FINAL_ARCHIVE_BYTES = b"final-stapled-dotsync-archive\n"
FINAL_ARCHIVE_SHA256 = hashlib.sha256(FINAL_ARCHIVE_BYTES).hexdigest()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "Casks").mkdir(parents=True)
    (repository / "packaging").mkdir()
    (repository / "packaging" / "dotsync-app.rb.in").write_bytes(
        (
            Path(__file__).resolve().parent.parent
            / "packaging"
            / "dotsync-app.rb.in"
        ).read_bytes()
    )
    return repository


def _render(repository: Path, **overrides: object) -> Path:
    output = repository / "Casks" / "dotsync-app.rb"
    arguments: dict[str, object] = {
        "version": VALID_VERSION,
        "sha256": VALID_SHA256,
        "url": VALID_URL,
        "output": output,
        "repository_root": repository,
    }
    arguments.update(overrides)
    render_cask(**arguments)
    return output


def test_renderer_writes_exact_formula_dependent_cask_from_release_inputs(tmp_path):
    repository = _repository(tmp_path)

    output = _render(repository)

    assert output.read_text(encoding="utf-8") == (
        'cask "dotsync-app" do\n'
        '  version "0.3.0"\n'
        f'  sha256 "{VALID_SHA256}"\n'
        "\n"
        f'  url "{VALID_URL}"\n'
        '  name "DotSync"\n'
        '  desc "Menu bar companion for DotSync config sync and Codex subscription usage"\n'
        '  homepage "https://github.com/changja88/homebrew-dotsync"\n'
        "\n"
        '  depends_on macos: ">= :ventura"\n'
        '  depends_on formula: "changja88/dotsync/dotsync"\n'
        "\n"
        '  app "DotSync.app"\n'
        "end\n"
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert "__DOTSYNC_" not in output.read_text(encoding="utf-8")
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize(
    "version",
    [
        "0.0.0",
        "0.3",
        "0.3.0.0",
        "00.3.0",
        "0.03.0",
        "0.3.00",
        "+0.3.0",
        "v0.3.0",
        "1.2.-3",
        "1.2.3\n",
    ],
)
def test_renderer_rejects_noncanonical_or_zero_version(version, tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="version"):
        _render(repository, version=version)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.parametrize(
    "sha256",
    [
        "0" * 64,
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "not-a-sha",
        "a" * 63 + "\n",
    ],
)
def test_renderer_rejects_noncanonical_or_zero_sha256(sha256, tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="SHA-256"):
        _render(repository, sha256=sha256)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/DotSync-0.3.0-macOS.zip",
        (
            "http://github.com/changja88/homebrew-dotsync/releases/download/"
            "v0.3.0/DotSync-0.3.0-macOS.zip"
        ),
        (
            "https://github.com/changja88/homebrew-dotsync/releases/download/"
            "v0.3.1/DotSync-0.3.0-macOS.zip"
        ),
        (
            "https://github.com/changja88/homebrew-dotsync/releases/download/"
            "v0.3.0/DotSync-0.3.1-macOS.zip"
        ),
        f"{VALID_URL}?download=1",
        f"{VALID_URL}#asset",
        f"{VALID_URL}\n",
    ],
)
def test_renderer_rejects_every_nonexact_release_asset_url(url, tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="URL"):
        _render(repository, url=url)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.parametrize(
    "relative_output",
    [
        "dotsync-app.rb",
        "Casks/other.rb",
        "nested/Casks/dotsync-app.rb",
        "Casks/subdirectory/dotsync-app.rb",
        "../Casks/dotsync-app.rb",
    ],
)
def test_renderer_rejects_output_other_than_exact_cask_path(
    relative_output, tmp_path
):
    repository = _repository(tmp_path)
    output = repository / relative_output

    with pytest.raises(ValueError, match="output"):
        _render(repository, output=output)

    assert not (repository / "Casks" / "dotsync-app.rb").exists()


def test_renderer_rejects_symlinked_casks_directory_without_touching_target(tmp_path):
    repository = _repository(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (repository / "Casks").rmdir()
    (repository / "Casks").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="Casks"):
        _render(repository)

    assert list(external.iterdir()) == []


def test_renderer_refuses_existing_cask_and_preserves_its_bytes(tmp_path):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    output.write_bytes(b"existing-cask\n")

    with pytest.raises(FileExistsError):
        _render(repository)

    assert output.read_bytes() == b"existing-cask\n"
    assert list(output.parent.iterdir()) == [output]


def test_renderer_replaces_existing_cask_only_when_explicitly_requested(tmp_path):
    repository = _repository(tmp_path)
    output = repository / "Casks" / "dotsync-app.rb"
    output.write_bytes(b"existing-cask\n")

    rendered = _render(repository, replace_existing=True)

    assert rendered == output
    assert output.read_text(encoding="utf-8").startswith(
        'cask "dotsync-app" do\n  version "0.3.0"\n'
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert list(output.parent.iterdir()) == [output]


def test_renderer_atomic_publish_failure_leaves_no_output_or_temporary_file(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected atomic publication failure")

    monkeypatch.setattr(os, "link", fail_publish)

    with pytest.raises(OSError, match="atomic publication"):
        _render(repository)

    assert list((repository / "Casks").iterdir()) == []


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _git(cwd: Path, real_git: str, *arguments: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [real_git, *arguments],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _shim_program(*, real_git: str, real_tar: str, real_python: str) -> str:
    return f"""#!{real_python}
import hashlib
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
arguments = sys.argv[1:]
call = [name, *arguments]
log_path = Path(os.environ["DOTSYNC_RELEASE_CALL_LOG"])
prior_calls = []
if log_path.exists():
    prior_calls = [json.loads(line) for line in log_path.read_text().splitlines()]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\\n")

failure = json.loads(os.environ.get("DOTSYNC_RELEASE_FAIL_PREFIX", "[]"))
failure_occurrence = int(os.environ.get("DOTSYNC_RELEASE_FAIL_OCCURRENCE", "1"))
matching_occurrence = 1 + sum(
    prior[:len(failure)] == failure for prior in prior_calls
)
if failure and call[:len(failure)] == failure and matching_occurrence == failure_occurrence:
    raise SystemExit(91)

if name == "git":
    if arguments == ["rev-parse", "--path-format=absolute", "--git-common-dir"] and os.environ.get("DOTSYNC_RELEASE_LINKED"):
        print(os.environ["DOTSYNC_RELEASE_FAKE_COMMON_DIR"])
        raise SystemExit(0)
    if arguments == ["branch", "--show-current"] and os.environ.get("DOTSYNC_RELEASE_BRANCH"):
        print(os.environ["DOTSYNC_RELEASE_BRANCH"])
        raise SystemExit(0)
    os.execv({real_git!r}, [{real_git!r}, *arguments])

if name == "tar":
    os.execv({real_tar!r}, [{real_tar!r}, *arguments])

if name == "bash":
    os.execv("/bin/bash", ["/bin/bash", *arguments])

if name == "python-release":
    if len(arguments) >= 1 and arguments[0].endswith("scripts/render_cask.py"):
        os.execv({real_python!r}, [{real_python!r}, *arguments])
    raise SystemExit(0)

if name == "security":
    identity = os.environ["DEVELOPER_ID_APPLICATION"]
    if not os.environ.get("DOTSYNC_RELEASE_IDENTITY_UNRESOLVABLE"):
        print(f'  1) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "{{identity}}"')
    raise SystemExit(0)

if name == "ditto":
    destination = Path(arguments[-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.name.startswith("DotSync-notarization-"):
        destination.write_bytes(b"unsigned-notary-archive\\n")
    else:
        destination.write_bytes({FINAL_ARCHIVE_BYTES!r})
    raise SystemExit(0)

if name == "shasum":
    archive = Path(arguments[-1])
    print(f"{{hashlib.sha256(archive.read_bytes()).hexdigest()}}  {{archive}}")
    raise SystemExit(0)

raise SystemExit(0)
"""


@pytest.fixture
def macos_release_repository(tmp_path):
    real_git = shutil.which("git")
    real_tar = shutil.which("tar")
    assert real_git is not None
    assert real_tar is not None

    repository = tmp_path / "release-repository"
    (repository / "scripts").mkdir(parents=True)
    (repository / "packaging").mkdir()
    (repository / "macos" / "DotSyncApp").mkdir(parents=True)
    (repository / "tests" / "web" / "js").mkdir(parents=True)
    shutil.copy2(RELEASE_SCRIPT, repository / "scripts" / RELEASE_SCRIPT.name)
    shutil.copy2(RENDERER_SCRIPT, repository / "scripts" / RENDERER_SCRIPT.name)
    shutil.copy2(CASK_TEMPLATE, repository / "packaging" / CASK_TEMPLATE.name)
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "dotsync"\nversion = "0.3.0"\n',
        encoding="utf-8",
    )
    (repository / "macos" / "DotSyncApp" / "Package.swift").write_text(
        "// release fixture\n",
        encoding="utf-8",
    )
    for test_name in ("state.test.mjs", "api-client.test.mjs"):
        (repository / "tests" / "web" / "js" / test_name).write_text(
            "// release fixture\n",
            encoding="utf-8",
        )
    (repository / ".gitignore").write_text("build/\nCasks/\n", encoding="utf-8")
    _write_executable(
        repository / "scripts" / "build_macos_app.sh",
        """#!/usr/bin/env bash
set -euo pipefail
mkdir -p build/DotSync.app/Contents/MacOS
printf '%s\\n' 'fixture-universal-binary' > build/DotSync.app/Contents/MacOS/DotSync
chmod 755 build/DotSync.app/Contents/MacOS/DotSync
""",
    )

    git_env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
    }
    _git(tmp_path, real_git, "init", "-b", "main", str(repository), env=git_env)
    _git(repository, real_git, "add", "-A", env=git_env)
    _git(repository, real_git, "commit", "-m", "release fixture", env=git_env)
    _git(repository, real_git, "tag", "-a", "v0.3.0", "-m", "v0.3.0", env=git_env)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    dispatcher = fake_bin / "dispatcher"
    _write_executable(
        dispatcher,
        _shim_program(
            real_git=real_git,
            real_tar=real_tar,
            real_python=sys.executable,
        ),
    )
    shim_names = (
        "git",
        "tar",
        "python-release",
        "node",
        "swift",
        "bash",
        "security",
        "lipo",
        "codesign",
        "ditto",
        "xcrun",
        "spctl",
        "shasum",
        "gh",
        "brew",
    )
    for name in shim_names:
        (fake_bin / name).symlink_to(dispatcher)

    call_log = tmp_path / "release-calls.jsonl"
    env = {
        **git_env,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PYTHON": str(fake_bin / "python-release"),
        "DEVELOPER_ID_APPLICATION": TEST_IDENTITY,
        "NOTARYTOOL_PROFILE": TEST_NOTARY_PROFILE,
        "DOTSYNC_RELEASE_CALL_LOG": str(call_log),
        "DOTSYNC_RELEASE_FAKE_COMMON_DIR": str(tmp_path / "other-common.git"),
    }
    return {
        "repository": repository,
        "fake_bin": fake_bin,
        "call_log": call_log,
        "env": env,
    }


def _run_macos_release(
    fixture: dict[str, object],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    repository = fixture["repository"]
    env = fixture["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    return subprocess.run(
        ["/bin/bash", "scripts/release_macos_app.sh", *arguments],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
    )


def _release_calls(fixture: dict[str, object]) -> list[list[str]]:
    call_log = fixture["call_log"]
    assert isinstance(call_log, Path)
    if not call_log.exists():
        return []
    return [json.loads(line) for line in call_log.read_text().splitlines()]


def _normalized_release_calls(fixture: dict[str, object]) -> list[list[str]]:
    repository = fixture["repository"]
    fake_bin = fixture["fake_bin"]
    assert isinstance(repository, Path)
    assert isinstance(fake_bin, Path)
    calls = _release_calls(fixture)
    work_directory: str | None = None
    for call in calls:
        if call[:3] == ["git", "archive", "--format=tar"]:
            output_index = call.index("--output") + 1
            work_directory = str(Path(call[output_index]).parent)
            break

    replacements = [
        (str(fake_bin / "python-release"), "$PYTHON"),
        (str(repository), "$REPOSITORY"),
    ]
    if work_directory is not None:
        replacements.append((work_directory, "$WORK"))
    normalized: list[list[str]] = []
    for call in calls:
        normalized.append(
            [
                next(
                    (
                        value.replace(original, replacement)
                        for original, replacement in replacements
                        if original in value
                    ),
                    value,
                )
                for value in call
            ]
        )
    return normalized


def _expected_success_calls() -> list[list[str]]:
    app = "$WORK/source/build/DotSync.app"
    notary_zip = "$WORK/DotSync-notarization-0.3.0.zip"
    final_zip = "$WORK/DotSync-0.3.0-macOS.zip"
    cask = "$REPOSITORY/Casks/dotsync-app.rb"
    return [
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "rev-parse", "--path-format=absolute", "--git-dir"],
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        ["git", "branch", "--show-current"],
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        ["git", "rev-parse", "HEAD"],
        ["git", "rev-parse", "--verify", "refs/tags/v0.3.0^{commit}"],
        [
            "git",
            "archive",
            "--format=tar",
            "--prefix=source/",
            "--output",
            "$WORK/source.tar",
            "refs/tags/v0.3.0",
        ],
        ["tar", "-xf", "$WORK/source.tar", "-C", "$WORK"],
        ["python-release", "-m", "pytest"],
        [
            "node",
            "--test",
            "tests/web/js/state.test.mjs",
            "tests/web/js/api-client.test.mjs",
        ],
        ["swift", "test", "--package-path", "macos/DotSyncApp"],
        ["python-release", "-m", "dotsync", "ui", "--check"],
        ["bash", "scripts/build_macos_app.sh"],
        [
            "lipo",
            f"{app}/Contents/MacOS/DotSync",
            "-verify_arch",
            "arm64",
            "x86_64",
        ],
        ["security", "find-identity", "-v", "-p", "codesigning"],
        [
            "xcrun",
            "notarytool",
            "history",
            "--keychain-profile",
            TEST_NOTARY_PROFILE,
        ],
        [
            "codesign",
            "--force",
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            TEST_IDENTITY,
            app,
        ],
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", app],
        ["ditto", "-c", "-k", "--keepParent", app, notary_zip],
        [
            "xcrun",
            "notarytool",
            "submit",
            notary_zip,
            "--keychain-profile",
            TEST_NOTARY_PROFILE,
            "--wait",
        ],
        ["xcrun", "stapler", "staple", app],
        ["xcrun", "stapler", "validate", app],
        ["spctl", "--assess", "--type", "execute", "--verbose=4", app],
        ["ditto", "-c", "-k", "--keepParent", app, final_zip],
        ["shasum", "-a", "256", final_zip],
        ["gh", "release", "view", "v0.3.0", "--repo", REPOSITORY_SLUG],
        [
            "gh",
            "release",
            "upload",
            "v0.3.0",
            final_zip,
            "--repo",
            REPOSITORY_SLUG,
        ],
        [
            "python-release",
            "$REPOSITORY/scripts/render_cask.py",
            "--version",
            "0.3.0",
            "--sha256",
            FINAL_ARCHIVE_SHA256,
            "--url",
            VALID_URL,
            "--output",
            cask,
            "--repository-root",
            "$REPOSITORY",
        ],
        ["brew", "audit", "--cask", "--strict", cask],
    ]


@pytest.mark.no_subprocess_block
def test_signed_macos_release_runs_every_gate_once_in_exact_order_then_stops(
    macos_release_repository,
):
    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _normalized_release_calls(macos_release_repository) == _expected_success_calls()
    cask = repository / "Casks" / "dotsync-app.rb"
    assert cask.is_file()
    assert f'  sha256 "{FINAL_ARCHIVE_SHA256}"' in cask.read_text(encoding="utf-8")
    assert "explicit publication confirmation" in result.stdout
    assert not any(call[:2] == ["git", "push"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "arguments",
    [(), ("0.3.0", "unexpected"), ("0.0.0",), ("00.3.0",), ("v0.3.0",)],
)
def test_signed_macos_release_requires_one_canonical_nonzero_version(
    macos_release_repository, arguments
):
    result = _run_macos_release(macos_release_repository, *arguments)

    assert result.returncode != 0
    assert _release_calls(macos_release_repository) == []


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "environment_change",
    [
        {"DOTSYNC_RELEASE_LINKED": "1"},
        {"DOTSYNC_RELEASE_BRANCH": "feature"},
    ],
)
def test_signed_macos_release_rejects_non_primary_or_non_main_checkout_before_export(
    macos_release_repository, environment_change
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env.update(environment_change)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_dirty_checkout_before_export(
    macos_release_repository,
):
    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    (repository / "untracked-release-state").write_text("dirty", encoding="utf-8")

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
def test_signed_macos_release_requires_tag_to_resolve_to_exact_head(
    macos_release_repository,
):
    repository = macos_release_repository["repository"]
    env = macos_release_repository["env"]
    assert isinstance(repository, Path)
    assert isinstance(env, dict)
    real_git = shutil.which("git", path=os.environ["PATH"])
    assert real_git is not None
    (repository / "later").write_text("different HEAD", encoding="utf-8")
    _git(repository, real_git, "add", "later", env=env)
    _git(repository, real_git, "commit", "-m", "different head", env=env)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("trailing_slashes", ["/", "///"])
def test_signed_macos_release_rejects_symlinked_temporary_root_without_touching_target(
    macos_release_repository, tmp_path, trailing_slashes
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    external = tmp_path / "external-temporary-root"
    external.mkdir()
    temporary_link = tmp_path / "temporary-link"
    temporary_link.symlink_to(external, target_is_directory=True)
    env["TMPDIR"] = f"{temporary_link}{trailing_slashes}"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    assert list(external.iterdir()) == []
    assert not any(call[:2] == ["git", "archive"] for call in _release_calls(macos_release_repository))


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "variable",
    ["DEVELOPER_ID_APPLICATION", "NOTARYTOOL_PROFILE"],
)
def test_signed_macos_release_rejects_missing_or_blank_credentials_before_signing(
    macos_release_repository, variable
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env[variable] = "   "

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert not any(call[0] == "codesign" for call in calls)
    assert not any(call[:3] == ["gh", "release", "upload"] for call in calls)
    repository = macos_release_repository["repository"]
    assert isinstance(repository, Path)
    assert not (repository / "Casks" / "dotsync-app.rb").exists()


@pytest.mark.no_subprocess_block
def test_signed_macos_release_rejects_unresolvable_developer_identity(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_IDENTITY_UNRESOLVABLE"] = "1"

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert any(call[0] == "security" for call in calls)
    assert not any(call[0] == "codesign" for call in calls)


GATE_FAILURE_CASES = [
    (["git", "rev-parse", "--show-toplevel"], 1),
    (["git", "rev-parse", "--path-format=absolute", "--git-dir"], 1),
    (["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], 1),
    (["git", "branch", "--show-current"], 1),
    (["git", "status"], 1),
    (["git", "rev-parse", "HEAD"], 1),
    (["git", "rev-parse", "--verify"], 1),
    (["git", "archive"], 1),
    (["tar", "-xf"], 1),
    (["python-release", "-m", "pytest"], 1),
    (["node", "--test"], 1),
    (["swift", "test"], 1),
    (["python-release", "-m", "dotsync"], 1),
    (["bash", "scripts/build_macos_app.sh"], 1),
    (["lipo"], 1),
    (["security"], 1),
    (["xcrun", "notarytool", "history"], 1),
    (["codesign", "--force"], 1),
    (["codesign", "--verify"], 1),
    (["ditto", "-c"], 1),
    (["xcrun", "notarytool", "submit"], 1),
    (["xcrun", "stapler", "staple"], 1),
    (["xcrun", "stapler", "validate"], 1),
    (["spctl"], 1),
    (["ditto", "-c"], 2),
    (["shasum"], 1),
    (["gh", "release", "view"], 1),
    (["gh", "release", "upload"], 1),
    (["python-release", "RENDERER"], 1),
    (["brew", "audit"], 1),
]


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("failure_prefix,failure_occurrence", GATE_FAILURE_CASES)
def test_each_failed_release_gate_stops_without_cask_or_trailing_external_calls(
    macos_release_repository, failure_prefix, failure_occurrence
):
    env = macos_release_repository["env"]
    repository = macos_release_repository["repository"]
    assert isinstance(env, dict)
    assert isinstance(repository, Path)
    actual_prefix = list(failure_prefix)
    if actual_prefix == ["python-release", "RENDERER"]:
        actual_prefix = [
            "python-release",
            str(repository / "scripts" / "render_cask.py"),
        ]
    env["DOTSYNC_RELEASE_FAIL_PREFIX"] = json.dumps(actual_prefix)
    env["DOTSYNC_RELEASE_FAIL_OCCURRENCE"] = str(failure_occurrence)

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    failed_indexes = [
        index
        for index, call in enumerate(calls)
        if call[: len(actual_prefix)] == actual_prefix
    ]
    assert len(failed_indexes) == failure_occurrence
    assert failed_indexes[-1] == len(calls) - 1
    assert not (repository / "Casks" / "dotsync-app.rb").exists()
    if actual_prefix[:3] not in (
        ["gh", "release", "upload"],
        ["python-release", str(repository / "scripts" / "render_cask.py")],
    ) and actual_prefix[:2] != ["brew", "audit"]:
        assert not any(call[:3] == ["gh", "release", "upload"] for call in calls)


@pytest.mark.no_subprocess_block
def test_notary_profile_validation_failure_stops_before_sign_or_upload(
    macos_release_repository,
):
    env = macos_release_repository["env"]
    assert isinstance(env, dict)
    env["DOTSYNC_RELEASE_FAIL_PREFIX"] = json.dumps(
        ["xcrun", "notarytool", "history"]
    )

    result = _run_macos_release(macos_release_repository, VALID_VERSION)

    assert result.returncode != 0
    calls = _release_calls(macos_release_repository)
    assert not any(call[0] == "codesign" for call in calls)
    assert not any(call[:3] == ["gh", "release", "upload"] for call in calls)
