"""Catalog build orchestrator.

Two phases:
  1. Discovery — extractors that create nodes (python modules, frontend, tf stacks)
  2. Enrichment — extractors that add fields to existing nodes

Each extractor is isolated: a failure is recorded as a warning, not fatal.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from .config import RepoLayout, detect_layout
from .model import Catalog, CatalogNode
from .extractors import (
    alembic_schema,
    claude_md,
    frontend,
    gitlab_ci,
    pyproject_deps,
    python_modules,
    terraform_stacks,
)

DISCOVERY = [python_modules, frontend, terraform_stacks]
ENRICHMENT = [claude_md, pyproject_deps, gitlab_ci, alembic_schema]


@dataclass
class ExtractContext:
    repo_path: Path
    layout: RepoLayout
    nodes: dict[str, CatalogNode]


def build_catalog(repo_path: Path) -> Catalog:
    repo_path = repo_path.resolve()
    layout = detect_layout(repo_path)
    catalog = Catalog(
        repo=repo_path.name,
        repo_path=str(repo_path),
        generated_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    ctx = ExtractContext(repo_path, layout, catalog.nodes)

    for extractor in DISCOVERY:
        name = extractor.__name__.rsplit(".", 1)[-1]
        try:
            for node_name, fields in extractor.extract(ctx).items():
                catalog.nodes[node_name] = CatalogNode(
                    name=fields["name"],
                    kind=fields["kind"],
                    path=fields["path"],
                    depends_on=list(fields.get("depends_on", [])),
                    external_deps=list(fields.get("external_deps", [])),
                    loc=fields.get("loc", 0),
                    extra=dict(fields.get("extra", {})),
                )
        except Exception as exc:  # noqa: BLE001 — extractor isolation is intentional
            catalog.warnings.append(f"discovery/{name}: {exc!r}")

    for extractor in ENRICHMENT:
        name = extractor.__name__.rsplit(".", 1)[-1]
        try:
            for node_name, fields in extractor.extract(ctx).items():
                if node_name in catalog.nodes:
                    catalog.nodes[node_name].merge_fields(fields)
        except Exception as exc:  # noqa: BLE001
            catalog.warnings.append(f"enrichment/{name}: {exc!r}")

    catalog.compute_reverse_edges()

    if not catalog.nodes:
        catalog.warnings.append(
            "no nodes discovered — is the repo layout recognised? "
            "expected backend/pyproject.toml, app/frontend/package.json, or terraform/"
        )
    return catalog
