#!/usr/bin/env bash
# Idempotent install of the closedloop-ai plugin marketplace + plugins.
# Re-runs are safe: existing marketplace registration and installed plugins
# are detected and skipped. Run after install-claude-code (requires the
# `claude` CLI on PATH for $DEVBOX_USER).
#
# Plugins installed (user scope, floating against upstream `main`):
#   bootstrap     — /bootstrap:agent-bootstrap to generate per-domain agents
#   code          — plan/critic/judge/implement/verify pipeline (/code:code)
#   code-review   — /code-review:start branch + PR review (/ultrareview)
#   judges        — 16 generic plan-quality judges + run-judges skill
#   platform      — shared skills (mermaid, prompt patterns, ...)
#   self-learning — capture/process/export learnings into org-patterns.toon
#
# Tracks upstream `main` (floating). To pin, run
#   claude plugin marketplace update closedloop-ai --commit <sha>
# manually after first install.
set -euo pipefail
source /opt/devbox/env

if ! sudo -u "$DEVBOX_USER" bash -lc 'command -v claude' >/dev/null 2>&1; then
  echo "claude CLI not on PATH for $DEVBOX_USER — install-claude-code must run first"
  exit 1
fi

run_as_user() {
  sudo -u "$DEVBOX_USER" bash -lc "export HOME=/home/$DEVBOX_USER && $1"
}

# 1. Register the marketplace (idempotent).
if ! run_as_user "claude plugin marketplace list 2>/dev/null" | grep -q closedloop-ai; then
  echo "registering closedloop-ai marketplace"
  run_as_user "claude plugin marketplace add https://github.com/closedloop-ai/claude-plugins"
fi

# 2. Install each plugin at user scope, skipping already-installed.
PLUGINS=(bootstrap code code-review judges platform self-learning)
for p in "${PLUGINS[@]}"; do
  if run_as_user "claude plugin list 2>/dev/null" | grep -qF "${p}@closedloop-ai"; then
    echo "skip ${p}@closedloop-ai (already installed)"
    continue
  fi
  echo "installing ${p}@closedloop-ai"
  run_as_user "claude plugin install ${p}@closedloop-ai --scope user"
done

echo "closedloop plugins ready"
