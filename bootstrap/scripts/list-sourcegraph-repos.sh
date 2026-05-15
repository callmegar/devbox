#!/usr/bin/env bash
# List repos Sourcegraph has cloned/indexed. Runs on the box; expects the
# instance role to grant ssm:GetParameter on /devbox/sourcegraph-token.
set -eu
. /opt/devbox/env 2>/dev/null || true
TOKEN=$(aws ssm get-parameter \
    --name /devbox/sourcegraph-token --with-decryption \
    --region "${AWS_REGION:-us-east-2}" \
    --query Parameter.Value --output text)
curl -sS -H "Authorization: token $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"{ repositories(first:50){ totalCount nodes { name mirrorInfo { cloned cloneInProgress lastError updatedAt } } } }"}' \
  http://localhost:7080/.api/graphql \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]["repositories"]
print("total:", d["totalCount"])
for n in d["nodes"]:
    m = n["mirrorInfo"]
    err = m.get("lastError") or "-"
    line = "  {name:50}  cloned={cloned!s:5}  in_progress={ip!s:5}  last_error={err}".format(
        name=n["name"], cloned=m["cloned"], ip=m["cloneInProgress"], err=err[:60])
    print(line)
'
