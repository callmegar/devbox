"""Repo layout auto-detection.

Figures out where the Python modules, frontend, and Terraform stacks live.
Tuned for the `match` monorepo layout but falls back to common conventions.
A `.devbox.toml` at the repo root can override (not yet wired — v2).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoLayout:
    python_root: Path | None = None              # dir containing the modules' pyproject.toml
    python_modules: list[str] = field(default_factory=list)  # module dir names
    frontend_path: Path | None = None            # dir with package.json
    terraform_root: Path | None = None           # dir containing stack subdirs


def detect_layout(repo_path: Path) -> RepoLayout:
    layout = RepoLayout()

    # Python: prefer backend/pyproject.toml, then repo-root pyproject.toml.
    for candidate in (repo_path / "backend", repo_path):
        pp = candidate / "pyproject.toml"
        if pp.exists():
            layout.python_root = candidate
            layout.python_modules = _python_modules(pp, candidate)
            break

    # Frontend: app/frontend, then frontend, then app.
    for candidate in (repo_path / "app" / "frontend", repo_path / "frontend", repo_path / "app"):
        if (candidate / "package.json").exists():
            layout.frontend_path = candidate
            break

    # Terraform: terraform, then infra, then infrastructure.
    for candidate in (repo_path / "terraform", repo_path / "infra", repo_path / "infrastructure"):
        if candidate.is_dir() and any(candidate.rglob("*.tf")):
            layout.terraform_root = candidate
            break

    return layout


def _python_modules(pyproject: Path, root: Path) -> list[str]:
    """Derive module names from setuptools packages.find.include, else __init__.py scan."""
    try:
        data = tomllib.loads(pyproject.read_text())
    except Exception:
        data = {}
    include = (
        data.get("tool", {}).get("setuptools", {})
        .get("packages", {}).get("find", {}).get("include", [])
    )
    modules: list[str] = []
    for pattern in include:
        name = pattern.rstrip("*")
        if name and (root / name).is_dir():
            modules.append(name)
    if not modules:
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "__init__.py").exists():
                modules.append(child.name)
    return sorted(set(modules))
