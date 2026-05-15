#!/usr/bin/env bash
# devbox linear MCP server wrapper — installed to /usr/local/bin/devbox-linear-mcp.
# Pulls the Linear personal API key (and optional default team key) from SSM
# via the EC2 instance role, exports them, and exec's the FastMCP stdio server.
# Missing key is non-fatal — the server returns a structured error on each
# tool call so Claude can surface the remediation.
set -u
. /opt/devbox/env 2>/dev/null || true
LINEAR_API_KEY="$(aws ssm get-parameter \
    --name /devbox/linear-api-key \
    --with-decryption \
    --region "${AWS_REGION:-us-east-2}" \
    --query Parameter.Value --output text 2>/dev/null || echo "")"
if [ -z "$LINEAR_API_KEY" ]; then
  echo "[devbox-linear-mcp] /devbox/linear-api-key not in SSM. Mint a personal API key at https://linear.app/settings/account/security and upload it with 'make upload-linear-api-key KEY=<lin_api_...>' from your laptop." >&2
fi
LINEAR_DEFAULT_TEAM_KEY="$(aws ssm get-parameter \
    --name /devbox/linear-default-team-key \
    --region "${AWS_REGION:-us-east-2}" \
    --query Parameter.Value --output text 2>/dev/null || echo "")"
export LINEAR_API_KEY LINEAR_DEFAULT_TEAM_KEY
exec /home/ubuntu/.local/bin/uv run --project /opt/devbox/tooling --quiet devbox-linear-mcp "$@"
