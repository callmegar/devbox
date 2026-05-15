"""Enrichment extractor: harvest purpose text from CLAUDE.md / README.md files.

The `match` repo keeps a cascading CLAUDE.md per backend module — that's the
hand-authored semantic layer. The terraform stacks have no per-stack CLAUDE.md;
instead `terraform/CLAUDE.md` carries a `| Directory | Purpose |` table. We
harvest a one-line purpose per node, in priority order:

  1. the node's own CLAUDE.md (Purpose/Overview section, else first prose line)
  2. a parent table — backend/CLAUDE.md's module table, or terraform/CLAUDE.md's
     directory table (keyed by the `tf:` node's directory name)
  3. the node's own README.md
"""

from __future__ import annotations

import re

_SECTION_RE = re.compile(r"^#+\s+(Purpose|Overview|Project Overview)\s*$", re.I)
_TABLE_ROW_RE = re.compile(r"\|\s*`?([A-Za-z0-9_-]+)/?`?\s*\|\s*(.+?)\s*\|")


def _extract_purpose(md_text: str) -> str:
    lines = md_text.splitlines()
    # Prefer an explicit Purpose/Overview section.
    for i, line in enumerate(lines):
        if _SECTION_RE.match(line.strip()):
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s and not s.startswith("#"):
                    return s
    # Fallback: first non-heading, non-fence, non-empty line.
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("|"):
            return s
    return ""


def _parse_module_table(md_text: str) -> dict[str, str]:
    """Parse a `| Name | Purpose |` markdown table into {name: purpose}."""
    out: dict[str, str] = {}
    for line in md_text.splitlines():
        m = _TABLE_ROW_RE.match(line.strip())
        if not m:
            continue
        name, purpose = m.group(1), m.group(2)
        if name.lower() in ("module", "name", "directory") or set(purpose) <= {"-", " "}:
            continue
        out[name] = purpose
    return out


def extract(ctx) -> dict[str, dict]:
    # backend/CLAUDE.md module table — keyed by python module name.
    backend_table: dict[str, str] = {}
    if ctx.layout.python_root:
        backend_claude = ctx.layout.python_root / "CLAUDE.md"
        if backend_claude.exists():
            backend_table = _parse_module_table(backend_claude.read_text(errors="ignore"))

    # terraform/CLAUDE.md directory table — keyed by stack directory name.
    # There are no per-stack CLAUDE.md files, so this is the primary source
    # for tf:* node purposes.
    tf_table: dict[str, str] = {}
    if ctx.layout.terraform_root:
        tf_claude = ctx.layout.terraform_root / "CLAUDE.md"
        if tf_claude.exists():
            tf_table = _parse_module_table(tf_claude.read_text(errors="ignore"))

    enrich: dict[str, dict] = {}
    for name, node in ctx.nodes.items():
        node_dir = ctx.repo_path / node.path
        fields: dict = {}

        # 1. The node's own CLAUDE.md.
        claude_md = node_dir / "CLAUDE.md"
        if claude_md.exists():
            text = claude_md.read_text(errors="ignore")
            fields["claude_md"] = str(claude_md.relative_to(ctx.repo_path))
            purpose = _extract_purpose(text)
            if purpose:
                fields["purpose"] = purpose

        # 2. A parent table.
        if not fields.get("purpose"):
            if name in backend_table:
                fields["purpose"] = backend_table[name]
            elif name.startswith("tf:") and name[3:] in tf_table:
                fields["purpose"] = tf_table[name[3:]]

        # 3. The node's own README.md.
        if not fields.get("purpose"):
            readme = node_dir / "README.md"
            if readme.exists():
                purpose = _extract_purpose(readme.read_text(errors="ignore"))
                if purpose:
                    fields["purpose"] = purpose

        if fields:
            enrich[name] = fields
    return enrich
