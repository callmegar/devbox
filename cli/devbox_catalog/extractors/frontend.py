"""Discovery extractor: the frontend (Next.js / React) app."""

from __future__ import annotations

import json

from ..model import KIND_FRONTEND

_SRC_EXTS = ("*.ts", "*.tsx", "*.js", "*.jsx")
_SKIP = {"node_modules", ".next", "dist", "build", ".turbo"}


def extract(ctx) -> dict[str, dict]:
    layout = ctx.layout
    if not layout.frontend_path:
        return {}

    pj = layout.frontend_path / "package.json"
    try:
        data = json.loads(pj.read_text(errors="ignore"))
    except Exception:
        return {}

    deps = data.get("dependencies", {}) or {}
    dev_deps = data.get("devDependencies", {}) or {}
    if "next" in deps:
        framework = f"next@{deps['next']}"
    elif "react" in deps:
        framework = f"react@{deps['react']}"
    else:
        framework = "unknown"

    loc = 0
    src_files = 0
    for ext in _SRC_EXTS:
        for f in layout.frontend_path.rglob(ext):
            if _SKIP & set(f.parts):
                continue
            try:
                loc += f.read_text(errors="ignore").count("\n") + 1
                src_files += 1
            except Exception:
                pass

    return {
        "frontend": {
            "name": "frontend",
            "kind": KIND_FRONTEND,
            "path": str(layout.frontend_path.relative_to(ctx.repo_path)),
            "external_deps": sorted(deps.keys()),
            "loc": loc,
            "extra": {
                "framework": framework,
                "package_name": data.get("name", ""),
                "scripts": sorted((data.get("scripts") or {}).keys()),
                "dev_deps": sorted(dev_deps.keys()),
                "src_files": src_files,
            },
        }
    }
