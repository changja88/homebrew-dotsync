from __future__ import annotations

import errno
import importlib.util
import os
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
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
    (project / "macos" / "DotSyncApp" / "Package.swift").write_text(
        "snapshot-package\n",
        encoding="utf-8",
    )
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
if [[ -n "${DOTSYNC_TEST_REPLACE_BUILD_ROOT:-}" && ! -e "$DOTSYNC_TEST_ROOT_REPLACED_MARKER" ]]; then
  mv "$DOTSYNC_TEST_PROJECT/build" "$DOTSYNC_TEST_PROJECT/detached-build"
  ln -s "$DOTSYNC_TEST_EXTERNAL_BUILD" "$DOTSYNC_TEST_PROJECT/build"
  : > "$DOTSYNC_TEST_ROOT_REPLACED_MARKER"
fi
if [[ -n "${DOTSYNC_TEST_REPLACE_CHECKOUT_INPUTS:-}" && ! -e "$DOTSYNC_TEST_INPUTS_REPLACED_MARKER" ]]; then
  mv "$DOTSYNC_TEST_PROJECT/macos/DotSyncApp" "$DOTSYNC_TEST_PROJECT/original-DotSyncApp"
  mkdir -p "$DOTSYNC_TEST_PROJECT/macos/DotSyncApp"
  printf '%s\n' 'attacker-package' > "$DOTSYNC_TEST_PROJECT/macos/DotSyncApp/Package.swift"
  printf '%s\n' 'invalid replacement plist' > "$DOTSYNC_TEST_PROJECT/packaging/DotSync-Info.plist.in"
  : > "$DOTSYNC_TEST_INPUTS_REPLACED_MARKER"
fi
if [[ -n "${DOTSYNC_TEST_HOLD_SWIFT:-}" ]]; then
  if [[ -n "${DOTSYNC_TEST_SWIFT_PID_FILE:-}" ]]; then
    printf '%s\n' "$$" > "$DOTSYNC_TEST_SWIFT_PID_FILE"
  fi
  if [[ -n "${DOTSYNC_TEST_GRANDCHILD_PID_FILE:-}" ]]; then
    /bin/sh -c 'trap "" INT TERM HUP; printf "%s\n" "$$" > "$1"; while true; do sleep 0.05; done' sh "$DOTSYNC_TEST_GRANDCHILD_PID_FILE" &
  fi
  : > "$DOTSYNC_TEST_SWIFT_HELD_MARKER"
  while [[ ! -e "$DOTSYNC_TEST_RELEASE_SWIFT_MARKER" ]]; do
    sleep 0.05
  done
fi
[[ "$#" == 11 || "$#" == 12 ]] || exit 80
[[ "$1" == "build" ]] || exit 80
[[ "$2" == "--package-path" && "$3" == "package" ]] || exit 80
grep -qx 'snapshot-package' "$3/Package.swift" || exit 80
if [[ -n "${DOTSYNC_TEST_MUTATE_PACKAGE_SNAPSHOT:-}" && ! -e "$DOTSYNC_TEST_PACKAGE_MUTATED_MARKER" ]]; then
  printf '%s\n' 'unexpected source' > "$3/Injected.swift"
  : > "$DOTSYNC_TEST_PACKAGE_MUTATED_MARKER"
fi
[[ "$4" == "--configuration" && "$5" == "release" ]] || exit 80
[[ "$6" == "--triple" ]] || exit 80
triple="$7"
[[ "$triple" == "arm64-apple-macosx13.0" || "$triple" == "x86_64-apple-macosx13.0" ]] || exit 80
[[ "$8" == "--sdk" && "$9" == "/fake/MacOSX.sdk" ]] || exit 80
[[ "${10}" == "--scratch-path" ]] || exit 80
scratch="${11}"
if [[ "$triple" == arm64-* ]]; then
  [[ "$scratch" == swift-arm64 ]] || exit 80
