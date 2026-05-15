"""devbox-sourcegraph MCP server — wraps the box-local Sourcegraph instance.

Tools:
  code_search        run a Sourcegraph search query, return file matches + snippets
  find_symbol        search for symbol definitions by name (type:symbol)
  find_references    text-search for references to a symbol or string

Configuration via env vars (set by the wrapper from SSM):
  SOURCEGRAPH_URL    base URL (default: http://localhost:7080)
  SOURCEGRAPH_TOKEN  access token (required)

Missing token doesn't crash the server — each tool returns a structured
{"error": "..."} that surfaces the remediation in Claude's tool response.
"""

from __future__ import annotations

import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import SourcegraphClient, SourcegraphError

mcp = FastMCP("devbox-sourcegraph")

# Defaults injected into tool queries unless the caller specifies otherwise.
# DEFAULT_REPO scopes searches to a single repo (Sourcegraph needs `repo:` to
# search a non-default revision via the index). DEFAULT_REV pins a branch like
# `develop`. Both are sourced by the wrapper from SSM and may be empty.
DEFAULT_REPO = os.environ.get("SOURCEGRAPH_DEFAULT_REPO", "").strip()
DEFAULT_REV = os.environ.get("SOURCEGRAPH_DEFAULT_REV", "").strip()

_REPO_AT_REV_RE = re.compile(r"\brepo:\S+@\S+")

_SEARCH_GQL = """
query Search($query: String!) {
  search(query: $query, version: V3, patternType: standard) {
    results {
      matchCount
      limitHit
      results {
        __typename
        ... on FileMatch {
          file { path }
          repository { name }
          chunkMatches { content contentStart { line } }
        }
      }
    }
  }
}
"""

_SYMBOL_GQL = """
query SymbolSearch($query: String!) {
  search(query: $query, version: V3, patternType: standard) {
    results {
      matchCount
      limitHit
      results {
        __typename
        ... on FileMatch {
          file { path }
          repository { name }
          symbols {
            name
            containerName
            kind
            location { range { start { line character } } }
          }
        }
      }
    }
  }
}
"""


def _file_match_summary(m: dict) -> dict:
    chunks = []
    for c in m.get("chunkMatches") or []:
        start_line = (c.get("contentStart") or {}).get("line")
        chunks.append({"line": start_line, "content": c.get("content")})
    return {
        "repository": (m.get("repository") or {}).get("name"),
        "path": (m.get("file") or {}).get("path"),
        "snippets": chunks,
    }


def _symbol_match_summary(m: dict) -> list[dict]:
    out = []
    repo = (m.get("repository") or {}).get("name")
    path = (m.get("file") or {}).get("path")
    for s in m.get("symbols") or []:
        loc = (s.get("location") or {}).get("range") or {}
        start = loc.get("start") or {}
        out.append({
            "name": s.get("name"),
            "container": s.get("containerName"),
            "kind": s.get("kind"),
            "repository": repo,
            "path": path,
            "line": start.get("line"),
            "character": start.get("character"),
        })
    return out


def _run_search(query: str, gql: str) -> dict[str, Any]:
    try:
        return SourcegraphClient().query(gql, {"query": query})
    except SourcegraphError as e:
        return {"__error__": str(e)}


def _apply_defaults(query: str) -> str:
    """Inject default `repo:` and/or `rev:` filters when the caller hasn't.

    Sourcegraph won't return results from an indexed non-default branch
    unless the query is scoped with `repo:`, so we add both together for
    the common single-repo / single-working-branch case.
    """
    extras: list[str] = []
    if DEFAULT_REPO and "repo:" not in query:
        extras.append(f"repo:{DEFAULT_REPO}")
    if DEFAULT_REV and "rev:" not in query and not _REPO_AT_REV_RE.search(query):
        extras.append(f"rev:{DEFAULT_REV}")
    if not extras:
        return query
    return query + " " + " ".join(extras)


def _quote_if_phrase(text: str) -> str:
    """Sourcegraph treats `"..."` as a strict literal phrase. Single tokens
    work better unquoted (otherwise `type:symbol "foo"` and `"foo"` both
    over-restrict matches). Only quote when the text has whitespace.
    """
    if any(c.isspace() for c in text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


@mcp.tool()
def code_search(query: str, max_results: int = 30) -> dict[str, Any]:
    """Run a Sourcegraph search query and return file matches with snippet
    context. `query` uses Sourcegraph's native search syntax — examples:

      ClientProfile lang:python
      repo:match file:migrations CREATE
      lang:typescript favorites
      select:file file:Dockerfile
    """
    data = _run_search(_apply_defaults(query), _SEARCH_GQL)
    if "__error__" in data:
        return {"error": data["__error__"]}
    results = ((data.get("search") or {}).get("results") or {})
    matches = [
        _file_match_summary(m)
        for m in (results.get("results") or [])
        if m.get("__typename") == "FileMatch"
    ][:max_results]
    return {
        "query": query,
        "match_count": results.get("matchCount", 0),
        "limit_hit": bool(results.get("limitHit")),
        "matches": matches,
    }


@mcp.tool()
def find_symbol(name: str, repo: str | None = None, lang: str | None = None,
                max_results: int = 30) -> dict[str, Any]:
    """Find symbol definitions by name across indexed repos. Builds a
    `type:symbol "<name>"` query, optionally narrowed by `repo:` and `lang:`.
    Returns each match's name, kind, container, and file:line.
    """
    parts = [f"type:symbol {_quote_if_phrase(name)}"]
    if repo:
        parts.append(f"repo:{repo}")
    if lang:
        parts.append(f"lang:{lang}")
    query = _apply_defaults(" ".join(parts))
    data = _run_search(query, _SYMBOL_GQL)
    if "__error__" in data:
        return {"error": data["__error__"]}
    results = ((data.get("search") or {}).get("results") or {})
    symbols: list[dict] = []
    for m in (results.get("results") or []):
        if m.get("__typename") == "FileMatch":
            symbols.extend(_symbol_match_summary(m))
    return {
        "query": query,
        "match_count": results.get("matchCount", 0),
        "limit_hit": bool(results.get("limitHit")),
        "symbols": symbols[:max_results],
    }


@mcp.tool()
def find_references(text: str, repo: str | None = None, lang: str | None = None,
                    max_results: int = 50) -> dict[str, Any]:
    """Text-search for references to a symbol or literal string across indexed
    repos. Returns file matches with snippet context. Use find_symbol when you
    only want definitions; this returns every usage.
    """
    parts = [_quote_if_phrase(text)]
    if repo:
        parts.append(f"repo:{repo}")
    if lang:
        parts.append(f"lang:{lang}")
    query = _apply_defaults(" ".join(parts))
    data = _run_search(query, _SEARCH_GQL)
    if "__error__" in data:
        return {"error": data["__error__"]}
    results = ((data.get("search") or {}).get("results") or {})
    matches = [
        _file_match_summary(m)
        for m in (results.get("results") or [])
        if m.get("__typename") == "FileMatch"
    ][:max_results]
    return {
        "query": query,
        "match_count": results.get("matchCount", 0),
        "limit_hit": bool(results.get("limitHit")),
        "matches": matches,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
