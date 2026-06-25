"""End-to-end tests for scripts/release.sh against a sandboxed git origin.

The release script's job is to publish a new version WITHOUT ever exposing
the placeholder sha256 on origin/main — brew reads the tap's main directly,
so a placeholder there breaks `brew install` for everyone (this is exactly
what happened to v0.1.19 when `gh release create` died mid-script).

These tests run the real script in a throwaway clone wired to a local bare
"origin", with `gh` / `curl` replaced by PATH stubs, and assert on the state
of origin after the run.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

FAKE_TARBALL = b"fake tarball bytes for release test"
FAKE_TARBALL_SHA = hashlib.sha256(FAKE_TARBALL).hexdigest()

PLACEHOLDER = "0" * 64
OLD_SHA = "a" * 64

PYPROJECT = 'version = "0.1.19"\n'
INIT_PY = '__version__ = "0.1.19"\n'
FORMULA = (
    "class Dotsync < Formula\n"
    '  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.19.tar.gz"\n'
    f'  sha256 "{OLD_SHA}"\n'
    "  test do\n"
    '    assert_match "dotsync 0.1.19", shell_output("#{bin}/dotsync --version")\n'
    "  end\n"
    "end\n"
)


def _git(cwd, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/bin/bash\n{body}\n")
    stub.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path):
    """A work clone + bare origin containing a minimal dotsync repo at
    v0.1.19, plus a stub-bin dir where tests drop fake gh/curl/python."""
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin), env=env)

    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work), env=env)
    (work / "pyproject.toml").write_text(PYPROJECT)
    (work / "lib" / "dotsync").mkdir(parents=True)
    (work / "lib" / "dotsync" / "__init__.py").write_text(INIT_PY)
    (work / "Formula").mkdir()
    (work / "Formula" / "dotsync.rb").write_text(FORMULA)
    (work / "scripts").mkdir()
    (work / "scripts" / "release.sh").write_bytes(
        (REPO_ROOT / "scripts" / "release.sh").read_bytes()
    )
    (work / "scripts" / "release.sh").chmod(0o755)
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-m", "v0.1.19 state", env=env)
    _git(work, "push", "origin", "main", env=env)

    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "fakepython", "exit 0")  # stands in for `pytest` run
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["PYTHON"] = str(bin_dir / "fakepython")
    # Keep download retries fast in tests; the script defaults are larger.
    env["RELEASE_CURL_RETRIES"] = "2"
    env["RELEASE_CURL_DELAY"] = "0"
    return {"work": work, "origin": origin, "bin": bin_dir, "env": env}


def _run_release(sandbox, *, choice="1\n"):
    return subprocess.run(
        ["bash", "scripts/release.sh"],
        cwd=sandbox["work"],
        env=sandbox["env"],
        input=choice,
        capture_output=True,
        text=True,
    )


def _origin_formula(sandbox) -> str:
    r = subprocess.run(
        ["git", "--git-dir", str(sandbox["origin"]), "show", "main:Formula/dotsync.rb"],
        capture_output=True,
        text=True,
        check=True,
        env=sandbox["env"],
    )
    return r.stdout


def _origin_has_tag(sandbox, tag: str) -> bool:
    r = subprocess.run(
        ["git", "--git-dir", str(sandbox["origin"]), "tag", "--list", tag],
        capture_output=True,
        text=True,
        check=True,
        env=sandbox["env"],
    )
    return tag in r.stdout.split()


@pytest.mark.no_subprocess_block
def test_release_completes_and_never_publishes_placeholder_when_gh_fails(sandbox):
    """gh being broken (not installed properly / not authenticated) must not
    leave the tap broken: the release should still complete with the real
    tarball sha on origin/main, and the placeholder must never be what
    origin/main serves. This is the v0.1.19 incident as a regression test."""
    _write_stub(sandbox["bin"], "gh", "exit 1")  # unauthenticated gh
    _write_stub(sandbox["bin"], "curl", f'printf "%s" "{FAKE_TARBALL.decode()}"')

    result = _run_release(sandbox)

    formula = _origin_formula(sandbox)
    assert PLACEHOLDER not in formula, (
        f"placeholder sha published to origin/main:\n{result.stdout}\n{result.stderr}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert FAKE_TARBALL_SHA in formula
    assert "v0.1.20" in formula  # url bumped
    assert _origin_has_tag(sandbox, "v0.1.20")


@pytest.mark.no_subprocess_block
def test_release_leaves_tap_intact_when_tarball_download_fails(sandbox):
    """If the tarball can't be fetched (network down, codeload hiccup), the
    script must abort WITHOUT having touched origin/main — the tap keeps
    serving the previous release."""
    _write_stub(sandbox["bin"], "gh", "exit 1")
    _write_stub(sandbox["bin"], "curl", "exit 22")  # curl -f style failure

    result = _run_release(sandbox)

    assert result.returncode != 0
    formula = _origin_formula(sandbox)
    assert OLD_SHA in formula, "origin/main must still serve the previous release"
    assert "v0.1.19" in formula
    assert PLACEHOLDER not in formula


@pytest.mark.no_subprocess_block
def test_release_preflights_pytest_before_version_mutation(sandbox):
    """A missing pytest runner should abort before version files and Formula
    are rewritten, otherwise a developer is left with a dirty placeholder SHA."""
    _write_stub(sandbox["bin"], "fakepython", "exit 1")
    _write_stub(sandbox["bin"], "gh", "exit 1")

    result = _run_release(sandbox)

    assert result.returncode != 0
    assert (sandbox["work"] / "pyproject.toml").read_text() == PYPROJECT
    assert (sandbox["work"] / "lib" / "dotsync" / "__init__.py").read_text() == INIT_PY
    formula = (sandbox["work"] / "Formula" / "dotsync.rb").read_text()
    assert OLD_SHA in formula
    assert PLACEHOLDER not in formula
    assert "v0.1.19" in formula
    assert not _origin_has_tag(sandbox, "v0.1.20")


@pytest.mark.no_subprocess_block
def test_release_falls_back_to_uv_pytest_when_default_venv_lacks_pytest(sandbox):
    """A fresh clone may have a uv-created .venv without pytest installed.
    The release script should still be able to run tests using uv's
    --with pytest overlay before mutating release files."""
    sandbox["env"].pop("PYTHON")
    venv_bin = sandbox["work"] / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write_stub(venv_bin, "python3", "exit 1")
    _write_stub(
        sandbox["bin"],
        "uv",
        (
            'if [[ "$*" == "run --with pytest python -m pytest --version" ]]; then\n'
            "  exit 0\n"
            'elif [[ "$*" == "run --with pytest python -m pytest -q" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            "exit 2"
        ),
    )
    _write_stub(sandbox["bin"], "gh", "exit 1")
    _write_stub(sandbox["bin"], "curl", f'printf "%s" "{FAKE_TARBALL.decode()}"')

    result = _run_release(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    formula = _origin_formula(sandbox)
    assert FAKE_TARBALL_SHA in formula
    assert _origin_has_tag(sandbox, "v0.1.20")


def test_formula_wraps_libexec_entrypoint_with_pythonpath():
    formula = (REPO_ROOT / "Formula" / "dotsync.rb").read_text()

    assert 'libexec.install "lib/dotsync"' in formula
    assert 'libexec.install "bin"' in formula
    assert 'inreplace libexec/"bin/dotsync"' in formula
    assert 'bin.env_script_all_files(libexec/"bin", PYTHONPATH: libexec)' in formula
    assert 'bin.install "bin/dotsync"' not in formula


@pytest.mark.no_subprocess_block
def test_formula_libexec_entrypoint_can_import_dotsync(tmp_path):
    libexec = tmp_path / "libexec"
    shutil.copytree(REPO_ROOT / "lib" / "dotsync", libexec / "dotsync")
    shutil.copytree(REPO_ROOT / "bin", libexec / "bin")
    entrypoint = libexec / "bin" / "dotsync"
    env = {**os.environ, "PYTHONPATH": str(libexec)}

    result = subprocess.run(
        [sys.executable, "-S", str(entrypoint), "--version"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "dotsync 0.1.21" in result.stdout