else
  [[ "$scratch" == swift-x86_64 ]] || exit 80
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
  [[ "$first" =~ ^/dev/fd/[0-9]+$ ]] || exit 80
  [[ "$second" =~ ^/dev/fd/[0-9]+$ ]] || exit 80
  [[ "$4" == "-output" ]] || exit 80
  output="$5"
  [[ "$output" == DotSync ]] || exit 80
  if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "lipo-create" ]]; then exit 91; fi
  if [[ -n "${DOTSYNC_TEST_REBIND_SWIFT_BINARIES:-}" ]]; then
    stages=("$DOTSYNC_TEST_PROJECT"/build/.dotsync-app-stage.*)
    [[ "${#stages[@]}" == 1 && -d "${stages[0]}" ]] || exit 80
    printf '%s' 'rebound-arm' > "${stages[0]}/swift-arm64/fake-bin/rebound"
    mv "${stages[0]}/swift-arm64/fake-bin/rebound" "${stages[0]}/swift-arm64/fake-bin/DotSync"
    printf '%s' 'rebound-x86' > "${stages[0]}/swift-x86_64/fake-bin/rebound"
    mv "${stages[0]}/swift-x86_64/fake-bin/rebound" "${stages[0]}/swift-x86_64/fake-bin/DotSync"
  fi
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
  [[ "$input" == DotSync ]] || exit 80
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
[[ "$2" == DotSync ]] || exit 80
stages=("$DOTSYNC_TEST_PROJECT"/build/.dotsync-app-stage.*)
[[ "${#stages[@]}" == 1 && -d "${stages[0]}" ]] || exit 80
stage_root="${stages[0]}"
bundle="$stage_root/DotSync.app"
[[ "$(stat -f %Lp "$stage_root")" == "700" ]] || exit 80
printf 'strip\t%s\t%s\n' "$1" "$2" >> "$DOTSYNC_TEST_CALL_LOG"
if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "strip" ]]; then exit 93; fi
/bin/cp "$2" "$2.stripped"
/bin/mv "$2.stripped" "$2"
case "${DOTSYNC_TEST_INJECT_BUNDLE_ENTRY:-}" in
  symlink) ln -s "$DOTSYNC_TEST_EXTERNAL_TARGET" "$bundle/Contents/injected-link" ;;
  special) mkfifo "$bundle/Contents/injected-fifo" ;;
  unreadable)
    printf '%s' 'unreadable' > "$bundle/Contents/unreadable-file"
    chmod 000 "$bundle/Contents/unreadable-file"
    ;;
  hardlink) ln "$DOTSYNC_TEST_EXTERNAL_TARGET" "$bundle/Contents/injected-hardlink" ;;
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
[[ "$2" == Info.plist ]] || exit 80
printf 'plutil\t%s\t%s\n' "$1" "$2" >> "$DOTSYNC_TEST_CALL_LOG"
if [[ "${DOTSYNC_TEST_FAIL_TOOL:-}" == "plutil" ]]; then exit 94; fi
/usr/bin/plutil "$@"
''',
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOTSYNC_TEST_CALL_LOG": str(call_log),
        "DOTSYNC_TEST_PROJECT": str(project),
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


def _load_support_module():
    module_name = f"dotsync_macos_app_support_{os.urandom(4).hex()}"
    spec = importlib.util.spec_from_file_location(module_name, SUPPORT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _wait_for_path(path: Path, process: subprocess.Popen[str], *, label: str) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"build exited before {label}: {stdout}{stderr}")
        if time.monotonic() >= deadline:
            process.kill()
            process.wait()
            pytest.fail(f"timed out waiting for {label}")
        time.sleep(0.01)


def _assert_process_gone(process_id: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail(f"process {process_id} survived build termination")


def _write_support_runner(
    path: Path,
    *,
    injection: str,
) -> None:
    path.write_text(
        """
import importlib.util
import os
from pathlib import Path
import signal
import sys

project = Path(sys.argv[1])
support_path = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("packaging_support_runner", support_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

INJECTION

raise SystemExit(module.main(["assemble", str(project)]))
""".replace("INJECTION", injection),
        encoding="utf-8",
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
    assert result.returncode == 0, result.stdout + result.stderr
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
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
        "package",
        "--configuration",
        "release",
        "--triple",
        "arm64-apple-macosx13.0",
        "--sdk",
        "/fake/MacOSX.sdk",
        "--scratch-path",
        "swift-arm64",
    ]
    x86_args = arm_args.copy()
    x86_args[6] = "x86_64-apple-macosx13.0"
    x86_args[10] = "swift-x86_64"
    arm_binary_path = calls[5][2]
    x86_binary_path = calls[5][3]
    assert re.fullmatch(r"/dev/fd/[0-9]+", arm_binary_path)
    assert re.fullmatch(r"/dev/fd/[0-9]+", x86_binary_path)
    assert arm_binary_path != x86_binary_path
    assert calls == [
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
        ["swift", *arm_args],
        ["swift", *arm_args, "--show-bin-path"],
        ["swift", *x86_args],
        ["swift", *x86_args, "--show-bin-path"],
        [
            "lipo",
            "-create",
            arm_binary_path,
            x86_binary_path,
            "-output",
            "DotSync",
        ],
        ["strip", "-S", "DotSync"],
        ["lipo", "DotSync", "-verify_arch", "arm64", "x86_64"],
        ["plutil", "-lint", "Info.plist"],
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
def test_stage_owned_scratch_ignores_legacy_symlink_without_touching_target(tmp_path):
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

    assert result.returncode == 0, result.stdout + result.stderr
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
def test_build_root_replacement_never_writes_or_deletes_external_target(tmp_path):
    project, env = _fake_build_project(tmp_path)
    external_build = tmp_path / "external-build"
    external_build.mkdir()
    sentinel = external_build / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    env["DOTSYNC_TEST_REPLACE_BUILD_ROOT"] = "1"
    env["DOTSYNC_TEST_EXTERNAL_BUILD"] = str(external_build)
    env["DOTSYNC_TEST_ROOT_REPLACED_MARKER"] = str(tmp_path / "root-replaced")

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert {path.name for path in external_build.iterdir()} == {"sentinel.txt"}
    assert sentinel.read_text(encoding="utf-8") == "keep"
    detached_build = project / "detached-build"
    assert not (detached_build / "DotSync.app").exists()
    assert not list(detached_build.glob(".dotsync-app-stage.*"))


@pytest.mark.no_subprocess_block
def test_checkout_package_and_plist_replacement_cannot_change_snapshotted_build(
    tmp_path,
):
    project, env = _fake_build_project(tmp_path)
    env["DOTSYNC_TEST_REPLACE_CHECKOUT_INPUTS"] = "1"
    env["DOTSYNC_TEST_INPUTS_REPLACED_MARKER"] = str(tmp_path / "inputs-replaced")

    result = _run_fake_build(project, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        project / "macos" / "DotSyncApp" / "Package.swift"
    ).read_text(encoding="utf-8") == "attacker-package\n"
    assert (
        project / "original-DotSyncApp" / "Package.swift"
    ).read_text(encoding="utf-8") == "snapshot-package\n"
    plist = plistlib.loads(
        (project / "build" / "DotSync.app" / "Contents" / "Info.plist").read_bytes()
    )
    assert plist["CFBundleShortVersionString"] == "0.3.0"


def test_source_addition_during_snapshot_copy_is_rejected(tmp_path, monkeypatch):
    """Removing the post-copy source manifest comparison must fail this test."""
    support = _load_support_module()
    project, _ = _fake_build_project(tmp_path)
    repo_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    added = False
    real_copy = support._copy_snapshot_file

    def copy_then_add_source(*args, **kwargs):
        nonlocal added
        result = real_copy(*args, **kwargs)
        if not added and args[2] == "Package.swift":
            added = True
            (project / "macos" / "DotSyncApp" / "Added.swift").write_text(
                "added during copy\n",
                encoding="utf-8",
            )
        return result

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with support._owned_staging_directory(
            build_fd,
            build_identity.device,
        ) as stage:
            with monkeypatch.context() as patch_context:
                patch_context.setattr(support, "_copy_snapshot_file", copy_then_add_source)
                with pytest.raises(
                    support.PackagingError,
                    match="source.*changed|changed.*source",
                ):
                    support._snapshot_build_inputs(
                        repo_fd,
                        os.fstat(repo_fd).st_dev,
                        stage,
                    )
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


@pytest.mark.no_subprocess_block
def test_first_swift_mutation_stops_before_show_bin_or_second_architecture(tmp_path):
    """Removing per-Swift-call snapshot validation must fail this test."""
    project, env = _fake_build_project(tmp_path)
    env["DOTSYNC_TEST_MUTATE_PACKAGE_SNAPSHOT"] = "1"
    env["DOTSYNC_TEST_PACKAGE_MUTATED_MARKER"] = str(tmp_path / "package-mutated")

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert _logged_calls(env) == [
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
        [
            "swift",
            "build",
            "--package-path",
            "package",
            "--configuration",
            "release",
            "--triple",
            "arm64-apple-macosx13.0",
            "--sdk",
            "/fake/MacOSX.sdk",
            "--scratch-path",
            "swift-arm64",
        ],
    ]
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_lipo_consumes_opened_binary_fds_after_scratch_names_are_rebound(tmp_path):
    project, env = _fake_build_project(tmp_path)
    env["DOTSYNC_TEST_REBIND_SWIFT_BINARIES"] = "1"

    result = _run_fake_build(project, env)

    assert result.returncode == 0, result.stdout + result.stderr
    executable = project / "build" / "DotSync.app" / "Contents" / "MacOS" / "DotSync"
    assert executable.read_bytes() == b"universal:safe-binary:safe-binary:end"


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
def test_existing_final_app_is_refused_before_tools_and_preserved_byte_for_byte(
    tmp_path,
):
    project, env = _fake_build_project(tmp_path)
    previous_app = project / "build" / "DotSync.app"
    previous_app.mkdir(parents=True)
    build_root = previous_app.parent
    build_root.chmod(0o711)
    for index in range(100):
        existing_file = previous_app / f"existing-{index:03d}.txt"
        existing_file.write_text(f"keep-{index}", encoding="utf-8")
        existing_file.chmod(0o444)
    before = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_ino)
        for path in previous_app.iterdir()
    }
    build_before = os.lstat(build_root)
    final_before = os.lstat(previous_app)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    after = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_ino)
        for path in previous_app.iterdir()
    }
    assert after == before
    build_after = os.lstat(build_root)
    final_after = os.lstat(previous_app)
    assert (
        build_after.st_dev,
        build_after.st_ino,
        build_after.st_mode,
        build_after.st_mtime_ns,
    ) == (
        build_before.st_dev,
        build_before.st_ino,
        build_before.st_mode,
        build_before.st_mtime_ns,
    )
    assert (
        final_after.st_dev,
        final_after.st_ino,
        final_after.st_mode,
        final_after.st_mtime_ns,
    ) == (
        final_before.st_dev,
        final_before.st_ino,
        final_before.st_mode,
        final_before.st_mtime_ns,
    )
    call_log = Path(env["DOTSYNC_TEST_CALL_LOG"])
    assert not call_log.exists() or call_log.read_bytes() == b""
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("entry_kind", ["file", "symlink", "fifo"])
def test_every_non_directory_final_entry_is_refused_and_preserved(
    tmp_path,
    entry_kind,
):
    project, env = _fake_build_project(tmp_path)
    build_root = project / "build"
    build_root.mkdir()
    final_entry = build_root / "DotSync.app"
    external = tmp_path / "external-target"
    external.write_text("keep", encoding="utf-8")
    if entry_kind == "file":
        final_entry.write_text("existing-final", encoding="utf-8")
    elif entry_kind == "symlink":
        final_entry.symlink_to(external)
    else:
        os.mkfifo(final_entry)
    before = os.lstat(final_entry)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    after = os.lstat(final_entry)
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_nlink) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
    )
    assert external.read_text(encoding="utf-8") == "keep"
    if entry_kind == "file":
        assert final_entry.read_text(encoding="utf-8") == "existing-final"
    call_log = Path(env["DOTSYNC_TEST_CALL_LOG"])
    assert not call_log.exists() or call_log.read_bytes() == b""
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
def test_sigterm_during_child_tool_unwinds_stage_cleanup_with_signal_status(tmp_path):
    project, env = _fake_build_project(tmp_path)
    held_marker = tmp_path / "swift-held"
    release_marker = tmp_path / "release-swift"
    env["DOTSYNC_TEST_HOLD_SWIFT"] = "1"
    env["DOTSYNC_TEST_SWIFT_HELD_MARKER"] = str(held_marker)
    env["DOTSYNC_TEST_RELEASE_SWIFT_MARKER"] = str(release_marker)
    process = subprocess.Popen(
        ["bash", "scripts/build_macos_app.sh"],
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not held_marker.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"build exited before hold marker: {stdout}{stderr}")
        if time.monotonic() >= deadline:
            process.kill()
            process.wait()
            pytest.fail("fake Swift did not reach its hold point")
        time.sleep(0.01)

    process.send_signal(signal.SIGTERM)
    returncode = process.wait(timeout=10)
    release_marker.touch()
    stdout, stderr = process.communicate(timeout=10)

    assert returncode == 128 + signal.SIGTERM, stdout + stderr
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_repeated_signals_terminate_the_tool_process_group_and_keep_first_status(
    tmp_path,
):
    """Dropping process-group escalation or first-signal retention must fail."""
    project, env = _fake_build_project(tmp_path)
    held_marker = tmp_path / "swift-held"
    release_marker = tmp_path / "release-swift"
    swift_pid_file = tmp_path / "swift.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"
    env.update(
        {
            "DOTSYNC_TEST_HOLD_SWIFT": "1",
            "DOTSYNC_TEST_SWIFT_HELD_MARKER": str(held_marker),
            "DOTSYNC_TEST_RELEASE_SWIFT_MARKER": str(release_marker),
            "DOTSYNC_TEST_SWIFT_PID_FILE": str(swift_pid_file),
            "DOTSYNC_TEST_GRANDCHILD_PID_FILE": str(grandchild_pid_file),
        }
    )
    process = subprocess.Popen(
        ["bash", "scripts/build_macos_app.sh"],
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_path(held_marker, process, label="Swift hold marker")
    _wait_for_path(swift_pid_file, process, label="Swift pid")
    _wait_for_path(grandchild_pid_file, process, label="Swift grandchild pid")
    swift_pid = int(swift_pid_file.read_text(encoding="utf-8"))
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8"))

    process.send_signal(signal.SIGTERM)
    _assert_process_gone(swift_pid)
    if process.poll() is None:
        process.send_signal(signal.SIGHUP)
    returncode = process.wait(timeout=10)
    stdout, stderr = process.communicate(timeout=10)

    assert returncode == 128 + signal.SIGTERM, stdout + stderr
    _assert_process_gone(swift_pid)
    _assert_process_gone(grandchild_pid)
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_repeated_signals_during_cleanup_cannot_interrupt_cleanup(tmp_path):
    """Making cleanup signal-reentrant must fail this real-process test."""
    project, env = _fake_build_project(tmp_path)
    runner = tmp_path / "cleanup-signal-runner.py"
    _write_support_runner(
        runner,
        injection="""
real_cleanup = module._remove_directory_contents_except
sent = False
def signal_then_cleanup(*args, **kwargs):
    global sent
    if not sent:
        sent = True
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGHUP)
    return real_cleanup(*args, **kwargs)
module._remove_directory_contents_except = signal_then_cleanup
""",
    )

    result = subprocess.run(
        [sys.executable, str(runner), str(project), str(SUPPORT_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_signal_immediately_after_publish_rolls_back_exact_final(tmp_path):
    """Removing post-rename rollback must fail this real-process test."""
    project, env = _fake_build_project(tmp_path)
    runner = tmp_path / "post-rename-signal-runner.py"
    _write_support_runner(
        runner,
        injection="""
real_rename = module._rename_no_replace
def rename_then_signal(source_fd, source_name, destination_fd, destination_name):
    real_rename(source_fd, source_name, destination_fd, destination_name)
    if source_name == module.FINAL_APP and destination_name == module.FINAL_APP:
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGHUP)
module._rename_no_replace = rename_then_signal
""",
    )

    result = subprocess.run(
        [sys.executable, str(runner), str(project), str(SUPPORT_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
@pytest.mark.parametrize("post_rename_failure", ["binding", "stat", "mode"])
def test_post_rename_validation_failure_rolls_back_through_private_stage(
    tmp_path,
    post_rename_failure,
):
    """Returning publication state to the caller too late must fail this test."""
    project, env = _fake_build_project(tmp_path)
    runner = tmp_path / f"post-rename-{post_rename_failure}-runner.py"
    injections = {
        "binding": """
real_verify = module._verify_build_binding
def fail_published_binding(repo_fd, build_fd, build_identity):
    real_verify(repo_fd, build_fd, build_identity)
    try:
        os.stat(module.FINAL_APP, dir_fd=build_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise module.PackagingError("injected post-rename binding failure")
module._verify_build_binding = fail_published_binding
""",
        "stat": """
real_rename = module._rename_no_replace
real_stat = module.os.stat
renamed = False
failed = False
def remember_rename(source_fd, source_name, destination_fd, destination_name):
    global renamed
    real_rename(source_fd, source_name, destination_fd, destination_name)
    if source_name == module.FINAL_APP and destination_name == module.FINAL_APP:
        renamed = True
def fail_first_final_stat_after_rename(path, *args, **kwargs):
    global failed
    if renamed and not failed and path == module.FINAL_APP:
        failed = True
        raise OSError(5, "injected post-rename stat failure")
    return real_stat(path, *args, **kwargs)
module._rename_no_replace = remember_rename
module.os.stat = fail_first_final_stat_after_rename
""",
        "mode": """
real_rename = module._rename_no_replace
def corrupt_mode_after_rename(source_fd, source_name, destination_fd, destination_name):
    real_rename(source_fd, source_name, destination_fd, destination_name)
    if source_name == module.FINAL_APP and destination_name == module.FINAL_APP:
        os.chmod(destination_name, 0o700, dir_fd=destination_fd)
module._rename_no_replace = corrupt_mode_after_rename
""",
    }
    _write_support_runner(runner, injection=injections[post_rename_failure])

    result = subprocess.run(
        [sys.executable, str(runner), str(project), str(SUPPORT_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_signal_between_tools_prevents_the_next_tool_from_spawning(tmp_path):
    """Removing the retained-signal check before Popen must add a second spawn."""
    project, env = _fake_build_project(tmp_path)
    spawn_log = tmp_path / "spawn.log"
    env["DOTSYNC_TEST_SPAWN_LOG"] = str(spawn_log)
    runner = tmp_path / "between-tools-signal-runner.py"
    _write_support_runner(
        runner,
        injection="""
real_popen = module.subprocess.Popen
def counted_popen(*args, **kwargs):
    with open(os.environ["DOTSYNC_TEST_SPAWN_LOG"], "a", encoding="utf-8") as log:
        log.write("spawn\\n")
    return real_popen(*args, **kwargs)
module.subprocess.Popen = counted_popen

real_run_tool = module._run_tool
completed = 0
def signal_after_first_tool(*args, **kwargs):
    global completed
    result = real_run_tool(*args, **kwargs)
    completed += 1
    if completed == 1:
        os.kill(os.getpid(), signal.SIGTERM)
    return result
module._run_tool = signal_after_first_tool
""",
    )

    result = subprocess.run(
        [sys.executable, str(runner), str(project), str(SUPPORT_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert spawn_log.read_text(encoding="utf-8").splitlines() == ["spawn"]
    assert _logged_calls(env) == [
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
    ]
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_signal_at_spawn_barrier_is_delivered_only_after_group_attachment(tmp_path):
    """Removing the spawn signal mask must expose an unattached signal handler."""
    project, env = _fake_build_project(tmp_path)
    handler_log = tmp_path / "handler.log"
    spawn_log = tmp_path / "spawn.log"
    env["DOTSYNC_TEST_HANDLER_LOG"] = str(handler_log)
    env["DOTSYNC_TEST_SPAWN_LOG"] = str(spawn_log)
    runner = tmp_path / "spawn-barrier-signal-runner.py"
    _write_support_runner(
        runner,
        injection="""
real_handle = module.SignalCoordinator._handle_signal
def recorded_handle(self, signum, frame):
    with open(os.environ["DOTSYNC_TEST_HANDLER_LOG"], "a", encoding="utf-8") as log:
        log.write("attached\\n" if self._active_process is not None else "unattached\\n")
    return real_handle(self, signum, frame)
module.SignalCoordinator._handle_signal = recorded_handle

real_popen = module.subprocess.Popen
sent = False
def signal_exactly_before_spawn(*args, **kwargs):
    global sent
    with open(os.environ["DOTSYNC_TEST_SPAWN_LOG"], "a", encoding="utf-8") as log:
        log.write("spawn\\n")
    if not sent:
        sent = True
        os.kill(os.getpid(), signal.SIGTERM)
    return real_popen(*args, **kwargs)
module.subprocess.Popen = signal_exactly_before_spawn
""",
    )

    result = subprocess.run(
        [sys.executable, str(runner), str(project), str(SUPPORT_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert spawn_log.read_text(encoding="utf-8").splitlines() == ["spawn"]
    assert handler_log.read_text(encoding="utf-8").splitlines() == ["attached"]
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


@pytest.mark.no_subprocess_block
def test_interrupted_process_group_is_quiesced_before_leader_is_reaped(tmp_path):
    """Replacing waitid(WNOWAIT) with communicate-before-quiesce must fail."""
    project, env = _fake_build_project(tmp_path)
    held_marker = tmp_path / "swift-held"
    release_marker = tmp_path / "release-swift"
    swift_pid_file = tmp_path / "swift.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"
    ordering_log = tmp_path / "ordering.log"
    env.update(
        {
            "DOTSYNC_TEST_HOLD_SWIFT": "1",
            "DOTSYNC_TEST_SWIFT_HELD_MARKER": str(held_marker),
            "DOTSYNC_TEST_RELEASE_SWIFT_MARKER": str(release_marker),
            "DOTSYNC_TEST_SWIFT_PID_FILE": str(swift_pid_file),
            "DOTSYNC_TEST_GRANDCHILD_PID_FILE": str(grandchild_pid_file),
            "DOTSYNC_TEST_ORDERING_LOG": str(ordering_log),
        }
    )
    runner = tmp_path / "process-group-order-runner.py"
    _write_support_runner(
        runner,
        injection="""
def record(event):
    with open(os.environ["DOTSYNC_TEST_ORDERING_LOG"], "a", encoding="utf-8") as log:
        log.write(event + "\\n")

real_waitid = module.os.waitid
def recorded_waitid(*args, **kwargs):
    result = real_waitid(*args, **kwargs)
    if result is not None:
        flags = args[2] if len(args) > 2 else kwargs["options"]
        record(
            "exit-observed-without-reap"
            if flags & os.WNOWAIT
            else "leader-reaped"
        )
    return result
module.os.waitid = recorded_waitid

real_quiesce = module.SignalCoordinator.quiesce_process_group
def recorded_quiesce(self, process_group, **kwargs):
    record("group-quiesce")
    return real_quiesce(self, process_group, **kwargs)
module.SignalCoordinator.quiesce_process_group = recorded_quiesce

real_popen = module.subprocess.Popen
sent = False
def signal_first_swift(*args, **kwargs):
    global sent
    process = real_popen(*args, **kwargs)
    command = args[0] if args else kwargs.get("args", [])
    if command and command[0] == "swift" and not sent:
        sent = True
        os.kill(os.getpid(), signal.SIGTERM)
    return process
module.subprocess.Popen = signal_first_swift
""",
    )

    result = subprocess.run(
        [sys.executable, str(runner), str(project), str(SUPPORT_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    events = ordering_log.read_text(encoding="utf-8").splitlines()
    observed = events.index("exit-observed-without-reap")
    quiesced = events.index("group-quiesce", observed)
    reaped = events.index("leader-reaped", quiesced)
    assert observed < quiesced < reaped
    swift_pid = int(swift_pid_file.read_text(encoding="utf-8"))
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8"))
    _assert_process_gone(swift_pid)
    _assert_process_gone(grandchild_pid)
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


def test_temporary_signal_handlers_restore_all_prior_handlers_after_failure():
    support = _load_support_module()
    previous = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }

    with pytest.raises(RuntimeError, match="injected body failure"):
        with support._temporary_signal_handlers():
            for signum, prior_handler in previous.items():
                assert signal.getsignal(signum) is not prior_handler
            raise RuntimeError("injected body failure")

    assert {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    } == previous


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
def test_scanner_rejects_hardlink_without_changing_external_inode(tmp_path):
    project, env = _fake_build_project(tmp_path)
    external = tmp_path / "external-hardlink-source"
    external.write_text("keep", encoding="utf-8")
    inode_before = external.stat().st_ino
    env["DOTSYNC_TEST_INJECT_BUNDLE_ENTRY"] = "hardlink"
    env["DOTSYNC_TEST_EXTERNAL_TARGET"] = str(external)

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    assert external.read_text(encoding="utf-8") == "keep"
    assert external.stat().st_ino == inode_before
    assert external.stat().st_nlink == 1
    assert not (project / "build" / "DotSync.app").exists()
    _assert_no_private_staging(project)


def test_scanner_rejects_injected_descendant_device_mismatch(tmp_path, monkeypatch):
    support = _load_support_module()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "payload").write_bytes(b"safe")
    bundle_fd = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    expected_device = os.fstat(bundle_fd).st_dev
    real_stat = support.os.stat
    real_fstat = support.os.fstat

    def mismatched(node_stat):
        return SimpleNamespace(
            st_dev=expected_device + 1,
            st_ino=node_stat.st_ino,
            st_mode=node_stat.st_mode,
            st_nlink=node_stat.st_nlink,
        )

    def injected_stat(path, *args, **kwargs):
        node_stat = real_stat(path, *args, **kwargs)
        return mismatched(node_stat) if path == "payload" else node_stat

    def injected_fstat(descriptor):
        node_stat = real_fstat(descriptor)
        return mismatched(node_stat) if stat.S_ISREG(node_stat.st_mode) else node_stat

    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(support.os, "stat", injected_stat)
            patch_context.setattr(support.os, "fstat", injected_fstat)
            with pytest.raises(support.PackagingError, match="device boundary"):
                support._scan_directory(bundle_fd, b"checkout", expected_device)
    finally:
        os.close(bundle_fd)


def test_scan_to_publish_replacement_is_rejected_before_atomic_rename(tmp_path):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    try:
        repo_device = os.fstat(repo_fd).st_dev
        build_fd, build_identity = support._prepare_build_root(repo_fd, repo_device)
        with support._owned_staging_directory(
            build_fd,
            build_identity.device,
        ) as stage:
            app_fd = support._create_directory(
                stage.descriptor,
                support.FINAL_APP,
                build_identity.device,
                0o755,
            )
            os.close(app_fd)
            scanned = support._open_and_scan_staged_app(
                stage.descriptor,
                checkout=b"checkout",
                expected_device=build_identity.device,
            )
            try:
                os.rename(
                    support.FINAL_APP,
                    "scanned-app",
                    src_dir_fd=stage.descriptor,
                    dst_dir_fd=stage.descriptor,
                )
                replacement_fd = support._create_directory(
                    stage.descriptor,
                    support.FINAL_APP,
                    build_identity.device,
                    0o755,
                )
                os.close(replacement_fd)

                with pytest.raises(support.PackagingError, match="changed after scanning"):
                    support._publish_scanned_app(
                        repo_fd=repo_fd,
                        build_fd=build_fd,
                        build_identity=build_identity,
                        stage=stage,
                        scanned_app=scanned,
                        coordinator=support.SignalCoordinator(),
                    )
                assert not (repo / "build" / "DotSync.app").exists()
            finally:
                os.close(scanned.descriptor)
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


@pytest.mark.parametrize("mutation", ["replace", "in-place"])
def test_descendant_manifest_change_is_rejected_before_publish(tmp_path, mutation):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with support._owned_staging_directory(
            build_fd,
            build_identity.device,
        ) as stage:
            app_fd = support._create_directory(
                stage.descriptor,
                support.FINAL_APP,
                build_identity.device,
                0o755,
            )
            contents_fd = support._create_directory(
                app_fd,
                "Contents",
                build_identity.device,
                0o755,
            )
            payload_fd = os.open(
                "payload",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=contents_fd,
            )
            os.write(payload_fd, b"AAAA")
            os.close(payload_fd)
            os.close(contents_fd)
            os.close(app_fd)
            scanned = support._open_and_scan_staged_app(
                stage.descriptor,
                checkout=b"checkout",
                expected_device=build_identity.device,
            )
            payload = (
                repo
                / "build"
                / stage.name
                / support.FINAL_APP
                / "Contents"
                / "payload"
            )
            original_stat = payload.stat()
            if mutation == "replace":
                payload.rename(payload.with_name("old-payload"))
                payload.write_bytes(b"AAAA")
            else:
                payload.write_bytes(b"BBBB")
            payload.chmod(stat.S_IMODE(original_stat.st_mode))
            os.utime(
                payload,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            try:
                with pytest.raises(
                    support.PackagingError,
                    match="descendant manifest changed",
                ):
                    support._publish_scanned_app(
                        repo_fd=repo_fd,
                        build_fd=build_fd,
                        build_identity=build_identity,
                        stage=stage,
                        scanned_app=scanned,
                        coordinator=support.SignalCoordinator(),
                    )
                assert not (repo / "build" / "DotSync.app").exists()
            finally:
                os.close(scanned.descriptor)
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


def test_publish_revalidates_the_open_staging_descriptor(tmp_path, monkeypatch):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with support._owned_staging_directory(
            build_fd,
            build_identity.device,
        ) as stage:
            app_fd = support._create_directory(
                stage.descriptor,
                support.FINAL_APP,
                build_identity.device,
                0o755,
            )
            os.close(app_fd)
            scanned = support._open_and_scan_staged_app(
                stage.descriptor,
                checkout=b"checkout",
                expected_device=build_identity.device,
            )
            real_fstat = support.os.fstat

            def replaced_staging_descriptor(descriptor):
                node_stat = real_fstat(descriptor)
                if descriptor != stage.descriptor:
                    return node_stat
                return SimpleNamespace(
                    st_dev=node_stat.st_dev,
                    st_ino=node_stat.st_ino + 1,
                    st_mode=node_stat.st_mode,
                    st_nlink=node_stat.st_nlink,
                )

            try:
                with monkeypatch.context() as patch_context:
                    patch_context.setattr(
                        support.os,
                        "fstat",
                        replaced_staging_descriptor,
                    )
                    with pytest.raises(
                        support.PackagingError,
                        match="open staging identity changed",
                    ):
                        support._publish_scanned_app(
                            repo_fd=repo_fd,
                            build_fd=build_fd,
                            build_identity=build_identity,
                            stage=stage,
                            scanned_app=scanned,
                            coordinator=support.SignalCoordinator(),
                        )
                assert not (repo / "build" / "DotSync.app").exists()
            finally:
                os.close(scanned.descriptor)
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


@pytest.mark.parametrize("post_publish_mutation", ["identity", "mode"])
def test_post_publish_final_identity_and_mode_are_proven_before_success(
    tmp_path,
    monkeypatch,
    post_publish_mutation,
):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    real_rename = support._rename_no_replace

    def mutate_after_rename(source_fd, source_name, destination_fd, destination_name):
        real_rename(source_fd, source_name, destination_fd, destination_name)
        if post_publish_mutation == "identity":
            os.rename(
                destination_name,
                "published-original",
                src_dir_fd=destination_fd,
                dst_dir_fd=destination_fd,
            )
            os.mkdir(destination_name, mode=0o755, dir_fd=destination_fd)
            replacement_fd = os.open(
                destination_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=destination_fd,
            )
            try:
                marker_fd = os.open(
                    "replacement-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=replacement_fd,
                )
                os.close(marker_fd)
            finally:
                os.close(replacement_fd)
        else:
            os.chmod(destination_name, 0o700, dir_fd=destination_fd)

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with support._owned_staging_directory(
            build_fd,
            build_identity.device,
        ) as stage:
            app_fd = support._create_directory(
                stage.descriptor,
                support.FINAL_APP,
                build_identity.device,
                0o755,
            )
            os.close(app_fd)
            scanned = support._open_and_scan_staged_app(
                stage.descriptor,
                checkout=b"checkout",
                expected_device=build_identity.device,
            )
            try:
                with monkeypatch.context() as patch_context:
                    patch_context.setattr(
                        support,
                        "_rename_no_replace",
                        mutate_after_rename,
                    )
                    with pytest.raises(support.PackagingError) as caught:
                        support._publish_scanned_app(
                            repo_fd=repo_fd,
                            build_fd=build_fd,
                            build_identity=build_identity,
                            stage=stage,
                            scanned_app=scanned,
                            coordinator=support.SignalCoordinator(),
                        )
            finally:
                os.close(scanned.descriptor)
        if post_publish_mutation == "identity":
            assert "rollback lost exact ownership" in str(caught.value)
            assert (repo / "build" / "published-original").is_dir()
            assert (
                repo / "build" / support.FINAL_APP / "replacement-marker"
            ).read_bytes() == b""
        else:
            assert "published final app mode" in str(caught.value)
            assert not (repo / "build" / support.FINAL_APP).exists()
        assert not list((repo / "build").glob(".dotsync-app-stage.*"))
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


def test_stage_first_open_failure_preserves_the_unadopted_private_entry(
    tmp_path,
    monkeypatch,
):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    real_open = support.os.open
    failed_once = False
    failed_name = ""

    def fail_first_stage_open(path, flags, *args, **kwargs):
        nonlocal failed_once, failed_name
        if str(path).startswith(".dotsync-app-stage.") and not failed_once:
            failed_once = True
            failed_name = str(path)
            raise OSError(errno.EIO, "injected stage open failure")
        return real_open(path, flags, *args, **kwargs)

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with monkeypatch.context() as patch_context:
            patch_context.setattr(support.os, "open", fail_first_stage_open)
            with pytest.raises(OSError, match="injected stage open failure"):
                with support._owned_staging_directory(
                    build_fd,
                    build_identity.device,
                ):
                    pytest.fail("stage setup unexpectedly completed")
        unadopted = repo / "build" / failed_name
        assert unadopted.is_dir()
        assert list(unadopted.iterdir()) == []
        assert stat.S_IMODE(unadopted.stat().st_mode) == 0o700
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


def test_stage_binding_replacement_after_first_open_is_never_adopted_or_deleted(
    tmp_path,
    monkeypatch,
):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    original_stage_name = "original-created-stage"
    replacement_name = ""
    real_stat = support.os.stat
    replaced = False

    def replace_before_first_stage_stat(path, *args, **kwargs):
        nonlocal replaced, replacement_name
        dir_fd = kwargs.get("dir_fd")
        if str(path).startswith(".dotsync-app-stage.") and not replaced:
            replaced = True
            replacement_name = str(path)
            os.rename(
                path,
                original_stage_name,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            os.mkdir(path, mode=0o700, dir_fd=dir_fd)
            replacement_fd = os.open(
                path,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            try:
                marker_fd = os.open(
                    "replacement-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=replacement_fd,
                )
                os.close(marker_fd)
            finally:
                os.close(replacement_fd)
        return real_stat(path, *args, **kwargs)

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                support.os,
                "stat",
                replace_before_first_stage_stat,
            )
            with pytest.raises(support.PackagingError):
                with support._owned_staging_directory(
                    build_fd,
                    build_identity.device,
                ):
                    pytest.fail("stage setup unexpectedly completed")
        replacement = repo / "build" / replacement_name
        assert (replacement / "replacement-marker").is_file()
        assert (repo / "build" / original_stage_name).is_dir()
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)
        shutil.rmtree(repo / "build", ignore_errors=True)


def test_stage_replacement_exactly_after_mkdir_is_never_adopted_or_deleted(
    tmp_path,
    monkeypatch,
):
    """Moving the first open behind a named stat must fail this test."""
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    replacement_name = ""
    original_name = "original-created-stage-at-mkdir"
    real_mkdir = support.os.mkdir
    injected = False

    def replace_after_mkdir(path, mode=0o777, *, dir_fd=None):
        nonlocal replacement_name, injected
        real_mkdir(path, mode=mode, dir_fd=dir_fd)
        if str(path).startswith(".dotsync-app-stage.") and not injected:
            injected = True
            replacement_name = str(path)
            os.rename(
                path,
                original_name,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            real_mkdir(path, mode=0o711, dir_fd=dir_fd)
            replacement_fd = os.open(
                path,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            try:
                marker_fd = os.open(
                    "replacement-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=replacement_fd,
                )
                os.close(marker_fd)
            finally:
                os.close(replacement_fd)

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with monkeypatch.context() as patch_context:
            patch_context.setattr(support.os, "mkdir", replace_after_mkdir)
            with pytest.raises(support.PackagingError):
                with support._owned_staging_directory(
                    build_fd,
                    build_identity.device,
                ):
                    pytest.fail("replacement was adopted")
        replacement = repo / "build" / replacement_name
        assert (replacement / "replacement-marker").read_bytes() == b""
        assert stat.S_IMODE(replacement.stat().st_mode) == 0o711
        assert (repo / "build" / original_name).is_dir()
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)
        shutil.rmtree(repo / "build", ignore_errors=True)


def test_created_build_root_replacement_is_never_adopted_or_mutated(
    tmp_path,
    monkeypatch,
):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    real_stat = support.os.stat
    replaced = False
    replacement_name = ""

    def replace_before_build_stat(path, *args, **kwargs):
        nonlocal replaced, replacement_name
        path_text = str(path)
        dir_fd = kwargs.get("dir_fd")
        build_exists = False
        if path_text == support.BUILD_DIRECTORY:
            try:
                real_stat(path, *args, **kwargs)
                build_exists = True
            except FileNotFoundError:
                pass
        should_replace = (
            path_text.startswith(".dotsync-build.")
            or (path_text == support.BUILD_DIRECTORY and build_exists)
        )
        if should_replace and not replaced:
            replaced = True
            replacement_name = path_text
            os.rename(
                path,
                "original-created-build",
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            os.mkdir(path, mode=0o711, dir_fd=dir_fd)
            replacement_fd = os.open(
                path,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            try:
                marker_fd = os.open(
                    "replacement-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=replacement_fd,
                )
                os.close(marker_fd)
            finally:
                os.close(replacement_fd)
        return real_stat(path, *args, **kwargs)

    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                support.os,
                "stat",
                replace_before_build_stat,
            )
            with pytest.raises(
                support.PackagingError,
                match="binding changed|identity no longer matches",
            ):
                support._prepare_build_root(repo_fd, os.fstat(repo_fd).st_dev)
        replacement = repo / replacement_name
        assert (replacement / "replacement-marker").is_file()
        assert stat.S_IMODE(replacement.stat().st_mode) == 0o711
        assert (repo / "original-created-build").is_dir()
    finally:
        os.close(repo_fd)


def test_new_build_replacement_exactly_after_mkdir_is_never_adopted_or_deleted(
    tmp_path,
    monkeypatch,
):
    """Replacing immediate no-follow open with named stat must fail this test."""
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    replacement_name = ""
    original_name = "original-created-build-at-mkdir"
    real_mkdir = support.os.mkdir
    injected = False

    def replace_after_mkdir(path, mode=0o777, *, dir_fd=None):
        nonlocal replacement_name, injected
        real_mkdir(path, mode=mode, dir_fd=dir_fd)
        path_text = str(path)
        if (
            path_text == support.BUILD_DIRECTORY
            or path_text.startswith(".dotsync-build.")
        ) and not injected:
            injected = True
            replacement_name = path_text
            os.rename(
                path,
                original_name,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            real_mkdir(path, mode=0o711, dir_fd=dir_fd)
            replacement_fd = os.open(
                path,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            try:
                marker_fd = os.open(
                    "replacement-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=replacement_fd,
                )
                os.close(marker_fd)
            finally:
                os.close(replacement_fd)

    try:
        with monkeypatch.context() as patch_context:
            patch_context.setattr(support.os, "mkdir", replace_after_mkdir)
            with pytest.raises(support.PackagingError):
                support._prepare_build_root(repo_fd, os.fstat(repo_fd).st_dev)
        replacement = repo / replacement_name
        assert (replacement / "replacement-marker").read_bytes() == b""
        assert stat.S_IMODE(replacement.stat().st_mode) == 0o711
        assert (repo / original_name).is_dir()
    finally:
        os.close(repo_fd)


def test_staging_cleanup_error_forces_failure(tmp_path, monkeypatch):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    stage_name = ""

    def fail_cleanup(*args, **kwargs):
        raise OSError(errno.EIO, "injected cleanup failure")

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with monkeypatch.context() as patch_context:
            patch_context.setattr(support, "_cleanup_owned_stage", fail_cleanup)
            with pytest.raises(support.PackagingError, match="staging cleanup failed"):
                with support._owned_staging_directory(
                    build_fd,
                    build_identity.device,
                ) as stage:
                    stage_name = stage.name
        assert (repo / "build" / stage_name).is_dir()
    finally:
        if stage_name:
            shutil.rmtree(repo / "build" / stage_name, ignore_errors=True)
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


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
