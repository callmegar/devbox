#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["ruamel.yaml>=0.18"]
# ///
"""Initialize ao's Slack notifier (global) and Linear tracker (per-project).

Pulls /devbox/linear-api-key from SSM (via the EC2 instance role), resolves a
Linear team KEY (e.g. "ABC") to its UUID via Linear's GraphQL API, then
idempotently merges into ao's config files:

  ~/.agent-orchestrator/config.yaml          # global
      notifiers.slack: { plugin, webhook: ${SLACK_WEBHOOK_URL}, channel }
      notificationRouting.{urgent,action,warning}: appends "slack" if absent

  <repo>/agent-orchestrator.yaml             # per-project (the target repo)
      tracker: { plugin: linear, teamId: <UUID> }
      orchestratorRules: <managed block teaching the orchestrator to use the
                          devbox Linear MCP for epic-driven fan-out>
      agentRulesFile: .devbox-agent-rules.md  # pointer to the devbox-managed
                                              # done-gate rules (alongside any
                                              # user-written `agentRules`)

  <repo>/.devbox-agent-rules.md              # devbox-managed worker rules
      (Currently: invoke /local-review:local-review before reporting
       completed; treat Critical findings as blocking.)

Round-trips via ruamel.yaml so comments and existing key order survive. The
`tracker`, `orchestratorRules`, and `agentRulesFile` keys are devbox-managed
and get overwritten on each run; the `agentRules` field and everything else
is preserved.

Usage (on the box, invoked by `make init-ao-config`):
  init-ao-config.py --team ABC
  init-ao-config.py --team ABC --repo /home/ubuntu/repos/your-repo --channel '#agent-updates'
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

# Managed block injected into per-project agent-orchestrator.yaml under the
# `orchestratorRules` key. Overwritten on each run — don't hand-edit. Teaches
# the orchestrator session how to use the devbox-linear MCP for epic-driven
# fan-out so a single "work this epic" instruction kicks off many parallel
# workers.
ORCHESTRATOR_RULES = """\
# Epic-driven autonomous fan-out (devbox)
When the user points you at a Linear epic (a parent issue with children) and
asks you to "work it" / "drive it" / "run it through", do not implement
yourself — fan out parallel workers via ao. The devbox-linear MCP exposes the
needed tools:

1. Call `list_ready_issues(parent=<EPIC-ID>)`. The MCP filters out children
   with unresolved `blocks` relations, so the returned set is genuinely
   pickup-able right now. Use the response's `blocked_preview` to anticipate
   what unblocks next.
2. For each ready child, run `ao spawn <identifier>` (or `ao batch-spawn`
   for 3+). Each worker gets its own worktree + branch named after the
   identifier; they run in parallel.
3. Workers should call `claim_issue(<id>)` from their Linear MCP at start —
   that flips state to "In Progress" and acts as the lock against duplicates.
4. Poll `ao status --reports 1` periodically. When a worker reports
   `completed`, call `list_ready_issues` again — completing one issue usually
   unblocks others. Spawn the newly-ready ones.
5. Terminate the run when `list_ready_issues` returns empty AND no workers
   are active. Post a Slack summary listing what completed, what's left, and
   any items still blocked on external dependencies.

Refuse to fan-out and ask for confirmation if:
- The epic has more than ~10 ready children (cost / blast radius).
- Any ready child has no description or acceptance criteria — a worker can't
  do a good job on a stub; flag it back to the user instead.

If the user references a single Linear issue (no parent context), treat it as
a normal `ao spawn` — no fan-out.
"""

# Devbox-managed worker rules, written to `<repo>/.devbox-agent-rules.md` and
# referenced via the per-project `agentRulesFile`. ao merges this content into
# every worker's system prompt alongside any user-written `agentRules`. The
# gate forces a fresh-context multi-persona review pass before workers report
# `completed`, catching architecture / security / test / UX regressions
# before they reach the MR.
DEVBOX_AGENT_RULES_FILE_NAME = ".devbox-agent-rules.md"
DEVBOX_AGENT_RULES = """\
# Devbox-managed worker rules

