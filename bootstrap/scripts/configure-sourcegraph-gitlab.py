#!/usr/bin/env python3
"""Configure Sourcegraph to clone + index one or more GitLab repos.

Reads tokens from SSM (via the EC2 instance role):
  /devbox/sourcegraph-token   site-admin access token
  /devbox/gitlab-api-token    GitLab personal access token (read_api, read_repository)

Idempotent — looks up an existing external service named 'devbox-gitlab';
updates its config if found, creates a new one otherwise. Repo list is merged
(deduped) so repeat invocations add repos without dropping previous ones.

Usage:
  configure-sourcegraph-gitlab.py --url https://gitlab.com match2160244/match
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

SOURCEGRAPH_URL = os.environ.get("SOURCEGRAPH_URL", "http://localhost:7080").rstrip("/")
DISPLAY_NAME = "devbox-gitlab"


def _ssm(name: str) -> str:
    region = os.environ.get("AWS_REGION", "us-east-2")
    return subprocess.check_output(
        ["aws", "ssm", "get-parameter",
         "--name", name, "--with-decryption",
         "--region", region,
         "--query", "Parameter.Value", "--output", "text"],
        text=True,
    ).strip()


def _gql(query: str, variables: dict | None = None) -> dict:
    token = os.environ["SOURCEGRAPH_TOKEN"]
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        f"{SOURCEGRAPH_URL}/.api/graphql",
        data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"token {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"sourcegraph HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
    except urllib.error.URLError as e:
        sys.exit(f"could not reach sourcegraph at {SOURCEGRAPH_URL}: {e.reason}")
    if payload.get("errors"):
        msg = "; ".join(e.get("message", "?") for e in payload["errors"])
        sys.exit(f"sourcegraph graphql error: {msg}")
    return payload["data"]


def _find_existing() -> dict | None:
    data = _gql("""
        { externalServices(first: 50) {
            nodes { id displayName kind config }
        } }
    """)
    for s in data["externalServices"]["nodes"]:
        if s["displayName"] == DISPLAY_NAME and s["kind"] == "GITLAB":
            return s
    return None


def _add(config_json: str) -> dict:
    return _gql("""
        mutation Add($displayName: String!, $config: String!) {
          addExternalService(input: { kind: GITLAB, displayName: $displayName, config: $config }) {
            id displayName
          }
        }
    """, {"displayName": DISPLAY_NAME, "config": config_json})


def _update(svc_id: str, config_json: str) -> dict:
    return _gql("""
        mutation Upd($id: ID!, $displayName: String!, $config: String!) {
          updateExternalService(input: { id: $id, displayName: $displayName, config: $config }) {
            id displayName
          }
        }
    """, {"id": svc_id, "displayName": DISPLAY_NAME, "config": config_json})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://gitlab.com", help="GitLab base URL")
    ap.add_argument("repos", nargs="+",
                    help="GitLab project full-paths (e.g. match2160244/match)")
    args = ap.parse_args()

    if not os.environ.get("SOURCEGRAPH_TOKEN"):
        os.environ["SOURCEGRAPH_TOKEN"] = _ssm("/devbox/sourcegraph-token")
    gitlab_token = os.environ.get("GITLAB_API_TOKEN") or _ssm("/devbox/gitlab-api-token")

    existing = _find_existing()
    if existing:
        try:
            old_cfg = json.loads(existing["config"])
        except json.JSONDecodeError:
            old_cfg = {}
        existing_projects = old_cfg.get("projects") or []
    else:
        existing_projects = []

    by_name = {p.get("name"): p for p in existing_projects if p.get("name")}
    for r in args.repos:
        by_name.setdefault(r, {"name": r})
    merged = list(by_name.values())

    # projectQuery is required by Sourcegraph's GitLab config schema even when
    # we only want the explicit `projects` list — `"none"` disables auto-discovery.
    config = {
        "url": args.url,
        "token": gitlab_token,
        "projectQuery": ["none"],
        "projects": merged,
    }
    config_json = json.dumps(config)

    if existing:
        _update(existing["id"], config_json)
        print(f"updated external service '{DISPLAY_NAME}' (id={existing['id']}) — {len(merged)} project(s)")
    else:
        result = _add(config_json)
        sid = result["addExternalService"]["id"]
        print(f"created external service '{DISPLAY_NAME}' (id={sid}) — {len(merged)} project(s)")

    print(f"projects: {[p['name'] for p in merged]}")
    print("Sourcegraph will clone + index in the background.")
    print("Watch with: `make sourcegraph-repos`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
