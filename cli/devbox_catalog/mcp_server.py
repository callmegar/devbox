"""devbox-catalog MCP server — exposes the system catalog over stdio MCP.

Tools:
  catalog_overview      summary of every node (name, kind, purpose, dep counts)
  get_node              full detail for a single node
  find_dependents       direct reverse-deps of a node
  impact_of_change      transitive blast radius (direct + transitive dependents)
  find_node_for_file    locate the node that owns a repo-relative path
  db_schema_map         inverted {schema: [modules that touch it]}

The server uses the cached catalog at ~/.devbox/catalog/<repo>.json, building
on demand if no cache exists. `repo` defaults to the spawn-time CWD, which
matches how Claude Code launches project-scoped MCP servers from .mcp.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .catalog_io import load_or_build

mcp = FastMCP("devbox-catalog")


def _node_summary(node) -> dict[str, Any]:
    return {
        "name": node.name,
        "kind": node.kind,
        "path": node.path,
        "purpose": node.purpose,
        "depends_on_count": len(node.depends_on),
        "dependents_count": len(node.dependents),
    }


def _node_detail(node) -> dict[str, Any]:
    return {
        "name": node.name,
        "kind": node.kind,
        "path": node.path,
        "purpose": node.purpose,
        "depends_on": list(node.depends_on),
        "dependents": list(node.dependents),
        "external_deps": list(node.external_deps),
        "loc": node.loc,
        "claude_md": node.claude_md,
        "extra": dict(node.extra),
    }


def _require_node(catalog, name: str):
    node = catalog.nodes.get(name)
    if not node:
        known = ", ".join(sorted(catalog.nodes))
        raise ValueError(f"no node '{name}'. known: {known}")
    return node


@mcp.tool()
def catalog_overview(repo: str = ".") -> dict[str, Any]:
    """Bird's-eye view of the system catalog: every node's name, kind, purpose,
    and dependency counts. Use this to orient yourself before drilling into a
    specific node with get_node. The catalog is built on demand if no cache
    exists.
    """
    catalog = load_or_build(repo)
    nodes = sorted(
        (_node_summary(n) for n in catalog.nodes.values()),
        key=lambda d: d["name"],
    )
    return {
        "repo": catalog.repo,
        "generated_at": catalog.generated_at,
        "node_count": len(nodes),
        "warnings": list(catalog.warnings),
        "nodes": nodes,
    }


@mcp.tool()
def get_node(name: str, repo: str = ".") -> dict[str, Any]:
    """Full details for one catalog node — path, purpose, depends_on,
    dependents, external_deps, loc, claude_md, and kind-specific `extra`
    fields (e.g. db_schemas, has_migrations, has_dockerfile, framework,
    ci_jobs). Raises ValueError if no such node exists.
    """
    catalog = load_or_build(repo)
    return _node_detail(_require_node(catalog, name))


@mcp.tool()
def find_dependents(name: str, repo: str = ".") -> list[str]:
    """Direct reverse-dependents of a node — every node that `depends_on`
    this one. For the full transitive set, use impact_of_change.
    """
    catalog = load_or_build(repo)
    return list(_require_node(catalog, name).dependents)


@mcp.tool()
def impact_of_change(name: str, repo: str = ".") -> dict[str, Any]:
    """Blast radius of changing a node — every node reachable via
    reverse-dependency edges, including transitively. Returns the direct
    dependents and the full transitive set as a sorted list.
    """
    catalog = load_or_build(repo)
    node = _require_node(catalog, name)
    seen: set[str] = set()
    frontier = list(node.dependents)
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        frontier.extend(catalog.nodes[cur].dependents)
    return {
        "node": name,
        "direct_dependents": list(node.dependents),
        "transitive_dependents": sorted(seen),
        "blast_radius_size": len(seen),
    }


@mcp.tool()
def find_node_for_file(path: str, repo: str = ".") -> dict[str, Any] | None:
    """Given a repo-relative (or absolute, inside-repo) file path, return the
    catalog node that owns it — the node whose path is the longest directory
    prefix of the input. Returns null when no node matches (file is outside
    any tracked module). Useful before editing: "what will this change affect?"
    """
    catalog = load_or_build(repo)
    repo_path = Path(catalog.repo_path).resolve()
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(repo_path)
        except ValueError:
            return None
    parts = p.parts
    best: tuple[int, str] | None = None
    for name, node in catalog.nodes.items():
        node_parts = Path(node.path).parts
        if len(node_parts) > len(parts):
            continue
        if parts[: len(node_parts)] == node_parts:
            score = len(node_parts)
            if best is None or score > best[0]:
                best = (score, name)
    if best is None:
        return None
    return {"node": best[1], "matched_path": catalog.nodes[best[1]].path}


@mcp.tool()
def db_schema_map(repo: str = ".") -> dict[str, list[str]]:
    """Inverted index of Postgres schemas → modules that touch them, derived
    from each module's Alembic migrations. Useful for "who owns schema X?"
    and cross-component impact analysis on DB changes.
    """
    catalog = load_or_build(repo)
    inv: dict[str, set[str]] = {}
    for name, node in catalog.nodes.items():
        for schema in node.extra.get("db_schemas", []) or []:
            inv.setdefault(schema, set()).add(name)
    return {schema: sorted(modules) for schema, modules in sorted(inv.items())}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
