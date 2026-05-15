#!/usr/bin/env bash
# devbox catalog MCP server wrapper — installed to /usr/local/bin/devbox-catalog-mcp
# on the box. Runs the FastMCP stdio server from the uv project while preserving
# the caller's cwd (so `repo="."` resolves against where Claude Code spawned it
# from — typically the target repo root via .mcp.json).
exec /home/ubuntu/.local/bin/uv run --project /opt/devbox/tooling --quiet devbox-catalog-mcp "$@"
