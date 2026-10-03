"""Codex plugins: record what the user installed, leave Codex's own to Codex.

`config.toml` carries `[marketplaces.*]` / `[plugins.*]` tables that Codex
rewrites by itself: local paths of the marketplaces it bundles, plus
`last_updated` / `last_revision`. dotsync stores the rest of the file and
records the user's plugins in `plugins.json`; `apply` reinstalls them with
the Codex CLI and keeps the local tables Codex wrote.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping

from dotsync.apps.mcp_sanitizer import toml_table_name

PLUGIN_TABLE_ROOTS = ("marketplaces", "plugins")
INSTALLED_BY_DEFAULT = "INSTALLED_BY_DEFAULT"
_MARKETPLACE_KEYS = ("name", "source", "ref", "sparse")


def split_plugin_tables(text: str) -> tuple[str, str]:
    """Return (config without marketplace/plugin tables, those tables)."""
    rest: list[str] = []
    tables: list[str] = []
    target = rest
    for line in text.splitlines(keepends=True):
        table = toml_table_name(line)
        if table is not None:
            target = tables if table.split(".", 1)[0] in PLUGIN_TABLE_ROOTS else rest
        target.append(line)
    settings = "".join(rest)
    if tables:
        while settings.endswith("\n\n"):
            settings = settings[:-1]
    return settings, "".join(tables)


def merge_plugin_tables(stored: str, local: str) -> str:
    """Settings from the stored config, marketplace/plugin tables from local."""
    settings, _ = split_plugin_tables(stored)
    _, tables = split_plugin_tables(local)
    if not tables:
        return settings
    tables = tables.rstrip("\n") + "\n"
    if not settings.strip():
        return tables
    return settings.rstrip("\n") + "\n\n" + tables


def build_manifest(
    installed: Iterable[Mapping[str, Any]],
    config_text: str,
    codex_roots: Iterable[Path],
) -> dict[str, list]:
    """Pick the user's plugins out of `codex plugin list --json` "installed".

    Skipped: disabled plugins, plugins Codex installs by default, and plugins
    from marketplaces Codex keeps under its own directories (bundled ones).
    Marketplaces come from config.toml, minus those same Codex-owned ones.
    """
    roots = tuple(codex_roots)
    # Validate every entry before filtering: a malformed one must fail loudly,
    # not be dropped as if it were a disabled plugin.
    entries = [(plugin_id(item), item) for item in installed]
    plugins = sorted(
        selector
        for selector, item in entries
        if item.get("enabled") is True
        and item.get("installPolicy") != INSTALLED_BY_DEFAULT
        and not _is_codex_owned(item.get("marketplaceSource"), roots)
    )
    try:
        tables = tomllib.loads(config_text).get("marketplaces", {})
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid config.toml: {exc}") from exc
    marketplaces = []
    for name, table in sorted(tables.items()):
        source = {"sourceType": table.get("source_type"), "source": table.get("source")}
        if not isinstance(table.get("source"), str) or _is_codex_owned(source, roots):
            continue
        entry: dict[str, Any] = {"name": name, "source": table["source"]}
        for key in ("ref", "sparse"):
            if key in table:
                entry[key] = table[key]
        marketplaces.append(entry)
    return {"marketplaces": marketplaces, "plugins": plugins}


def manifest_text(manifest: Mapping[str, Any]) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def parse_manifest(text: str) -> dict[str, list]:
    """Validate a stored plugins.json; raise ValueError naming the problem."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid plugins.json: {exc}") from exc
    if not isinstance(doc, dict) or set(doc) != {"marketplaces", "plugins"}:
        raise ValueError("invalid plugins.json: expected marketplaces and plugins")
    plugins = doc["plugins"]
    if not isinstance(plugins, list) or not all(_is_selector(p) for p in plugins):
        raise ValueError("invalid plugins.json: plugins must be plugin@marketplace")
    marketplaces = doc["marketplaces"]
    if not isinstance(marketplaces, list) or not all(
        _is_marketplace(mp) for mp in marketplaces
    ):
        raise ValueError(
            "invalid plugins.json: marketplaces need name and source strings, "
            "optional ref string and sparse list"
        )
    return doc


def plugin_id(item: Mapping[str, Any]) -> str:
    """`plugin@marketplace` of one `codex plugin list --json` entry."""
    selector = item.get("pluginId")
    name, marketplace = item.get("name"), item.get("marketplaceName")
    if selector is None and isinstance(name, str) and isinstance(marketplace, str):
        selector = f"{name}@{marketplace}"
    if not _is_selector(selector):
        raise ValueError(f"unexpected codex plugin list entry: {dict(item)!r}")
    return selector


def _is_codex_owned(source: object, roots: tuple[Path, ...]) -> bool:
    if not isinstance(source, Mapping) or source.get("sourceType") != "local":
        return False
    path = source.get("source")
    return isinstance(path, str) and any(
        Path(path).is_relative_to(root) for root in roots
    )


def _is_selector(value: object) -> bool:
    if not isinstance(value, str) or value.count("@") != 1:
        return False
    name, marketplace = value.split("@")
    return bool(name and marketplace) and not any(ch.isspace() for ch in value)


def _is_marketplace(value: object) -> bool:
    if not isinstance(value, dict) or not set(value) <= set(_MARKETPLACE_KEYS):
        return False
    if not all(isinstance(value.get(k), str) and value[k].strip() for k in ("name", "source")):
        return False
    if "ref" in value and not isinstance(value["ref"], str):
        return False
    sparse = value.get("sparse", [])
    return isinstance(sparse, list) and all(
        isinstance(p, str) and p.strip() for p in sparse
    )
