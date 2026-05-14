#!/usr/bin/env bash
# Pulls outbound SSH keys from SSM (/devbox/ssh-keys/<name>) and installs
# them under ~ubuntu/.ssh with the right perms + Host alias.
# Idempotent: safe to re-run.
set -euo pipefail
source /opt/devbox/env

SSH_DIR="/home/$DEVBOX_USER/.ssh"
install -d -o "$DEVBOX_USER" -g "$DEVBOX_USER" -m 0700 "$SSH_DIR"
install -o "$DEVBOX_USER" -g "$DEVBOX_USER" -m 0600 /dev/null "$SSH_DIR/config.devbox" 2>/dev/null || true

fetch_key() {
  local name="$1" hostname="$2"
  local priv="$SSH_DIR/${name}_ed25519"
  local pub="$priv.pub"
  if ! aws ssm get-parameter --name "/devbox/ssh-keys/$name" --with-decryption \
        --region "$AWS_REGION" --query Parameter.Value --output text > "$priv.tmp" 2>/dev/null; then
    rm -f "$priv.tmp"
    echo "no key /devbox/ssh-keys/$name in SSM — skipping $hostname"
    return 0
  fi
  install -o "$DEVBOX_USER" -g "$DEVBOX_USER" -m 0600 "$priv.tmp" "$priv"
  rm -f "$priv.tmp"
  if aws ssm get-parameter --name "/devbox/ssh-keys/$name.pub" \
        --region "$AWS_REGION" --query Parameter.Value --output text > "$pub.tmp" 2>/dev/null; then
    install -o "$DEVBOX_USER" -g "$DEVBOX_USER" -m 0644 "$pub.tmp" "$pub"
  fi
  rm -f "$pub.tmp"
  # User-managed ~/.ssh/config is left alone; our entries live in a sibling
  # file that gets pulled in via Include.
  local config="$SSH_DIR/config.devbox"
  if ! grep -q "^# devbox-managed: $name$" "$config" 2>/dev/null; then
    cat >> "$config" <<EOF
# devbox-managed: $name
Host $hostname
  IdentityFile $priv
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new

EOF
    chown "$DEVBOX_USER:$DEVBOX_USER" "$config"
  fi
  # Ensure the user's main ~/.ssh/config sources config.devbox.
  # Include must be at the top to apply to all subsequent Host blocks.
  local main="$SSH_DIR/config"
  touch "$main"
  chown "$DEVBOX_USER:$DEVBOX_USER" "$main"
  chmod 0600 "$main"
  if ! grep -q "^Include $config$" "$main"; then
    (echo "Include $config"; echo; cat "$main") > "$main.new"
    mv "$main.new" "$main"
    chown "$DEVBOX_USER:$DEVBOX_USER" "$main"
    chmod 0600 "$main"
  fi
  echo "installed SSH key for $hostname"
}

# Add more hosts as needed: fetch_key github github.com
fetch_key gitlab gitlab.com
