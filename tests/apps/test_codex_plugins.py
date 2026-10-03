import json
from pathlib import Path

import pytest

from dotsync.apps.codex_plugins import (
    build_manifest,
    manifest_text,
    merge_plugin_tables,
    parse_manifest,
    split_plugin_tables,
)

CONFIG = """\
model = "gpt-5.5"

[mcp_servers.context7]
command = "npx"

[marketplaces.openai-bundled]
source_type = "local"
source = "/home/u/.codex/.tmp/bundled-marketplaces/openai-bundled"

[marketplaces.my-market]
last_updated = "2026-08-15T16:53:43Z"
last_revision = "737d42b"
source_type = "git"
source = "https://github.com/me/my-market.git"
ref = "main"

[plugins."mine@my-market"]
enabled = true

[tui]
theme = "dark"
"""

PORTABLE = """\
model = "gpt-5.5"

[mcp_servers.context7]
command = "npx"

[tui]
theme = "dark"
"""

TABLES = """\
[marketplaces.openai-bundled]
source_type = "local"
source = "/home/u/.codex/.tmp/bundled-marketplaces/openai-bundled"

[marketplaces.my-market]
last_updated = "2026-08-15T16:53:43Z"
last_revision = "737d42b"
source_type = "git"
source = "https://github.com/me/my-market.git"
ref = "main"

[plugins."mine@my-market"]
enabled = true

"""

ROOTS = (Path("/home/u/.codex"), Path("/home/u/.cache/codex-runtimes"))


def _installed(plugin_id, *, policy="AVAILABLE", enabled=True, source=None):
    item = {
        "pluginId": plugin_id,
        "installPolicy": policy,
        "enabled": enabled,
        "installed": True,
    }
    if source is not None:
        item["marketplaceSource"] = source
    return item


def test_split_plugin_tables_separates_marketplace_and_plugin_tables():
    portable, tables = split_plugin_tables(CONFIG)

    assert portable == PORTABLE
    assert tables == TABLES


def test_split_plugin_tables_leaves_config_without_them_unchanged():
    assert split_plugin_tables(PORTABLE) == (PORTABLE, "")


def test_merge_plugin_tables_takes_settings_from_stored_and_tables_from_local():
    stored = 'model = "o3"\n'

    merged = merge_plugin_tables(stored, CONFIG)

    assert merged == 'model = "o3"\n\n' + TABLES.rstrip("\n") + "\n"


def test_merge_plugin_tables_without_local_tables_is_the_stored_settings():
    assert merge_plugin_tables(CONFIG, 'model = "o3"\n') == PORTABLE


def test_build_manifest_records_only_what_the_user_installed():
    bundled = {"sourceType": "local", "source": "/home/u/.codex/.tmp/bundled-marketplaces/openai-bundled"}
    runtime = {
        "sourceType": "local",
        "source": "/home/u/.cache/codex-runtimes/runtime/plugins/openai-primary-runtime",
    }
    git = {"sourceType": "git", "source": "https://github.com/me/my-market.git"}
    installed = [
        _installed("mine@my-market", source=git),
        _installed("superpowers@openai-curated-remote"),
        _installed("pages@openai-curated-remote", policy="INSTALLED_BY_DEFAULT"),
        _installed("browser@openai-bundled", source=bundled),
        _installed("pdf@openai-primary-runtime", source=runtime),
        _installed("off@my-market", source=git, enabled=False),
    ]

    manifest = build_manifest(installed, CONFIG, ROOTS)

    assert manifest == {
        "marketplaces": [
            {
                "name": "my-market",
                "source": "https://github.com/me/my-market.git",
                "ref": "main",
            }
        ],
        "plugins": ["mine@my-market", "superpowers@openai-curated-remote"],
    }


def test_manifest_text_round_trips_through_parse_manifest():
    manifest = {
        "marketplaces": [
            {"name": "my-market", "source": "me/my-market", "ref": "main", "sparse": ["p"]}
        ],
        "plugins": ["mine@my-market"],
    }

    text = manifest_text(manifest)

    assert text.endswith("\n")
    assert parse_manifest(text) == manifest


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"plugins": "mine@my-market", "marketplaces": []},
        {"plugins": ["no-marketplace"], "marketplaces": []},
        {"plugins": [], "marketplaces": [{"name": "m"}]},
        {"plugins": [], "marketplaces": [{"name": "m", "source": "s", "extra": 1}]},
        {"plugins": [], "marketplaces": [], "unknown": 1},
    ],
)
def test_parse_manifest_rejects_malformed_documents(doc):
    with pytest.raises(ValueError, match="plugins.json"):
        parse_manifest(json.dumps(doc))
