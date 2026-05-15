# devbox

A self-contained AWS dev environment, provisioned by OpenTofu + cloud-init, designed for working on a polyglot monorepo with the help of Claude (Code, plus an agent-orchestrator on top). Re-creatable from scratch in ~10 minutes; durable state lives on EBS + restic-encrypted S3 snapshots.

A single `tofu apply` provisions an EC2 t3.xlarge with:

- **Tools**: Docker, Python (+ `uv`), Node 22 LTS (+ pnpm/yarn), Terraform, AWS CLI v2, restic, tmux.
- **Claude Code** (`claude`) — native install, subscription auth via copy-paste device-code flow.
- **MCP layer** — `catalog-mcp` (devbox-owned, exposes a system catalog over the repo) and `sourcegraph-mcp` (wraps the local Sourcegraph). Registered at user scope in `~/.claude.json` so every `claude` session in any repo picks them up.
- **Agent orchestrator** (`ao`) — parallel Claude workers in their own git worktrees, dashboard at `localhost:3000`, Slack + Linear integrations.
- **Code search**: Sourcegraph single-container on `localhost:7080`.
- **Portable state**: hourly `restic` snapshots of `~/repos`, `~/.claude`, `~/.config`, `~/.local`, `~/.agent-orchestrator` → S3. Survives `tofu destroy && tofu apply`.
- **Stable address**: Elastic IP so SSH config stays put across stop/start.

---

## What's in here

```
devbox/
├── Makefile                 # day-to-day commands — `make help` lists targets
├── .env.example             # laptop-side env (AWS region, ssh key, etc.)
├── terraform/               # OpenTofu IaC
├── bootstrap/
│   ├── cloud-init.yaml      # first-boot provisioning (everything below)
│   └── scripts/             # SSM-pushed scripts: ssh-keys, sourcegraph code-host, ao config
├── cli/
│   ├── devbox_catalog/      # devbox CLI + catalog-mcp server
│   ├── devbox_sourcegraph/  # sourcegraph-mcp server
│   ├── devbox.sh            # /usr/local/bin/devbox wrapper
│   ├── devbox-catalog-mcp.sh
│   └── devbox-sourcegraph-mcp.sh
└── catalog-schema/          # catalog JSON schema
```

---

## Day 0 — provision the box

One-time prerequisites on your laptop:

```bash
# OpenTofu (tofu) >= 1.6 and awscli v2 installed.
# `aws configure` (or env vars) for the account + region you'll use.
# An EC2 key pair pre-created in that region — its name goes in tfvars.

cp .env.example .env                                              # edit values
cp terraform/terraform.tfvars.example terraform/terraform.tfvars  # edit values
```

Required variables:

| Variable | Where | Notes |
|---|---|---|
| `aws_region` | tfvars + .env | Same in both |
| `key_name` | tfvars | Pre-existing AWS EC2 key pair name |
| `allowed_ssh_cidr` | tfvars | `curl -s ifconfig.me` + `/32` |
| `backup_bucket_name` | tfvars | Globally unique |
| `SSH_PRIVATE_KEY` | .env | Path to the matching private key |
| `BACKUP_BUCKET` | .env | Match `backup_bucket_name` from tfvars |

Then:

```bash
make set-restic-password   # one-time: stash a random restic password in SSM
make init                  # tofu init
make up                    # tofu apply  (cloud-init runs for ~5–10 min after)
make ssh-config >> ~/.ssh/config    # optional: drop-in `ssh devbox` alias
make logs                  # tail /var/log/devbox-setup.log on the box
```

Cloud-init installs everything in the "tools" list above. Watch `make logs` until it reports `setup-* steps complete`.

---

## Day 1 — secrets, integrations, first login

The box ships ready to *boot* but most integrations need credentials you mint by hand and stash in SSM. Each `upload-*` target prompts (hidden input) and stores under `/devbox/...` SecureString.

### One-time secrets

```bash
# Outbound GitLab SSH key — for git clone over ssh from the box.
# Uploads ~/.ssh/id_ed25519{,.pub} (override with KEY_FILE=).
make upload-gitlab-key
make sync-ssh-keys                    # install onto the running box

# Sourcegraph admin token.
#   make tunnel (in another terminal) → open http://localhost:7080 →
#   sign up / sign in → avatar → Settings → Access tokens → generate.
make upload-sourcegraph-token

# GitLab personal access token (read_api + read_repository scopes).
#   https://gitlab.com/-/user_settings/personal_access_tokens
make upload-gitlab-api-token

# Linear personal API key (for ao's Linear tracker plugin).
#   https://linear.app/settings/api
make upload-linear-api-token

# Slack incoming webhook URL.
#   https://api.slack.com/apps → Create App → Incoming Webhooks →
#   Add to Workspace → pick the channel → copy URL.
make upload-slack-webhook
```

### Wire up code search

```bash
# Tell Sourcegraph to clone + index a GitLab repo. Repeat to add more.
make configure-sourcegraph-gitlab REPOS=your-org/your-repo

# Watch the clone complete (~30s for a small repo).
make sourcegraph-repos      # expect cloned=True

# Pin sourcegraph-mcp queries to a default repo + working branch so
# `find_symbol("Foo")` doesn't need explicit repo:/rev: filters.
make set-sourcegraph-default-repo REPO=your-org/your-repo
make set-sourcegraph-default-rev REV=develop
```

### Wire up agent-orchestrator + notifiers + tracker

```bash
# On the box (e.g. via `make ssh`):
cd ~/repos/your-repo
ao start          # auto-generates agent-orchestrator.yaml on first run
                  # (do this once; `ao start` again later picks up the existing config)
```

From your laptop:

```bash
# Merge Slack notifier (global config) + Linear tracker (per-project) +
# pin defaultBranch. Idempotent — re-run safely after editing.
make init-ao-config TEAM=ABC BRANCH=develop
```

