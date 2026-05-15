#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["ruamel.yaml>=0.18"]
# ///
"""Initialize ao's Slack notifier (global) and Linear tracker (per-project).

Pulls /devbox/linear-api-key from SSM (via the EC2 instance role), resolves a
Linear team KEY (e.g. "MAT") to its UUID via Linear's GraphQL API, then
idempotently merges into ao's config files:

  ~/.agent-orchestrator/config.yaml          # global
      notifiers.slack: { plugin, webhook: ${SLACK_WEBHOOK_URL}, channel }
      notificationRouting.{urgent,action,warning}: appends "slack" if absent

  <repo>/agent-orchestrator.yaml             # per-project (match's repo)
      projects.<key>.tracker: { plugin: linear, teamId: <UUID> }

Round-trips via ruamel.yaml so comments and existing key order survive.

Usage (on the box, invoked by `make init-ao-config`):
  init-ao-config.py --team MAT
  init-ao-config.py --team MAT --repo /home/ubuntu/repos/match --channel '#match-agents'
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML

GLOBAL_CONFIG_PATH = Path.home() / ".agent-orchestrator" / "config.yaml"


def ssm(name: str) -> str:
    region = os.environ.get("AWS_REGION", "us-east-2")
    return subprocess.check_output(
        ["aws", "ssm", "get-parameter", "--name", name, "--with-decryption",
         "--region", region, "--query", "Parameter.Value", "--output", "text"],
        text=True,
    ).strip()


def resolve_team_uuid(api_key: str, key: str) -> str:
    body = json.dumps({
        "query": "query($k:String!){teams(filter:{key:{eq:$k}}){nodes{id key name}}}",
        "variables": {"k": key},
    }).encode()
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"Linear API HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    if payload.get("errors"):
        sys.exit("Linear GraphQL error: " + json.dumps(payload["errors"]))
    nodes = payload["data"]["teams"]["nodes"]
    if not nodes:
        sys.exit(f"no Linear team with key '{key}' — wrong key, or the API token lacks access")
    team = nodes[0]
    print(f"resolved team key {team['key']} -> {team['id']} ({team['name']})", file=sys.stderr)
    return team["id"]


def append_unique(seq, value) -> bool:
    if value not in seq:
        seq.append(value)
        return True
    return False


def merge_global_config(yaml: YAML, channel: str) -> bool:
    if not GLOBAL_CONFIG_PATH.exists():
        sys.exit(f"{GLOBAL_CONFIG_PATH} not found — run `ao start` once to generate it.")
    cfg = yaml.load(GLOBAL_CONFIG_PATH)
    changed = False

    # ao's slack plugin reads `webhookUrl` from its YAML config (not from env)
    # despite the doc comment "Requires SLACK_WEBHOOK_URL env var" — that just
    # tells you where to keep the secret externally, not how the plugin loads
    # it. So we pull the actual webhook URL from SSM and inline it. The global
    # config is user-scope on the box (~/.agent-orchestrator/config.yaml), never
    # committed; restic snapshots are encrypted at rest. `channel:` isn't
    # honored by the slack plugin per `ao config-help` — the channel is bound
    # to the webhook itself at Slack-side creation time.
    webhook_url = ssm("/devbox/slack-webhook-url")
    if not webhook_url:
        sys.exit("no /devbox/slack-webhook-url in SSM — run `make upload-slack-webhook` first.")
    _ = channel
    notifiers = cfg.setdefault("notifiers", {})
    desired = {"plugin": "slack", "webhookUrl": webhook_url}
    current = dict(notifiers.get("slack") or {})
    if current != desired:
        notifiers["slack"] = desired
        changed = True

    routing = cfg.setdefault("notificationRouting", {})
    for level in ("urgent", "action", "warning"):
        seq = routing.setdefault(level, [])
        if append_unique(seq, "slack"):
            changed = True

    if changed:
        with open(GLOBAL_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f)
    return changed


def find_global_project_key(projects: dict, repo_path: str) -> str | None:
    """Locate the project entry in ~/.agent-orchestrator/config.yaml whose
    path matches the local repo. Global keys are hashed (e.g. match_3aef138fa5),
    so we match by `path:` not by key.
    """
    target = str(Path(repo_path).resolve())
    for key, entry in (projects or {}).items():
        ent_path = entry.get("path")
        if ent_path and str(Path(ent_path).resolve()) == target:
            return key
    return None


def merge_project_config(yaml: YAML, repo_path: str, team_uuid: str) -> bool:
    """Write tracker into <repo>/agent-orchestrator.yaml (the per-project
    OVERRIDES file). ao 0.8+ uses an unwrapped shape — fields live at the top
    level, no `projects:` wrapper. We auto-migrate from the legacy wrapped form
    if we encounter it.

    defaultBranch and defaults.notifiers no longer go here — they live in the
    global config under projects.<key>; see update_global_project().
    """
    yaml_path = Path(repo_path) / "agent-orchestrator.yaml"
    if not yaml_path.exists():
        cfg = {"tracker": {"plugin": "linear", "teamId": team_uuid}}
        with open(yaml_path, "w") as f:
            yaml.dump(cfg, f)
        return True

    cfg = yaml.load(yaml_path) or {}

    # Legacy migration: wrapped `projects.<key>.*` → flat top-level fields.
    if isinstance(cfg, dict) and isinstance(cfg.get("projects"), dict):
        projects = cfg["projects"]
        if len(projects) != 1:
            sys.exit(
                f"{yaml_path} has multiple projects in legacy wrapped form; "
                "auto-migration only handles single-project files. Use ao's "
                "dashboard 'Repair config' or migrate by hand."
            )
        only_key = next(iter(projects))
        cfg = dict(projects[only_key])

    desired = {"plugin": "linear", "teamId": team_uuid}
    if dict(cfg.get("tracker") or {}) == desired:
        # Already up to date AND we didn't have to migrate above (no wrapper).
        if yaml_path.read_text().lstrip().startswith("projects:") is False:
            return False

    cfg["tracker"] = desired
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)
    return True


def update_global_project(yaml: YAML, repo_path: str,
                           default_branch: str | None) -> bool:
    """Patch the matching projects.<key> entry in the global config — used to
    pin defaultBranch (since the unwrapped per-project YAML no longer carries
    project-identity fields)."""
    if not default_branch:
        return False
    cfg = yaml.load(GLOBAL_CONFIG_PATH)
    projects = cfg.get("projects") or {}
    key = find_global_project_key(projects, repo_path)
    if not key:
        print(
            f"warning: no project in global config matched path {repo_path}; "
            "skipping defaultBranch update",
            file=sys.stderr,
        )
        return False
    if projects[key].get("defaultBranch") == default_branch:
        return False
    projects[key]["defaultBranch"] = default_branch
    with open(GLOBAL_CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True, help="Linear team key (e.g. MAT)")
    ap.add_argument("--repo", default=str(Path.home() / "repos" / "match"),
                    help="path to the project repo holding agent-orchestrator.yaml")
    ap.add_argument("--channel", default="agent-updates",
                    help="Slack channel for notifications. Leading '#' is added "
                         "if missing — pass `dev-team` not `#dev-team` to avoid "
                         "shell comment-character headaches.")
    ap.add_argument("--default-branch", default=None,
                    help="If set, pin projects.<key>.defaultBranch to this value (e.g. develop)")
    args = ap.parse_args()

    api_key = os.environ.get("LINEAR_API_KEY") or ssm("/devbox/linear-api-key")
    team_uuid = resolve_team_uuid(api_key, args.team)

    channel = args.channel if args.channel.startswith("#") else f"#{args.channel}"

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # don't line-wrap long values like the $schema URL

    g_changed = merge_global_config(yaml, channel)
    g_changed = update_global_project(yaml, args.repo, args.default_branch) or g_changed
    p_changed = merge_project_config(yaml, args.repo, team_uuid)

    print(f"global  : {'updated' if g_changed else 'unchanged'}  ({GLOBAL_CONFIG_PATH})")
    print(f"project : {'updated' if p_changed else 'unchanged'}  ({args.repo}/agent-orchestrator.yaml)")
    print("restart ao for the new env (SLACK_WEBHOOK_URL, LINEAR_API_KEY) to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
