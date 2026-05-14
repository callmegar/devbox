"""Enrichment extractor: harvest purpose text from CLAUDE.md files.

The `match` repo keeps a cascading CLAUDE.md per module — that's the
hand-authored semantic layer. We harvest a one-line purpose from each node's
own CLAUDE.md, falling back to the module table in backend/CLAUDE.md.
"""

from __future__ import annotations

import re

_SECTION_RE = re.compile(r"^#+\s+(Purpose|Overview|Project Overview)\s*$", re.I)
_TABLE_ROW_RE = re.compile(r"\|\s*`?([A-Za-z0-9_]+)/?`?\s*\|\s*(.+?)\s*\|")


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
    """Parse a `| Module | Purpose |` markdown table into {name: purpose}."""
    out: dict[str, str] = {}
    for line in md_text.splitlines():
        m = _TABLE_ROW_RE.match(line.strip())
        if not m:
            continue
        name, purpose = m.group(1), m.group(2)
        if name.lower() in ("module", "name") or set(purpose) <= {"-", " "}:
            continue
        out[name] = purpose
    return out


def extract(ctx) -> dict[str, dict]:
    table: dict[str, str] = {}
    if ctx.layout.python_root:
        backend_claude = ctx.layout.python_root / "CLAUDE.md"
        if backend_claude.exists():
            table = _parse_module_table(backend_claude.read_text(errors="ignore"))

    enrich: dict[str, dict] = {}
    for name, node in ctx.nodes.items():
        node_dir = ctx.repo_path / node.path
        claude_md = node_dir / "CLAUDE.md"
        fields: dict = {}
        if claude_md.exists():
            text = claude_md.read_text(errors="ignore")
            fields["claude_md"] = str(claude_md.relative_to(ctx.repo_path))
            purpose = _extract_purpose(text)
            if purpose:
                fields["purpose"] = purpose
        if not fields.get("purpose") and name in table:
            fields["purpose"] = table[name]
        if fields:
            enrich[name] = fields
    return enrich