The Slack webhook lands in `~/.agent-orchestrator/config.yaml` (user-scope on the box, never committed). The Linear team key gets resolved to its UUID via the Linear GraphQL API. The per-project YAML in the target repo ends up with just `tracker:` + `agentRules:`.

### Claude Code first login

```bash
make ssh

# on the box
claude
# Press 'c' to copy the login URL → open it on your laptop → sign in to
# claude.ai → paste the code Claude shows you back into the ssh session.
```

`~/.claude/.credentials.json` is written; restic backs it up. Re-creating the box from scratch (`tofu destroy && tofu apply`) restores it via restic — no re-login needed.

### Open the dashboards (optional)

```bash
# Laptop side. Tunnels Sourcegraph (7080), ao (3000), MCP scratch (6070).
make tunnel
# Browse:  http://localhost:7080  (Sourcegraph)
#          http://localhost:3000  (ao dashboard)
```

---

## Day 2+ — normal flow

```bash
make start          # if you stopped the box overnight
make ssh
# ... work — `claude` for solo sessions; `ao` for multi-worker on issues ...
make stop           # release compute cost (preserves EBS state)
```

A systemd unit runs one last restic backup on shutdown. On startup, your EBS state is already there — no restore needed unless the volume was destroyed.

```bash
make backup-now     # trigger restic backup
make snapshots      # list restic snapshots
make status         # ec2 state + EIP
make logs           # tail cloud-init/setup logs
```

---

## The layers, briefly

**Catalog** (`devbox catalog ...` on the box, plus `catalog-mcp`)
- Discovers Python backend modules, frontends, Terraform stacks, GitLab CI jobs, Alembic migrations.
- Maps DB schemas to owning modules, computes blast radius via reverse-dep edges.
- Cached at `~/.devbox/catalog/<repo>.json`; rebuild on demand with `devbox catalog build`.
- Exposed to Claude via MCP tools: `catalog_overview`, `get_node`, `find_dependents`, `impact_of_change`, `find_node_for_file`, `db_schema_map`.

**Sourcegraph-MCP** (`sourcegraph-mcp`)
- Wraps the box-local Sourcegraph (localhost:7080) over GraphQL.
- Tools: `code_search`, `find_symbol`, `find_references`.
- Default repo + rev auto-injected from SSM; per-query override via `repo:` / `rev:` filters.

**Agent orchestrator** (`ao`)
- Spawns parallel Claude Code workers in their own git worktrees + branches.
- Issue source: Linear (or GitHub/GitLab). Notifiers: Slack, desktop, others. Dashboard at `localhost:3000`.
- Orchestrator session is read-only; worker sessions own implementation + PRs.
- All MCPs available in every spawned session automatically (they're registered at user scope, not per-project).

---

## SSM secrets reference

Everything sensitive lives in SSM SecureString under `/devbox/*`, fetched at runtime by the EC2 instance role. Nothing in this repo or in the target repo holds credentials.

| Name | Set with | Used by |
|---|---|---|
| `/devbox/restic-password` | `make set-restic-password` | restic backup/restore |
| `/devbox/ssh-keys/gitlab[.pub]` | `make upload-gitlab-key` + `make sync-ssh-keys` | outbound git clone over ssh |
| `/devbox/sourcegraph-token` | `make upload-sourcegraph-token` | sourcegraph-mcp wrapper |
| `/devbox/gitlab-api-token` | `make upload-gitlab-api-token` | `configure-sourcegraph-gitlab` |
| `/devbox/sourcegraph-default-repo` | `make set-sourcegraph-default-repo` | sourcegraph-mcp wrapper |
| `/devbox/sourcegraph-default-rev` | `make set-sourcegraph-default-rev` | sourcegraph-mcp wrapper |
| `/devbox/slack-webhook-url` | `make upload-slack-webhook` | ao wrapper + `init-ao-config` |
| `/devbox/linear-api-key` | `make upload-linear-api-token` | ao wrapper + `init-ao-config` |

---

## Recovering from a disaster

If the EBS volume is destroyed but the S3 bucket survives:

1. `make destroy` (or delete the EC2 + EBS in the console).
2. `make up` — fresh box; cloud-init's `restic-restore.sh` finds the snapshots and pulls them down to the new data volume.
3. SSH in — `~/repos`, `~/.claude`, `~/.agent-orchestrator` are exactly as you left them. Claude Code is still logged in. Sourcegraph + ao restart from where they were.

**The restic password in SSM is the one piece you cannot afford to lose.** Back it up to a password manager. (Everything else can be re-minted; restic-encrypted snapshots without the password are bricked.)

---

## Cost

Rough monthly numbers:

| Component | Cost |
|---|---|
| t3.xlarge always-on | ~$120/mo |
| t3.xlarge stopped 12h/day, weekends | ~$30/mo |
| EBS 150 GB gp3 | ~$12/mo |
| EIP (free attached, $0.005/hr unattached) | ~$0–4/mo |
| S3 backup (~10 GB dedup'd) | <$1/mo |

The `make stop` / `make start` workflow is the biggest lever. Treat the box as ephemeral *compute* over persistent *state*.

---

## Security posture

- SSH locked to a single CIDR (your IP). Override with care.
- IMDSv2 required on the instance.
- SSM Session Manager enabled as an SSH fallback (so a CIDR mistake doesn't lock you out).
- All EBS volumes encrypted at rest.
- S3 backup bucket: versioned, encrypted, public-access blocked, lifecycle-managed.
- No secrets in this repo or in the target repo — everything in SSM under `/devbox/*`, pulled at runtime via the instance role.
- Sourcegraph and ao bound to `127.0.0.1` only — reach via `make tunnel`.
