"""scripts/build-app.sh run for real, with the Xcode tools and codesign
replaced by PATH stubs: the stub xcodebuild leaves the app bundle with the
date it would keep from an earlier build.

The Dock keeps an app's icon while the bundle's date stays the same, and
xcodebuild never re-dates the bundle folder, so the zip (and the app brew
installs from it) has to carry the date of this build.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

XCODEBUILD = """
app=build/app/Build/Products/Release/dotsync.app
mkdir -p "$app/Contents/PlugIns/dotsyncWidget.appex/Contents"
touch -t 202001010000 "$app"
"""


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/bin/bash\n{body}\n")
    stub.chmod(0o755)


@pytest.mark.no_subprocess_block
def test_zipped_app_carries_the_build_date(tmp_path):
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    (work / "macos").mkdir()
    (work / "scripts" / "build-app.sh").write_bytes(
        (REPO_ROOT / "scripts" / "build-app.sh").read_bytes()
    )
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "xcodegen", "exit 0")
    _write_stub(bin_dir, "xcodebuild", XCODEBUILD)
    _write_stub(bin_dir, "codesign", "exit 0")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    started = time.time()

    subprocess.run(
        ["bash", "scripts/build-app.sh", "9.9.9"],
        cwd=work, env=env, capture_output=True, text=True, check=True,
    )

    out = tmp_path / "unzipped"
    subprocess.run(
        ["ditto", "-x", "-k", str(work / "dist" / "dotsync-app-9.9.9.zip"), str(out)],
        check=True,
    )
    assert (out / "dotsync.app").stat().st_mtime >= started - 2


BTT_USAGE = "BetterTouchTool 설정을 내보내고 가져오려면 자동화 권한이 필요해요"


@pytest.mark.no_subprocess_block
def test_app_info_plist_explains_the_apple_events_prompt(tmp_path):
    """xcodegen writes the app's Info.plist from project.yml; macOS shows
    NSAppleEventsUsageDescription when dotsync first drives BetterTouchTool."""
    import plistlib
    import shutil

    if shutil.which("xcodegen") is None:
        pytest.skip("xcodegen is not installed")
    for folder in ("App", "Shared", "Widget", "DotsyncKit"):
        (tmp_path / folder).mkdir()
    shutil.copy(REPO_ROOT / "macos" / "project.yml", tmp_path / "project.yml")

    subprocess.run(["xcodegen", "generate", "--quiet"], cwd=tmp_path, check=True, capture_output=True)

    info = plistlib.loads((tmp_path / "App" / "Info.plist").read_bytes())
    assert info["NSAppleEventsUsageDescription"] == BTT_USAGE


def test_app_is_signed_with_the_apple_events_entitlement():
    """build-app.sh signs the app with App.entitlements under the hardened
    runtime, which blocks Apple Events to BetterTouchTool without this key."""
    import plistlib

    script = (REPO_ROOT / "scripts" / "build-app.sh").read_text()
    assert '--entitlements macos/App/App.entitlements -s "$IDENTITY" "$APP"' in script
    entitlements = plistlib.loads((REPO_ROOT / "macos" / "App" / "App.entitlements").read_bytes())
    assert entitlements["com.apple.security.automation.apple-events"] is True
