"""Catalog load / cache / serialize helpers shared by the CLI and the MCP server.

Catalogs are JSON-cached under `~/.devbox/catalog/<repo>.json`. Both the
`devbox catalog` CLI commands and the MCP server build on demand if no cache
exists, so a fresh box (or a fresh repo) doesn't need an explicit pre-build.
"""

from __future__ import annotations

import json
from pathlib import Path

from .build import build_catalog
from .model import Catalog, CatalogNode

CACHE_DIR = Path.home() / ".devbox" / "catalog"


def cache_path(repo_path: Path) -> Path:
    return CACHE_DIR / f"{repo_path.resolve().name}.json"


def catalog_from_dict(data: dict) -> Catalog:
    catalog = Catalog(
        repo=data["repo"],
        repo_path=data["repo_path"],
        generated_at=data["generated_at"],
        warnings=data.get("warnings", []),
    )
    for name, node in data.get("nodes", {}).items():
        catalog.nodes[name] = CatalogNode(**node)
    return catalog


def write_cache(catalog: Catalog) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = cache_path(Path(catalog.repo_path))
    out.write_text(json.dumps(catalog.to_dict(), indent=2))
    return out


def load_or_build(repo: str | Path) -> Catalog:
    """Return the cached catalog for `repo`, building + caching it if absent."""
    repo_path = Path(repo).resolve()
    cache = cache_path(repo_path)
    if cache.exists():
        return catalog_from_dict(json.loads(cache.read_text()))
    catalog = build_catalog(repo_path)
    write_cache(catalog)
    return catalog
