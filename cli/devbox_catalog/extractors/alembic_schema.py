"""Enrichment extractor: map Alembic migrations to the modules that own them.

backend/migrations/versions/*.py follow a `{date}_{revision}_{component}_{desc}`
naming convention. We attribute each migration file to a module by matching
the module name in the filename, and pull DB schema names from `schema=`
references in the migration body.
"""

from __future__ import annotations

import re

from ..model import KIND_PYTHON_MODULE

_SCHEMA_RE = re.compile(r'schema\s*=\s*[\'"]([a-z_][a-z0-9_]*)[\'"]')


def extract(ctx) -> dict[str, dict]:
    layout = ctx.layout
    if not layout.python_root:
        return {}
    versions = layout.python_root / "migrations" / "versions"
    if not versions.is_dir():
        return {}

    module_names = {
        name for name, node in ctx.nodes.items()
        if node.kind == KIND_PYTHON_MODULE
    }

    by_module: dict[str, list[str]] = {}
    schemas: dict[str, set[str]] = {}

    for migration in sorted(versions.glob("*.py")):
        stem = migration.stem
        owner = None
        # Longest module-name match wins (avoids 'noise' matching inside others).
        for mod in sorted(module_names, key=len, reverse=True):
            if f"_{mod}_" in stem or stem.endswith(f"_{mod}"):
                owner = mod
                break
        if not owner:
            continue
        by_module.setdefault(owner, []).append(migration.name)
        try:
            text = migration.read_text(errors="ignore")
        except Exception:
            continue
        for m in _SCHEMA_RE.finditer(text):
            schemas.setdefault(owner, set()).add(m.group(1))

    enrich: dict[str, dict] = {}
    for mod, files in by_module.items():
        enrich[mod] = {
            "extra": {
                "has_migrations": True,
                "migration_count": len(files),
                "db_schemas": sorted(schemas.get(mod, set())),
            }
        }
    return enrich
