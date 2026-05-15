"""Enrichment extractor: map Alembic migrations to the modules that own them,
and to the Postgres schemas they touch.

backend/migrations/versions/*.py follow a `{date}_{revision}_{component}_{desc}`
naming convention. The component token isn't always the python module name —
e.g. `..._mls_initial_schema.py` belongs to the `mls_loader` module — so owner
attribution matches the component token against module names with a small
amount of prefix tolerance.

`match`'s migrations declare schemas as raw SQL inside `op.execute()` —
`CREATE SCHEMA IF NOT EXISTS workflows`, then `CREATE TABLE workflows.foo` —
not via the SQLAlchemy `schema=` kwarg. So we:
  pass 1: scan *every* migration for `CREATE SCHEMA` to learn the schema set;
  pass 2: attribute schema-qualified references (`<schema>.<table>`) to each
          owned migration's module — this also catches migrations that ALTER
          an existing schema without re-declaring it.
"""

from __future__ import annotations

import re

from ..model import KIND_PYTHON_MODULE

_CREATE_SCHEMA_RE = re.compile(
    r'CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?["\']?([a-z_][a-z0-9_]*)["\']?',
    re.I,
)
# Strips the `{date}_{revision}_` prefix to isolate the `{component}_{desc}` tail.
_PREFIX_RE = re.compile(r"^\d{6,}_\w+?_")


def _owner_for(stem: str, module_names: set[str]) -> str | None:
    """Attribute a migration filename to the module that owns it."""
    tail = _PREFIX_RE.sub("", stem, count=1)
    # 1. The tail begins with a full module name (longest match wins).
    for mod in sorted(module_names, key=len, reverse=True):
        if tail == mod or tail.startswith(mod + "_"):
            return mod
    # 2. Component alias: the tail's first token uniquely prefixes one module
    #    (e.g. `mls_initial_schema` -> `mls_loader`).
    first = tail.split("_", 1)[0]
    if first:
        candidates = {m for m in module_names if m.split("_", 1)[0] == first}
        if len(candidates) == 1:
            return next(iter(candidates))
    # 3. Legacy fallback: a module name embedded anywhere in the stem.
    for mod in sorted(module_names, key=len, reverse=True):
        if f"_{mod}_" in stem or stem.endswith(f"_{mod}"):
            return mod
    return None


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

    # Read every migration once. `all_texts` feeds schema discovery (a global
    # fact); `owned` feeds per-module attribution.
    all_texts: list[str] = []
    owned: list[tuple[str, str]] = []  # (owner module, file text)
    by_module: dict[str, list[str]] = {}
    for migration in sorted(versions.glob("*.py")):
        try:
            text = migration.read_text(errors="ignore")
        except Exception:
            continue
        all_texts.append(text)
        owner = _owner_for(migration.stem, module_names)
        if not owner:
            continue
        by_module.setdefault(owner, []).append(migration.name)
        owned.append((owner, text))

    # Pass 1: every schema declared by a `CREATE SCHEMA` statement, across all
    # migrations — the authoritative schema set for the system.
    known_schemas: set[str] = set()
    for text in all_texts:
        for m in _CREATE_SCHEMA_RE.finditer(text):
            known_schemas.add(m.group(1).lower())

    # Pass 2: attribute schema-qualified references to each migration's module.
    ref_res = {s: re.compile(rf"\b{re.escape(s)}\.", re.I) for s in known_schemas}
    schemas: dict[str, set[str]] = {}
    for owner, text in owned:
        for schema, rx in ref_res.items():
            if rx.search(text):
                schemas.setdefault(owner, set()).add(schema)

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
