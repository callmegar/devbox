# Auto-attach SSH logins on the devbox to the canonical `devbox` tmux session.
# Sourced by /etc/profile.d/devbox-tmux.sh — must NOT use `exit` (would kill
# the login shell on early-return). Returns to caller for normal shell start
# if any guard fails.
#
# Skip conditions: not an SSH session, non-interactive shell, already inside
# tmux, missing tmux binary, missing setup script, or opted out via
# DEVBOX_NO_TMUX=1.

if [[ -z "${SSH_CONNECTION:-}" ]]; then return 0 2>/dev/null || true; fi
case $- in *i*) ;; *) return 0 2>/dev/null || true ;; esac
if [[ -n "${TMUX:-}" ]]; then return 0 2>/dev/null || true; fi
if [[ -n "${DEVBOX_NO_TMUX:-}" ]]; then return 0 2>/dev/null || true; fi
if ! command -v tmux >/dev/null 2>&1; then return 0 2>/dev/null || true; fi

if ! tmux has-session -t devbox 2>/dev/null; then
  if [[ -x /opt/devbox/scripts/setup-tmux.sh ]]; then
    /opt/devbox/scripts/setup-tmux.sh || return 0 2>/dev/null || true
  else
    return 0 2>/dev/null || true
  fi
fi

# Use non-exec attach so detaching (Ctrl-b d) lands the user back at a
# regular shell instead of disconnecting their SSH session.
tmux attach -t devbox
