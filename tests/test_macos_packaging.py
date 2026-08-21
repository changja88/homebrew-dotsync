from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_macos_app.sh"
SUPPORT_SCRIPT = REPO_ROOT / "scripts" / "macos_app_support.py"
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


def _fake_build_project(
    tmp_path: Path,
    *,
    version: str = "0.3.0",
    pyproject_text: str | None = None,
) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "packaging").mkdir()
    (project / "macos" / "DotSyncApp").mkdir(parents=True)
    shutil.copy2(BUILD_SCRIPT, project / "scripts" / BUILD_SCRIPT.name)
    shutil.copy2(SUPPORT_SCRIPT, project / "scripts" / SUPPORT_SCRIPT.name)
    shutil.copy2(PLIST_TEMPLATE, project / "packaging" / PLIST_TEMPLATE.name)
    if pyproject_text is None:
        pyproject_text = f'[project]\nname = "dotsync"\nversion = "{version}"\n'
    (project / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.log"
    _write_executable(
        fake_bin / "xcrun",
        r'''
[[ "$#" == 3 ]] || exit 80
[[ "$1" == "--sdk" && "$2" == "macosx" && "$3" == "--show-sdk-path" ]] || exit 80
printf 'xcrun\t%s\t%s\t%s\n' "$1" "$2" "$3" >> "$DOTSYNC_TEST_CALL_LOG"
printf '%s\n' "/fake/MacOSX.sdk"
''',
    )
    _write_executable(
        fake_bin / "swift",
        r'''
[[ "$#" == 11 || "$#" == 12 ]] || exit 80
[[ "$1" == "build" ]] || exit 80
[[ "$2" == "--package-path" && "$3" == "macos/DotSyncApp" ]] || exit 80
[[ "$4" == "--configuration" && "$5" == "release" ]] || exit 80
[[ "$6" == "--triple" ]] || exit 80
triple="$7"
[[ "$triple" == "arm64-apple-macosx13.0" || "$triple" == "x86_64-apple-macosx13.0" ]] || exit 80
[[ "$8" == "--sdk" && "$9" == "/fake/MacOSX.sdk" ]] || exit 80
[[ "${10}" == "--scratch-path" ]] || exit 80
scratch="${11}"
if [[ "$triple" == arm64-* ]]; then
  [[ "$scratch" == */build/swift-arm64 ]] || exit 80
else
  [[ "$scratch" == */build/swift-x86_64 ]] || exit 80
fi
show="false"
if [[ "$#" == 12 ]]; then
  [[ "${12}" == "--show-bin-path" ]] || exit 80
  show="true"
fi
printf 'swift' >> "$DOTSYNC_TEST_CALL_LOG"
printf '\t%s' "$@" >> "$DOTSYNC_TEST_CALL_LOG"
printf '\n' >> "$DOTSYNC_TEST_CALL_LOG"
bin_path="$scratch/fake-bin"
if [[ "$show" == "true" ]]; then
  printf '%s\n' "$bin_path"
else
  mkdir -p "$bin_path"
  if [[ -n "${DOTSYNC_TEST_BINARY_PAYLOAD_FILE:-}" ]]; then
    cp "$DOTSYNC_TEST_BINARY_PAYLOAD_FILE" "$bin_path/DotSync"
  else
    printf '%s' "${DOTSYNC_TEST_BINARY_PAYLOAD:-safe-binary}" > "$bin_path/DotSync"
  fi
  chmod 755 "$bin_path/DotSync"
fi
''',
    )
    _write_executable(
        fake_bin / "lipo",
        r'''
printf 'lipo' >> "$DOTSYNC_TEST_CALL_LOG"
printf '\t%s' "$@" >> "$DOTSYNC_TEST_CALL_LOG"
printf '\n' >> "$DOTSYNC_TEST_CALL_LOG"
if [[ "$1" == "-create" ]]; then
  [[ "$#" == 5 ]] || exit 80
  first="$2"
  second="$3"
  [[ "$first" == */build/swift-arm64/fake-bin/DotSync ]] || exit 80
  [[ "$second" == */build/swift-x86_64/fake-bin/DotSync ]] || exit 80
  [[ "$4" == "-output" ]] || exit 80
  output="$5"
  [[ "$output" == */build/.dotsync-app-stage.????????/DotSync.app/Contents/MacOS/DotSync ]] || exit 80
  if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "lipo-create" ]]; then exit 91; fi
  {
    printf 'universal:'
    cat "$first"
    printf ':'
    cat "$second"
    printf ':end'
  } > "$output"
  chmod 755 "$output"
else
  [[ "$#" == 4 ]] || exit 80
  input="$1"
  [[ "$input" == */build/.dotsync-app-stage.????????/DotSync.app/Contents/MacOS/DotSync ]] || exit 80
  [[ "$2" == "-verify_arch" ]] || exit 80
  [[ "$3" == "arm64" && "$4" == "x86_64" ]] || exit 80
  if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "lipo-verify" ]]; then exit 92; fi
  grep -aq '^universal:' "$input"
fi
''',
    )
    _write_executable(
        fake_bin / "strip",
        r'''
[[ "$#" == 2 ]] || exit 80
[[ "$1" == "-S" ]] || exit 80
[[ "$2" == */build/.dotsync-app-stage.????????/DotSync.app/Contents/MacOS/DotSync ]] || exit 80
stage_root="${2%%/DotSync.app/*}"
bundle="${2%%/Contents/MacOS/DotSync}"
[[ "$(stat -f %Lp "$stage_root")" == "700" ]] || exit 80
printf 'strip\t%s\t%s\n' "$1" "$2" >> "$DOTSYNC_TEST_CALL_LOG"
if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "strip" ]]; then exit 93; fi
case "${DOTSYNC_TEST_INJECT_BUNDLE_ENTRY:-}" in
  symlink) ln -s "$DOTSYNC_TEST_EXTERNAL_TARGET" "$bundle/Contents/injected-link" ;;
  special) mkfifo "$bundle/Contents/injected-fifo" ;;
  unreadable)
    printf '%s' 'unreadable' > "$bundle/Contents/unreadable-file"
    chmod 000 "$bundle/Contents/unreadable-file"
    ;;
  "") ;;
  *) exit 95 ;;
esac
''',
    )
    _write_executable(
        fake_bin / "plutil",
        r'''
[[ "$#" == 2 ]] || exit 80
[[ "$1" == "-lint" ]] || exit 80
[[ "$2" == */build/.dotsync-app-stage.????????/DotSync.app/Contents/Info.plist ]] || exit 80
printf 'plutil\t%s\t%s\n' "$1" "$2" >> "$DOTSYNC_TEST_CALL_LOG"
if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "plutil" ]]; then exit 94; fi
/usr/bin/plutil "$@"
''',
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOTSYNC_TEST_CALL_LOG": str(call_log),
        "PYTHON": sys.executable,
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


def _assert_no_private_staging(project: Path) -> None:
    build_root = project / "build"
    assert not list(build_root.glob(".dotsync-app-stage.*"))


def _set_binary_payload(project: Path, env: dict[str, str], payload: bytes) -> None:
    payload_file = project.parent / "binary-payload"
    payload_file.write_bytes(payload)
    env["DOTSYNC_TEST_BINARY_PAYLOAD_FILE"] = str(payload_file)


def _logged_calls(env: dict[str, str]) -> list[list[str]]:
    return [
        line.split("\t")
        for line in Path(env["DOTSYNC_TEST_CALL_LOG"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def _force_scanner_failure(project: Path, env: dict[str, str]) -> None:
    real_python = env["PYTHON"]
    wrapper = project.parent / "scanner-failure-python"
    _write_executable(
        wrapper,
        f'''
if [[ "$1" == "-c" ]]; then exec "{real_python}" "$@"; fi
if [[ "${{2:-}}" == "scan" ]]; then exit 96; fi
exec "{real_python}" "$@"
''',
    )
    env["PYTHON"] = str(wrapper)


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
    assert stat.S_IMODE((project / "build").stat().st_mode) == 0o755
    assert stat.S_IMODE(app.stat().st_mode) == 0o755
    assert stat.S_IMODE((app / "Contents").stat().st_mode) == 0o755
    assert stat.S_IMODE((app / "Contents" / "MacOS").stat().st_mode) == 0o755
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
    assert stat.S_IMODE((app / "Contents" / "Info.plist").stat().st_mode) == 0o644
    assert plist["CFBundleShortVersionString"] == "0.3.0"
    assert plist["CFBundleVersion"] == "0.3.0"
    assert not (project / "Casks" / "dotsync-app.rb").exists()

    calls = _logged_calls(env)
    assert calls[0] == ["xcrun", "--sdk", "macosx", "--show-sdk-path"]
    arm_args = [
        "build",
        "--package-path",
        "macos/DotSyncApp",
        "--configuration",
        "release",
        "--triple",
        "arm64-apple-macosx13.0",
        "--sdk",
        "/fake/MacOSX.sdk",
        "--scratch-path",
        str(project / "build" / "swift-arm64"),
    ]
    x86_args = arm_args.copy()
    x86_args[6] = "x86_64-apple-macosx13.0"
    x86_args[10] = str(project / "build" / "swift-x86_64")
    assert calls[1:5] == [
        ["swift", *arm_args],
        ["swift", *arm_args, "--show-bin-path"],
        ["swift", *x86_args],
        ["swift", *x86_args, "--show-bin-path"],
    ]
    lipo_create = calls[5]
    staged_executable = Path(lipo_create[5])
    assert lipo_create == [
        "lipo",
        "-create",
        str(project / "build" / "swift-arm64" / "fake-bin" / "DotSync"),
        str(project / "build" / "swift-x86_64" / "fake-bin" / "DotSync"),
        "-output",
        str(staged_executable),
    ]
    assert calls[6] == ["strip", "-S", str(staged_executable)]
    assert calls[7] == [
        "lipo",
        str(staged_executable),
        "-verify_arch",
        "arm64",
        "x86_64",
    ]
    assert calls[8] == [
        "plutil",
        "-lint",
        str(staged_executable.parents[1] / "Info.plist"),
    ]


@pytest.mark.no_subprocess_block
def test_local_build_rejects_non_exact_semantic_project_version(tmp_path):
    project, env = _fake_build_project(tmp_path, version="0.3")

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_local_build_rejects_semantic_version_outside_project_table(tmp_path):
    project, env = _fake_build_project(
        tmp_path,
        pyproject_text='[tool.fixture]\nversion = "9.9.9"\n',
    )

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_local_build_uses_only_project_version_when_other_tables_have_versions(
    tmp_path,
):
    project, env = _fake_build_project(
        tmp_path,
        pyproject_text=(
            '[project]\nname = "dotsync"\nversion = "0.3.0"\n'
            '[tool.fixture]\nversion = "9.9.9"\n'
        ),
    )

    result = _run_fake_build(project, env)

    assert result.returncode == 0, result.stdout + result.stderr
    plist = plistlib.loads(
        (project / "build" / "DotSync.app" / "Contents" / "Info.plist").read_bytes()
    )
    assert plist["CFBundleShortVersionString"] == "0.3.0"


@pytest.mark.no_subprocess_block
def test_local_build_rejects_symlinked_build_root_without_touching_target(tmp_path):
    project, env = _fake_build_project(tmp_path)
    external_build = tmp_path / "external-build"
    sentinel = external_build / "swift-arm64" / "sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep", encoding="utf-8")
    (project / "build").symlink_to(external_build, target_is_directory=True)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.no_subprocess_block
def test_local_build_rejects_symlinked_scratch_child_without_touching_target(tmp_path):
    project, env = _fake_build_project(tmp_path)
    (project / "build").mkdir()
    external_scratch = tmp_path / "external-scratch"
    external_scratch.mkdir()
    sentinel = external_scratch / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (project / "build" / "swift-arm64").symlink_to(
        external_scratch,
        target_is_directory=True,
    )

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.no_subprocess_block
def test_local_build_rejects_symlinked_final_app_without_touching_target(tmp_path):
    project, env = _fake_build_project(tmp_path)
    build_root = project / "build"
    build_root.mkdir()
    external_app = tmp_path / "external-app"
    external_app.mkdir()
    sentinel = external_app / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (build_root / "DotSync.app").symlink_to(external_app, target_is_directory=True)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (build_root / "DotSync.app").is_symlink()
    _assert_no_private_staging(project)


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
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "payload",
    [
        "/Users/example/.codex-backup/auth.json",
        "/Users/example/.claude-old/settings.json",
        "https://127.0.0.1/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    ],
)
def test_local_build_accepts_provider_near_paths_and_non_43_char_tokens(
    tmp_path, payload
):
    project, env = _fake_build_project(tmp_path)
    env["DOTSYNC_TEST_BINARY_PAYLOAD"] = payload

    result = _run_fake_build(project, env)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "payload",
    [
        b"https://127.0.0.1/?token=" + b"A" * 43 + b"\xfftrailing",
        b'{"token":"' + b"B" * 43 + b'"}\x00trailing',
        b'{"capability" : "' + b"C" * 43 + b'"}\xfftrailing',
        b"prefix\x00/Users/example/.claude/settings.json\xfftrailing",
    ],
)
def test_local_build_rejects_exact_binary_capabilities_and_provider_components(
    tmp_path, payload
):
    project, env = _fake_build_project(tmp_path)
    _set_binary_payload(project, env, payload)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_local_build_rejects_exact_checkout_bytes_with_binary_trailing_data(tmp_path):
    project, env = _fake_build_project(tmp_path)
    _set_binary_payload(project, env, b"prefix\x00" + os.fsencode(project) + b"\xffend")

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_failed_rebuild_preserves_preexisting_valid_final_app(tmp_path):
    project, env = _fake_build_project(tmp_path)
    previous_app = project / "build" / "DotSync.app"
    previous_app.mkdir(parents=True)
    marker = previous_app / "previous-valid-build.txt"
    marker.write_text("keep", encoding="utf-8")
    env["DOTSYNC_TEST_BINARY_PAYLOAD"] = "/Users/example/.codex/auth.json"

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "keep"
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_successful_rebuild_replaces_preexisting_valid_final_app(tmp_path):
    project, env = _fake_build_project(tmp_path)
    previous_app = project / "build" / "DotSync.app"
    previous_app.mkdir(parents=True)
    marker = previous_app / "previous-valid-build.txt"
    marker.write_text("replace", encoding="utf-8")

    result = _run_fake_build(project, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
    assert (previous_app / "Contents" / "MacOS" / "DotSync").is_file()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("tool", ["lipo-create", "strip", "lipo-verify", "plutil"])
def test_tool_failure_leaves_no_new_final_or_private_staging(tmp_path, tool):
    project, env = _fake_build_project(tmp_path)
    env["DOTSYNC_TEST_FAIL_TOOL"] = tool

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("entry_kind", ["special", "unreadable"])
def test_scanner_rejects_special_and_unreadable_entries_without_residue(
    tmp_path, entry_kind
):
    project, env = _fake_build_project(tmp_path)
    env["DOTSYNC_TEST_INJECT_BUNDLE_ENTRY"] = entry_kind

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_scanner_rejects_symlink_without_following_or_leaving_residue(tmp_path):
    project, env = _fake_build_project(tmp_path)
    external = tmp_path / "external-sentinel"
    external.write_text("keep", encoding="utf-8")
    env["DOTSYNC_TEST_INJECT_BUNDLE_ENTRY"] = "symlink"
    env["DOTSYNC_TEST_EXTERNAL_TARGET"] = str(external)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert external.read_text(encoding="utf-8") == "keep"
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_scanner_process_failure_is_fail_closed_and_cleans_staging(tmp_path):
    project, env = _fake_build_project(tmp_path)
    _force_scanner_failure(project, env)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize(
    "payload",
    [
        b"?token=" + b"A" * 42 + b"&next=1",
        b"?token=" + b"A" * 44 + b"&next=1",
        b'{"token":"' + b"B" * 42 + b'"}',
        b'{"token":"' + b"B" * 44 + b'"}',
        b'{"mytoken":"' + b"B" * 43 + b'"}',
        b"/Users/example/.codex-backup/auth.json",
        b"/Users/example/.claude-old/settings.json",
        b"/Users/example/.claude.json.bak",
    ],
)
def test_local_build_accepts_non_contract_binary_near_matches(tmp_path, payload):
    project, env = _fake_build_project(tmp_path)
    _set_binary_payload(project, env, payload)

    result = _run_fake_build(project, env)

    assert result.returncode == 0, result.stdout + result.stderr


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
