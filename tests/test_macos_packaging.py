from __future__ import annotations

import errno
import importlib.util
import os
import plistlib
import shutil
import stat
import subprocess
import sys
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
[[ "$#" == 11 || "$#" == 12 ]] || exit 80
[[ "$1" == "build" ]] || exit 80
[[ "$2" == "--package-path" && "$3" == "$DOTSYNC_TEST_PROJECT/macos/DotSyncApp" ]] || exit 80
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
  [[ "$first" == */swift-arm64/fake-bin/DotSync ]] || exit 80
  [[ "$second" == */swift-x86_64/fake-bin/DotSync ]] || exit 80
  [[ "$4" == "-output" ]] || exit 80
  output="$5"
  [[ "$output" == DotSync ]] || exit 80
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
        str(project / "macos" / "DotSyncApp"),
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
    assert arm_binary_path.endswith("/swift-arm64/fake-bin/DotSync")
    assert x86_binary_path.endswith("/swift-x86_64/fake-bin/DotSync")
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
    for index in range(100):
        existing_file = previous_app / f"existing-{index:03d}.txt"
        existing_file.write_text(f"keep-{index}", encoding="utf-8")
        existing_file.chmod(0o444)
    before = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_ino)
        for path in previous_app.iterdir()
    }

    result = _run_fake_build(project, env)

    assert result.returncode != 0
    after = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_ino)
        for path in previous_app.iterdir()
    }
    assert after == before
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
                        )
                assert not (repo / "build" / "DotSync.app").exists()
            finally:
                os.close(scanned.descriptor)
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(repo_fd)


def test_stage_open_failure_after_creation_still_removes_owned_stage(
    tmp_path,
    monkeypatch,
):
    support = _load_support_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    build_fd = -1
    real_open_directory = support._open_directory_at
    failed_once = False

    def fail_first_stage_open(parent_fd, child_name, **kwargs):
        nonlocal failed_once
        if child_name.startswith(".dotsync-app-stage.") and not failed_once:
            failed_once = True
            raise OSError(errno.EIO, "injected stage open failure")
        return real_open_directory(parent_fd, child_name, **kwargs)

    try:
        build_fd, build_identity = support._prepare_build_root(
            repo_fd,
            os.fstat(repo_fd).st_dev,
        )
        with monkeypatch.context() as patch_context:
            patch_context.setattr(support, "_open_directory_at", fail_first_stage_open)
            with pytest.raises(OSError, match="injected stage open failure"):
                with support._owned_staging_directory(
                    build_fd,
                    build_identity.device,
                ):
                    pytest.fail("stage setup unexpectedly completed")
        assert not list((repo / "build").glob(".dotsync-app-stage.*"))
    finally:
        if build_fd >= 0:
            os.close(build_fd)
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
