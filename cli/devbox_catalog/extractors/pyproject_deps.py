"""Enrichment extractor: external dependencies per Python module.

The target backend uses one pyproject.toml. [project].dependencies are shared
by every module; [dependency-groups] whose name matches a module are that
module's extra deps (e.g. `training`, `mls_loader`, `pois`, `noise` groups —
substitute your own module names).
"""

from __future__ import annotations

import re
import tomllib

from ..model import KIND_PYTHON_MODULE

_PKG_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


def _names(dep_list) -> list[str]:
    out = []
    for dep in dep_list or []:
        if not isinstance(dep, str):
            continue
        m = _PKG_NAME_RE.match(dep)
        if m:
            out.append(m.group(0))
    return out


def extract(ctx) -> dict[str, dict]:
    layout = ctx.layout
    if not layout.python_root:
        return {}
    pyproject = layout.python_root / "pyproject.toml"
    if not pyproject.exists():
        return {}
    try:
        data = tomllib.loads(pyproject.read_text())
    except Exception:
        return {}

    base = _names(data.get("project", {}).get("dependencies", []))
    groups = data.get("dependency-groups", {}) or {}

    enrich: dict[str, dict] = {}
    for name, node in ctx.nodes.items():
        if node.kind != KIND_PYTHON_MODULE:
            continue
        deps = list(base)
        matched_groups = []
        for gname, gdeps in groups.items():
            # group matches a module if named exactly or module-prefixed
            if gname == name or gname.startswith(name + "_"):
                deps += _names(gdeps)
                matched_groups.append(gname)
        enrich[name] = {
            "external_deps": sorted(set(deps)),
            "extra": {"dependency_groups": matched_groups},
        }
    return enrich
