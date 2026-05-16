#!/usr/bin/env bash
# credential_process script — emits AWS credentials in the JSON shape the
# AWS SDK expects. Fetches values from SSM (SecureString) via the box's
# instance role and prints them on stdout.
#
# Wired in ~/.aws/config under [profile pablo]:
#
#   [profile pablo]
#   credential_process = /usr/local/bin/devbox-aws-credentials
#
# Use by exporting AWS_PROFILE=pablo (handled automatically for ubuntu's
# login shells via /etc/profile.d/devbox-aws-profile.sh) — then aws CLI,
# tofu's AWS provider, and any SDK-using tool transparently runs as the
# `pablo` IAM user.
#
# Bypasses the user's ~/.aws/* config when calling `aws ssm get-parameter`
# so we don't recurse back through credential_process and deadlock — the
# instance role is used to fetch the parameters.

set -euo pipefail
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
export AWS_SHARED_CREDENTIALS_FILE=/dev/null
export AWS_CONFIG_FILE=/dev/null
REGION="${AWS_REGION:-us-east-2}"

ssm_get() {
  aws ssm get-parameter --name "$1" --with-decryption --region "$REGION" \
    --query Parameter.Value --output text
}

KEY_ID="$(ssm_get /devbox/aws-access-key-id)"
SECRET="$(ssm_get /devbox/aws-secret-access-key)"

printf '{"Version":1,"AccessKeyId":"%s","SecretAccessKey":"%s"}\n' \
  "$KEY_ID" "$SECRET"
