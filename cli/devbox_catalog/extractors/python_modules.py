"""Discovery extractor: Python backend modules + inter-module import graph.

Walks each module's .py files, AST-parses imports, and records an edge when
one module imports from another. Also captures LOC and Dockerfile presence.
"""

from __future__ import annotations

import ast

from ..model import KIND_PYTHON_MODULE

_SKIP_DIRS = {"scratch", "tests", "__pycache__", ".venv", "node_modules"}


def extract(ctx) -> dict[str, dict]:
    layout = ctx.layout
    if not layout.python_root or not layout.python_modules:
        return {}

    root = layout.python_root
    module_set = set(layout.python_modules)
    out: dict[str, dict] = {}

    for mod in layout.python_modules:
        mod_dir = root / mod
        if not mod_dir.is_dir():
            continue
        py_files = [
            p for p in mod_dir.rglob("*.py")
            if not (_SKIP_DIRS & set(p.relative_to(mod_dir).parts))
        ]
        loc = 0
        deps: set[str] = set()
        for pf in py_files:
            try:
                src = pf.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            loc += src.count("\n") + 1
            try:
                tree = ast.parse(src, filename=str(pf))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import — stays within the module
                        continue
                    top = (node.module or "").split(".")[0]
                    if top in module_set and top != mod:
                        deps.add(top)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in module_set and top != mod:
                            deps.add(top)

        out[mod] = {
            "name": mod,
            "kind": KIND_PYTHON_MODULE,
            "path": str(mod_dir.relative_to(ctx.repo_path)),
            "depends_on": sorted(deps),
            "loc": loc,
            "extra": {
                "has_dockerfile": (mod_dir / "Dockerfile").exists(),
                "py_files": len(py_files),
            },
        }
    return out
