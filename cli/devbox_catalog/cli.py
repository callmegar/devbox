"""devbox CLI — build and query the system-comprehension catalog.

  devbox catalog build [--repo PATH]      build + cache the catalog
  devbox catalog show [NODE] [--repo P]   summary, or one node's detail
  devbox catalog graph [--repo PATH]      emit a mermaid dependency graph
  devbox catalog deps NODE [--repo PATH]  forward + reverse dependencies

Catalogs are cached under ~/.devbox/catalog/<repo>.json. show/graph/deps
build on demand if no cache exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import build_catalog
from .model import Catalog, CatalogNode

CACHE_DIR = Path.home() / ".devbox" / "catalog"

_KIND_LABEL = {
    "python_module": "py",
    "frontend": "fe",
    "terraform_stack": "tf",
}


def _cache_path(repo_path: Path) -> Path:
    return CACHE_DIR / f"{repo_path.resolve().name}.json"


def _load_or_build(repo: str) -> Catalog:
    repo_path = Path(repo).resolve()
    cache = _cache_path(repo_path)
    if cache.exists():
        return _catalog_from_dict(json.loads(cache.read_text()))
    catalog = build_catalog(repo_path)
    _write_cache(catalog)
    return catalog


def _catalog_from_dict(data: dict) -> Catalog:
    catalog = Catalog(
        repo=data["repo"],
        repo_path=data["repo_path"],
        generated_at=data["generated_at"],
        warnings=data.get("warnings", []),
    )
    for name, node in data.get("nodes", {}).items():
        catalog.nodes[name] = CatalogNode(**node)
    return catalog


def _write_cache(catalog: Catalog) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = _cache_path(Path(catalog.repo_path))
    out.write_text(json.dumps(catalog.to_dict(), indent=2))
    return out


def cmd_build(args) -> int:
    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"error: {repo_path} is not a directory", file=sys.stderr)
        return 1
    catalog = build_catalog(repo_path)
    out = Path(args.out).resolve() if args.out else _cache_path(repo_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog.to_dict(), indent=2))

    kinds: dict[str, int] = {}
    for node in catalog.nodes.values():
        kinds[node.kind] = kinds.get(node.kind, 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    print(f"catalog: {len(catalog.nodes)} nodes ({summary})")
    print(f"written: {out}")
    if catalog.warnings:
        print(f"\n{len(catalog.warnings)} warning(s):")
        for w in catalog.warnings:
            print(f"  - {w}")
    return 0


def cmd_show(args) -> int:
    catalog = _load_or_build(args.repo)
    if args.node:
        node = catalog.nodes.get(args.node)
        if not node:
            print(f"error: no node '{args.node}'. known: {', '.join(sorted(catalog.nodes))}",
                  file=sys.stderr)
            return 1
        print(f"{node.name}  [{node.kind}]  {node.path}")
        if node.purpose:
            print(f"  purpose:    {node.purpose}")
        if node.depends_on:
            print(f"  depends_on: {', '.join(node.depends_on)}")
        if node.dependents:
            print(f"  dependents: {', '.join(node.dependents)}")
        if node.loc:
            print(f"  loc:        {node.loc}")
        if node.claude_md:
            print(f"  claude_md:  {node.claude_md}")
        if node.external_deps:
            shown = ", ".join(node.external_deps[:12])
            more = f" (+{len(node.external_deps) - 12} more)" if len(node.external_deps) > 12 else ""
            print(f"  ext_deps:   {shown}{more}")
        for key, val in sorted(node.extra.items()):
            print(f"  {key}: {val}")
        return 0

    # Summary table
    print(f"{catalog.repo}  —  {len(catalog.nodes)} nodes  (generated {catalog.generated_at})")
    print()
    width = max((len(n) for n in catalog.nodes), default=4)
    for name, node in sorted(catalog.nodes.items()):
        label = _KIND_LABEL.get(node.kind, node.kind)
        deps = f"->[{','.join(node.depends_on)}]" if node.depends_on else ""
        print(f"  {label:3} {name:<{width}}  {node.purpose[:60]:<60} {deps}")
    if catalog.warnings:
        print(f"\n{len(catalog.warnings)} warning(s) — run `devbox catalog build` to see them")
    return 0


def cmd_graph(args) -> int:
    catalog = _load_or_build(args.repo)
    print("```mermaid")
    print("graph LR")
    for name, node in sorted(catalog.nodes.items()):
        node_id = name.replace(":", "_").replace("-", "_")
        print(f'  {node_id}["{name}"]')
    for name, node in sorted(catalog.nodes.items()):
        node_id = name.replace(":", "_").replace("-", "_")
        for dep in node.depends_on:
            dep_id = dep.replace(":", "_").replace("-", "_")
            print(f"  {node_id} --> {dep_id}")
    print("```")
    return 0


def cmd_deps(args) -> int:
    catalog = _load_or_build(args.repo)
    node = catalog.nodes.get(args.node)
    if not node:
        print(f"error: no node '{args.node}'. known: {', '.join(sorted(catalog.nodes))}",
              file=sys.stderr)
        return 1
    print(f"{node.name}")
    print(f"  depends on  ({len(node.depends_on)}): {', '.join(node.depends_on) or '-'}")
    print(f"  depended by ({len(node.dependents)}): {', '.join(node.dependents) or '-'}")
    # transitive dependents — the blast radius of a change to this node
    seen: set[str] = set()
    frontier = list(node.dependents)
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        frontier.extend(catalog.nodes[cur].dependents)
    if seen - set(node.dependents):
        print(f"  blast radius ({len(seen)}): {', '.join(sorted(seen))}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="devbox", description="devbox system catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    catalog_p = sub.add_parser("catalog", help="build and query the system catalog")
    csub = catalog_p.add_subparsers(dest="subcmd", required=True)

    b = csub.add_parser("build", help="build + cache the catalog")
    b.add_argument("--repo", default=".", help="repo path (default: cwd)")
    b.add_argument("--out", default=None, help="output json path (default: ~/.devbox/catalog/<repo>.json)")
    b.set_defaults(func=cmd_build)

    s = csub.add_parser("show", help="show catalog summary or a node")
    s.add_argument("node", nargs="?", help="node name (omit for summary)")
    s.add_argument("--repo", default=".")
    s.set_defaults(func=cmd_show)

    g = csub.add_parser("graph", help="emit a mermaid dependency graph")
    g.add_argument("--repo", default=".")
    g.set_defaults(func=cmd_graph)

    d = csub.add_parser("deps", help="forward + reverse + transitive deps of a node")
    d.add_argument("node")
    d.add_argument("--repo", default=".")
    d.set_defaults(func=cmd_deps)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
