from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_macos_app.sh"
PLIST_TEMPLATE = REPO_ROOT / "packaging" / "DotSync-Info.plist.in"


def _render_info_plist(*, version: str, build: str = "1") -> bytes:
    return (
        PLIST_TEMPLATE.read_text(encoding="utf-8")
        .replace("__DOTSYNC_VERSION__", version)
        .replace("__DOTSYNC_BUILD__", build)
        .encode()
    )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_build_project(tmp_path: Path, *, version: str = "0.3.0") -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "packaging").mkdir()
    (project / "macos" / "DotSyncApp").mkdir(parents=True)
    shutil.copy2(BUILD_SCRIPT, project / "scripts" / BUILD_SCRIPT.name)
    shutil.copy2(PLIST_TEMPLATE, project / "packaging" / PLIST_TEMPLATE.name)
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "dotsync"\nversion = "{version}"\n',
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.log"
    _write_executable(fake_bin / "xcrun", 'printf "%s\\n" "/fake/MacOSX.sdk"')
    _write_executable(
        fake_bin / "swift",
        r'''
printf 'swift %s\n' "$*" >> "$DOTSYNC_TEST_CALL_LOG"
scratch=""
show="false"
while (($#)); do
  case "$1" in
    --scratch-path) scratch="$2"; shift 2 ;;
    --show-bin-path) show="true"; shift ;;
    *) shift ;;
  esac
done
bin_path="$scratch/fake-bin"
if [[ "$show" == "true" ]]; then
  printf '%s\n' "$bin_path"
else
  mkdir -p "$bin_path"
  printf '%s' "${DOTSYNC_TEST_BINARY_PAYLOAD:-safe-binary}" > "$bin_path/DotSync"
  chmod 755 "$bin_path/DotSync"
fi
''',
    )
    _write_executable(
        fake_bin / "lipo",
        r'''
printf 'lipo %s\n' "$*" >> "$DOTSYNC_TEST_CALL_LOG"
if [[ "$1" == "-create" ]]; then
  first="$2"
  second="$3"
  [[ "$4" == "-output" ]]
  output="$5"
  {
    printf 'universal:'
    cat "$first"
    printf ':'
    cat "$second"
    printf ':end'
  } > "$output"
  chmod 755 "$output"
else
  input="$1"
  [[ "$2" == "-verify_arch" ]]
  [[ "$3" == "arm64" && "$4" == "x86_64" ]]
  grep -aq '^universal:' "$input"
fi
''',
    )
    _write_executable(
        fake_bin / "strip",
        'printf \'strip %s\\n\' "$*" >> "$DOTSYNC_TEST_CALL_LOG"',
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOTSYNC_TEST_CALL_LOG": str(call_log),
    }
    return project, env


def _run_fake_build(project: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/build_macos_app.sh"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )


def test_info_plist_template_defines_menu_bar_only_macos13_app():
    plist = plistlib.loads(_render_info_plist(version="0.3.0", build="300"))

    assert plist["CFBundleIdentifier"] == "dev.changja88.dotsync"
    assert plist["CFBundleExecutable"] == "DotSync"
    assert plist["CFBundleShortVersionString"] == "0.3.0"
    assert plist["CFBundleVersion"] == "300"
    assert plist["LSMinimumSystemVersion"] == "13.0"
    assert plist["LSUIElement"] is True


@pytest.mark.no_subprocess_block
def test_local_build_assembles_only_the_two_exact_macos13_architectures(tmp_path):
    project, env = _fake_build_project(tmp_path)

    result = _run_fake_build(project, env)

    app = project / "build" / "DotSync.app"
    executable = app / "Contents" / "MacOS" / "DotSync"
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"{app}\n"
    assert executable.is_file()
    assert os.access(executable, os.X_OK)
    assert plist["CFBundleShortVersionString"] == "0.3.0"
    assert plist["CFBundleVersion"] == "0.3.0"
    assert not (project / "Casks" / "dotsync-app.rb").exists()

    calls = Path(env["DOTSYNC_TEST_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
    swift_calls = [line for line in calls if line.startswith("swift ")]
    assert len(swift_calls) == 4
    assert sum("--triple arm64-apple-macosx13.0" in line for line in swift_calls) == 2
    assert sum("--triple x86_64-apple-macosx13.0" in line for line in swift_calls) == 2
    assert all("--configuration release" in line for line in swift_calls)
    assert all("--sdk /fake/MacOSX.sdk" in line for line in swift_calls)
    assert sum("--show-bin-path" in line for line in swift_calls) == 2

    lipo_calls = [line for line in calls if line.startswith("lipo ")]
    assert len(lipo_calls) == 2
    assert lipo_calls[0].startswith("lipo -create ")
    assert "swift-arm64/fake-bin/DotSync" in lipo_calls[0]
    assert "swift-x86_64/fake-bin/DotSync" in lipo_calls[0]
    assert "DotSync.app/Contents/MacOS/DotSync -verify_arch arm64 x86_64" in lipo_calls[1]
    strip_calls = [line for line in calls if line.startswith("strip ")]
    assert strip_calls == [f"strip -S {executable}"]


@pytest.mark.no_subprocess_block
def test_local_build_rejects_non_exact_semantic_project_version(tmp_path):
    project, env = _fake_build_project(tmp_path, version="0.3")

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "payload",
    [
        "checkout=/private/tmp/project",
        "https://127.0.0.1:4040/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "/Users/example/.codex/auth.json",
        "/Users/example/.claude.json",
    ],
)
def test_local_build_rejects_checkout_capability_and_provider_home_leaks(
    tmp_path, payload
):
    project, env = _fake_build_project(tmp_path)
    if payload.startswith("checkout="):
        payload = f"checkout={project}"
    env["DOTSYNC_TEST_BINARY_PAYLOAD"] = payload

    result = _run_fake_build(project, env)

    assert result.returncode != 0


def test_local_build_never_creates_or_claims_a_public_cask():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "Casks/dotsync-app.rb" not in script
    assert "notarytool" not in script
    assert "codesign --sign -" not in script
    assert ".zip" not in script


def test_formula_test_checks_version_and_state_free_ui_installation():
    formula = (REPO_ROOT / "Formula" / "dotsync.rb").read_text(encoding="utf-8")

    assert 'assert_match "dotsync #{version}"' in formula
    assert 'system bin/"dotsync", "ui", "--check"' in formula


@pytest.mark.no_subprocess_block
def test_root_make_help_lists_public_app_targets_without_local_dev():
    result = subprocess.run(
        ["make", "help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "test-ui" in result.stdout
    assert "test-native" in result.stdout
    assert "build-app" in result.stdout
    assert "local_dev" not in result.stdout
