"""Catalog extractors.

Each extractor exposes `extract(ctx) -> dict[str, dict]`:
  - Discovery extractors return new node dicts keyed by node name. Each value
    must include "name", "kind", "path".
  - Enrichment extractors return partial field dicts for existing node names;
    build.py merges them via CatalogNode.merge_fields.

ctx is an ExtractContext (repo_path, layout, nodes).
"""
