#!/usr/bin/env python3
"""Register devbox-owned MCP servers in the ubuntu user's ~/.claude.json.

User-scope registration: every Claude Code session for this user gets the
devbox MCPs, no per-repo .mcp.json plumbing required. Idempotent merge — only
the entries in MCP_SERVERS are touched; other keys (and unrelated mcpServers
entries) in ~/.claude.json are preserved.

Run via `make sync-mcp-config` from the devbox repo; that path is what
sync-tooling chains as the final step of a full deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

# Devbox-owned MCP servers. Add new entries (sourcegraph, postgres) here as we
# build them out. Entries removed from this dict are NOT pruned from the user's
# config automatically — delete them by hand if you want to retire one.
MCP_SERVERS: dict[str, dict] = {
    "catalog": {
        "command": "/usr/local/bin/devbox-catalog-mcp",
        "args": [],
    },
    "sourcegraph": {
        "command": "/usr/local/bin/devbox-sourcegraph-mcp",
        "args": [],
    },
}

CONFIG_PATH = Path.home() / ".claude.json"


def main() -> int:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError as e:
            print(f"error: {CONFIG_PATH} is not valid JSON: {e}")
            return 1
        if not isinstance(cfg, dict):
            print(f"error: {CONFIG_PATH} is not a JSON object")
            return 1
    else:
        cfg = {}

    servers = cfg.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        print("error: 'mcpServers' is not an object in ~/.claude.json")
        return 1

    changed = []
    for name, entry in MCP_SERVERS.items():
        if servers.get(name) != entry:
            servers[name] = entry
            changed.append(name)

    if changed:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"updated {CONFIG_PATH}: {', '.join(changed)}")
    else:
        print(f"{CONFIG_PATH}: already up to date ({', '.join(MCP_SERVERS) or 'no servers configured'})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
