"""Enrichment extractor: GitLab CI component change-triggers.

Parses .gitlab-ci.yml, finds each job's `changes:` path globs (from rules /
only), and attaches to each catalog node the jobs whose triggers point at
that node's path. This surfaces the CI-encoded component boundaries.
"""

from __future__ import annotations

import yaml


def _collect_changes(job: dict) -> list[str]:
    out: list[str] = []
    rules = job.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            ch = rule.get("changes")
            if isinstance(ch, list):
                out += [c for c in ch if isinstance(c, str)]
            elif isinstance(ch, dict) and isinstance(ch.get("paths"), list):
                out += [c for c in ch["paths"] if isinstance(c, str)]
    only = job.get("only")
    if isinstance(only, dict) and isinstance(only.get("changes"), list):
        out += [c for c in only["changes"] if isinstance(c, str)]
    return out


def _is_job(val) -> bool:
    return isinstance(val, dict) and any(
        k in val for k in ("script", "extends", "trigger", "stage")
    )


def extract(ctx) -> dict[str, dict]:
    gitlab_ci = ctx.repo_path / ".gitlab-ci.yml"
    if not gitlab_ci.exists():
        return {}
    try:
        data = yaml.safe_load(gitlab_ci.read_text(errors="ignore"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    # job name -> list of change-path globs
    jobs: dict[str, list[str]] = {}
    for key, val in data.items():
        if key.startswith(".") or not _is_job(val):
            continue
        changes = _collect_changes(val)
        if changes:
            jobs[key] = changes

    enrich: dict[str, dict] = {}
    for name, node in ctx.nodes.items():
        node_path = node.path.rstrip("/")
        matched_jobs: set[str] = set()
        triggers: set[str] = set()
        for job, globs in jobs.items():
            for glob in globs:
                g = glob.rstrip("/").lstrip("./")
                if g.startswith(node_path) or node_path in g:
                    matched_jobs.add(job)
                    triggers.add(glob)
        if matched_jobs:
            enrich[name] = {
                "extra": {
                    "ci_jobs": sorted(matched_jobs),
                    "ci_triggers": sorted(triggers),
                }
            }
    return enrich
