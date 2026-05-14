#!/usr/bin/env bash
# devbox CLI wrapper — installed to /usr/local/bin/devbox on the box.
# Runs the catalog tool from its uv project while preserving the caller's cwd
# (so `devbox catalog build --repo .` resolves against where you ran it).
exec /home/ubuntu/.local/bin/uv run --project /opt/devbox/tooling --quiet devbox "$@"
