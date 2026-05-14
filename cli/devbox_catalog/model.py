"""Catalog data model.

A catalog is a graph of nodes. Each node is a coherent unit of the system —
a Python backend module, the frontend, or a Terraform stack. Edges are
dependency relationships derived by the extractors.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# Node kinds. Each extractor either *discovers* nodes of a kind or *enriches*
# existing nodes with more fields.
KIND_PYTHON_MODULE = "python_module"
KIND_FRONTEND = "frontend"
KIND_TERRAFORM_STACK = "terraform_stack"


@dataclass
class CatalogNode:
    name: str                                    # unique key, e.g. "consumer_api", "frontend", "tf:ecs"
    kind: str                                    # one of KIND_*
    path: str                                    # path relative to repo root
    purpose: str = ""                            # harvested from CLAUDE.md / docs
    depends_on: list[str] = field(default_factory=list)   # node names this node depends on
    dependents: list[str] = field(default_factory=list)   # reverse edges (filled by build)
    external_deps: list[str] = field(default_factory=list)  # third-party packages
    claude_md: str | None = None                 # path to the node's CLAUDE.md, if any
    loc: int = 0                                 # rough lines-of-code
    extra: dict[str, Any] = field(default_factory=dict)   # kind-specific fields

    def merge_fields(self, fields: dict[str, Any]) -> None:
        """Merge an enrichment extractor's output into this node.

        List fields are extended + deduped; scalars overwrite only if currently
        empty; `extra` is shallow-merged.
        """
        for key, value in fields.items():
            if key == "extra":
                self.extra.update(value)
            elif key in ("depends_on", "dependents", "external_deps"):
                current = getattr(self, key)
                for item in value:
                    if item not in current:
                        current.append(item)
            elif key in ("purpose", "claude_md") and not getattr(self, key):
                setattr(self, key, value)
            elif key == "loc" and value:
                self.loc = value
            elif hasattr(self, key) and key not in ("name", "kind", "path"):
                setattr(self, key, value)


@dataclass
class Catalog:
    repo: str
    repo_path: str
    generated_at: str
    nodes: dict[str, CatalogNode] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "repo_path": self.repo_path,
            "generated_at": self.generated_at,
            "nodes": {name: asdict(node) for name, node in sorted(self.nodes.items())},
            "warnings": self.warnings,
        }

    def compute_reverse_edges(self) -> None:
        """Populate `dependents` from `depends_on` across all nodes."""
        for node in self.nodes.values():
            node.dependents = []
        for name, node in self.nodes.items():
            for dep in node.depends_on:
                if dep in self.nodes and name not in self.nodes[dep].dependents:
                    self.nodes[dep].dependents.append(name)
        for node in self.nodes.values():
            node.dependents.sort()