This file is overwritten by `make init-ao-config` on every run. Hand-written
rules belong in `agent-orchestrator.yaml`'s `agentRules` field — that field is
preserved and merged alongside this one.

## Local-review done-gate

Before reporting `completed` (and before pushing the branch or creating an
MR), invoke `/local-review:local-review` on your uncommitted changes.

- Treat every **Critical** finding as blocking. Fix it, commit the fix,
  then re-run `/local-review:local-review`.
- Treat every **Warning** finding as advisory. Address what you agree with;
  justify what you skip in your `ao report completed` message.
- If the same Critical persists across two re-runs of the reviewer, stop
  looping and surface it explicitly in your completion report:
  "blocked by reviewer Critical: <description> at <file:line>; needs human
  judgment." Do not push the branch in that state.

The reviewer is a panel of six fresh-context Sonnet specialists
(architecture, code-cleanliness, code-quality, security, test, ux) with
Haiku confidence-scoring on top — trust its Critical findings. Its purpose
is to catch the failure modes a single-pass implementer typically misses.
"""


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
    """Write tracker + orchestratorRules into <repo>/agent-orchestrator.yaml
    (the per-project OVERRIDES file). ao 0.8+ uses an unwrapped shape — fields
    live at the top level, no `projects:` wrapper. We auto-migrate from the
    legacy wrapped form if we encounter it.

    defaultBranch and defaults.notifiers no longer go here — they live in the
    global config under projects.<key>; see update_global_project().

    Devbox owns these keys (overwritten each run):
      tracker            — plugin + teamId
      orchestratorRules  — the epic-driven fan-out playbook (ORCHESTRATOR_RULES)
      agentRulesFile     — pointer to the devbox-managed worker rules
    Everything else (agentRules, etc.) is preserved as-is. The devbox-managed
    rules file at <repo>/.devbox-agent-rules.md is also (re)written on every
    run — ao merges it into every worker's prompt alongside agentRules.
    """
    yaml_path = Path(repo_path) / "agent-orchestrator.yaml"
    rules_path = Path(repo_path) / DEVBOX_AGENT_RULES_FILE_NAME
    desired_tracker = {"plugin": "linear", "teamId": team_uuid}

    # Always refresh the rules file content — it's devbox-managed and small.
    rules_changed = (
        not rules_path.exists()
        or rules_path.read_text() != DEVBOX_AGENT_RULES
    )
    if rules_changed:
        rules_path.write_text(DEVBOX_AGENT_RULES)

    if not yaml_path.exists():
        cfg = {
            "tracker": desired_tracker,
            "agentRulesFile": DEVBOX_AGENT_RULES_FILE_NAME,
            "orchestratorRules": ORCHESTRATOR_RULES,
        }
        with open(yaml_path, "w") as f:
            yaml.dump(cfg, f)
        return True

    cfg = yaml.load(yaml_path) or {}

    # Legacy migration: wrapped `projects.<key>.*` → flat top-level fields.
    migrated = False
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
        migrated = True

    changed = migrated or rules_changed
    if dict(cfg.get("tracker") or {}) != desired_tracker:
        cfg["tracker"] = desired_tracker
        changed = True
    if cfg.get("agentRulesFile") != DEVBOX_AGENT_RULES_FILE_NAME:
        cfg["agentRulesFile"] = DEVBOX_AGENT_RULES_FILE_NAME
        changed = True
    if cfg.get("orchestratorRules") != ORCHESTRATOR_RULES:
        cfg["orchestratorRules"] = ORCHESTRATOR_RULES
        changed = True

    if not changed:
        return False

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
    ap.add_argument("--team", required=True, help="Linear team key (e.g. ABC)")
    ap.add_argument("--repo", default=str(Path.home() / "repos" / "your-repo"),
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
