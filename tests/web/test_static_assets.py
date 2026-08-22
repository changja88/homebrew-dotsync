from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from dotsync.web.server import _STATIC_ROUTES


STATIC_ROOT = (
    Path(__file__).resolve().parents[2] / "lib" / "dotsync" / "web" / "static"
)
ASSET_NAMES = (
    "index.html",
    "styles.css",
    "state.mjs",
    "api-client.mjs",
    "render.mjs",
    "app.mjs",
)


@pytest.fixture
def package_assets() -> dict[str, str]:
    return {
        name: (STATIC_ROOT / name).read_text(encoding="utf-8")
        for name in ASSET_NAMES
    }


def test_packaged_ui_has_both_concept_a_surfaces(package_assets):
    html = package_assets["index.html"]

    assert 'data-surface="popover"' in html
    assert 'data-surface="manager"' in html
    assert 'class="manager-titlebar"' not in html
    assert 'class="window-dots"' not in html
    for destination in ("overview", "accounts", "sync", "settings"):
        assert f'data-destination="{destination}"' in html


def test_assets_have_no_external_or_inline_runtime_code(package_assets):
    html = package_assets["index.html"]
    joined = "\n".join(package_assets.values())

    assert "https://" not in joined
    assert "http://" not in joined
    assert "<script>" not in html
    assert " style=" not in html
    assert "innerHTML" not in joined
    assert "outerHTML" not in joined
    assert "insertAdjacentHTML" not in joined


def test_public_claude_controls_are_non_actionable(package_assets):
    joined = "\n".join(package_assets.values())

    assert 'data-provider", "claude"' in joined
    assert 'data-policy-state", "disabled"' in joined
    assert "add-claude" not in joined
    assert 'provider: "claude"' not in package_assets["api-client.mjs"]


def test_static_markup_has_semantic_accessible_controls(package_assets):
    html = package_assets["index.html"]

    assert html.count("<h1") >= 2
    assert '<nav aria-label="주요 화면">' in html
    assert 'aria-label="새로고침"' in html
    assert 'aria-label="DotSync 종료"' in html
    assert '<dialog id="confirmation-dialog"' in html
    assert '<h2 id="confirmation-title"' in html
    assert 'value="cancel" autofocus' in html
    assert '<progress max="100"' in html
    assert "autofocus>Apply" not in html
    assert "autofocus>Delete" not in html


def test_styles_preserve_accessibility_and_compact_popover_contract(package_assets):
    styles = package_assets["styles.css"]

    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert ":focus-visible" in styles
    assert "outline:" in styles
    assert "@media (max-width: 320px)" in styles
    assert "width: 360px" in styles
    assert "min-height: 560px" in styles
    assert "#ff9f0a" in styles


def test_module_imports_are_local_and_entrypoint_is_external(package_assets):
    html = package_assets["index.html"]

    assert '<script type="module" src="/app.mjs"></script>' in html
    assert '<link rel="stylesheet" href="/styles.css">' in html
    for name in ("state.mjs", "api-client.mjs", "render.mjs", "app.mjs"):
        imports = re.findall(
            r'(?:from\s+|import\s*)["\']([^"\']+)["\']',
            package_assets[name],
        )
        assert all(value.startswith("./") for value in imports)


def test_server_exposes_only_fixed_packaged_static_routes():
    assert {
        path: resource.package_name for path, resource in _STATIC_ROUTES.items()
    } == {
        "/": "index.html",
        "/styles.css": "styles.css",
        "/state.mjs": "state.mjs",
        "/api-client.mjs": "api-client.mjs",
        "/render.mjs": "render.mjs",
        "/app.mjs": "app.mjs",
    }


def test_setuptools_packages_every_production_ui_asset():
    pyproject = tomllib.loads(
        (STATIC_ROOT.parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["tool"]["setuptools"]["package-data"]["dotsync.web"] == [
        "static/*.html",
        "static/*.css",
        "static/*.mjs",
    ]
