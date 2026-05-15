#!/usr/bin/env bash
# devbox sourcegraph MCP server wrapper — installed to /usr/local/bin/devbox-sourcegraph-mcp.
# Pulls the Sourcegraph access token from SSM (via the EC2 instance role), exports it,
# and exec's the FastMCP stdio server. Missing-token case is non-fatal — the server
# returns a structured error on each tool call so Claude can surface the remediation.
set -u
. /opt/devbox/env 2>/dev/null || true
SOURCEGRAPH_URL="${SOURCEGRAPH_URL:-http://localhost:7080}"
SOURCEGRAPH_TOKEN="$(aws ssm get-parameter \
    --name /devbox/sourcegraph-token \
    --with-decryption \
    --region "${AWS_REGION:-us-east-2}" \
    --query Parameter.Value --output text 2>/dev/null || echo "")"
if [ -z "$SOURCEGRAPH_TOKEN" ]; then
  echo "[devbox-sourcegraph-mcp] /devbox/sourcegraph-token not in SSM. Run 'make upload-sourcegraph-token TOKEN=<token>' from your laptop." >&2
fi
# Optional: scope all queries to a single repo and pin them to a working branch
# (e.g. develop). Either or both may be unset.
SOURCEGRAPH_DEFAULT_REPO="$(aws ssm get-parameter \
    --name /devbox/sourcegraph-default-repo \
    --region "${AWS_REGION:-us-east-2}" \
    --query Parameter.Value --output text 2>/dev/null || echo "")"
SOURCEGRAPH_DEFAULT_REV="$(aws ssm get-parameter \
    --name /devbox/sourcegraph-default-rev \
    --region "${AWS_REGION:-us-east-2}" \
    --query Parameter.Value --output text 2>/dev/null || echo "")"
export SOURCEGRAPH_URL SOURCEGRAPH_TOKEN SOURCEGRAPH_DEFAULT_REPO SOURCEGRAPH_DEFAULT_REV
exec /home/ubuntu/.local/bin/uv run --project /opt/devbox/tooling --quiet devbox-sourcegraph-mcp "$@"
