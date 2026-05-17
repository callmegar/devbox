#!/usr/bin/env bash
# Idempotent install of devbox-local-review: a multi-persona code reviewer
# (architecture, code-cleanliness, code-quality, security, test, ux) invoked
# as `/local-review:local-review`.
#
# Files are fetched fresh from this devbox repo's main branch on every run.
# Editing a persona in the repo + re-running this script (locally on the box
# as /opt/devbox/scripts/install-local-review.sh, or via `make sync-local-review`
# from the laptop) picks up the change without redeploying cloud-init. To pin
# a specific commit, set LOCAL_REVIEW_RAW_BASE to that commit's raw URL.
#
# Layout on the box:
#   /opt/devbox/local-review/plugin/         — claude plugin (marketplace + command)
#   /opt/devbox/local-review/personas/*.md   — read at runtime by the orchestrator
#
# Runs after install-claude-code (needs the `claude` CLI for $DEVBOX_USER).
set -euo pipefail
source /opt/devbox/env

RAW_BASE="${LOCAL_REVIEW_RAW_BASE:-https://raw.githubusercontent.com/callmegar/devbox/main/bootstrap/local-review}"

if ! sudo -u "$DEVBOX_USER" bash -lc 'command -v claude' >/dev/null 2>&1; then
  echo "claude CLI not on PATH for $DEVBOX_USER — install-claude-code must run first"
  exit 1
fi

install -d -m 0755 \
  /opt/devbox/local-review/plugin/.claude-plugin \
  /opt/devbox/local-review/plugin/commands \
  /opt/devbox/local-review/personas

PLUGIN_FILES=(
  plugin/.claude-plugin/plugin.json
  plugin/.claude-plugin/marketplace.json
  plugin/commands/local-review.md
)
for f in "${PLUGIN_FILES[@]}"; do
  echo "fetch $f"
  curl -fsSL "$RAW_BASE/$f" -o "/opt/devbox/local-review/$f"
done

PERSONAS=(
  architecture-reviewer
  code-cleanliness-reviewer
  code-quality-reviewer
  security-reviewer
  test-reviewer
  ux-reviewer
)
for p in "${PERSONAS[@]}"; do
  echo "fetch personas/$p.md"
  curl -fsSL "$RAW_BASE/personas/$p.md" -o "/opt/devbox/local-review/personas/$p.md"
done

chown -R "$DEVBOX_USER:$DEVBOX_USER" /opt/devbox/local-review

run_as_user() {
  sudo -u "$DEVBOX_USER" bash -lc "export HOME=/home/$DEVBOX_USER && $1"
}

# Register the local marketplace (idempotent). Match on the marketplace's
# source path rather than the name so we don't confuse "devbox-local" with
# any other "*devbox-local*" entry.
if ! run_as_user "claude plugin marketplace list 2>/dev/null" | grep -q '/opt/devbox/local-review/plugin'; then
  echo "registering devbox-local marketplace at /opt/devbox/local-review/plugin"
  run_as_user "claude plugin marketplace add /opt/devbox/local-review/plugin"
else
  echo "refreshing devbox-local marketplace"
  run_as_user "claude plugin marketplace update devbox-local" || true
fi

# Install the plugin at user scope (idempotent).
if ! run_as_user "claude plugin list 2>/dev/null" | grep -qF "devbox-local-review@devbox-local"; then
  echo "installing devbox-local-review@devbox-local"
  run_as_user "claude plugin install devbox-local-review@devbox-local --scope user"
else
  echo "skip devbox-local-review (already installed)"
fi

echo "devbox-local-review ready — invoke via /local-review:local-review"
