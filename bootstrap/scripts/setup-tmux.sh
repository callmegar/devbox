#!/usr/bin/env bash
# Creates the canonical `devbox` tmux session with a 2x2 grid of plain shells:
#
#   ┌──────────────────────┬──────────────────────┐
#   │ TL: shell in match   │ TR: shell in match   │
#   ├──────────────────────┼──────────────────────┤
#   │ BL: shell in match   │ BR: shell in match   │
#   └──────────────────────┴──────────────────────┘
#
# No commands are auto-run in any pane — every pane is a plain bash in the
# match working directory, ready for ad-hoc local work.
#
# Idempotent — if the session already exists this script exits cleanly so
# the profile.d hook just attaches. To re-create with the latest layout,
# run with FORCE=1.
#
# Uses tmux pane IDs (#{pane_id}, e.g. %0/%1) for targeting because pane
# indices can be renumbered by select-layout. Avoids `select-layout tiled`
# entirely — it walks the layout tree depth-first and reorders panes by
# traversal, which scrambles spatial placement. Each split divides the
# target pane in half by default, so four sequential splits already yield
# an even 2×2 grid.

set -euo pipefail

SESSION=${SESSION:-devbox}
CWD=${CWD:-/data/home/repos/match}

if [[ "${FORCE:-0}" == 1 ]] && tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  exit 0
fi

# TL: initial pane
tmux new-session -d -s "$SESSION" -n main -c "$CWD"
PANE_TL=$(tmux display-message -t "$SESSION:main" -p '#{pane_id}')

# TR: right of TL
tmux split-window -h -t "$PANE_TL" -c "$CWD"
PANE_TR=$(tmux display-message -t "$SESSION:main" -p '#{pane_id}')

# BL: below TL
tmux split-window -v -t "$PANE_TL" -c "$CWD"

# BR: below TR
tmux split-window -v -t "$PANE_TR" -c "$CWD"

tmux select-pane -t "$PANE_TL"
