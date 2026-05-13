# devbox

A self-contained AWS dev environment as infrastructure-as-code, designed for working on a polyglot monorepo with the help of an LLM (Claude).

A single `terraform apply` provisions an EC2 t3.xlarge with:

- **Tools**: Docker, Python (+ `uv`), Node 22 LTS (+ pnpm/yarn), Terraform, AWS CLI v2, restic
- **Code search**: Sourcegraph single-container on `localhost:7080`
- **Portable state**: hourly `restic` snapshots of `~/repos`, `~/.claude`, dotfiles → S3
- **Stable address**: Elastic IP so SSH config stays put across stop/start

The catalog + MCP + Claude configuration layer is wired in next — this repo currently lands the infrastructure foundation those layers will sit on.

---

## What's in here

```
devbox/
├── Makefile                 # day-to-day commands (up, ssh, stop, start, status, logs)
├── .env.example             # laptop-side env (AWS region, ssh key, etc.)
├── terraform/
│   ├── versions.tf          # provider pins
│   ├── variables.tf         # tunables
│   ├── main.tf              # VPC data lookups, EC2, EBS, EIP, SG
│   ├── iam.tf               # instance role + SSM/S3 policies
│   ├── s3.tf                # restic backup bucket (versioned, encrypted, lifecycle)
│   ├── outputs.tf           # public IP, instance ID, ssh_config snippet
│   └── terraform.tfvars.example
├── bootstrap/
│   └── cloud-init.yaml      # first-boot provisioning: tools, volumes, sourcegraph, restic
├── cli/                     # `devbox` CLI (per-repo init, refresh) — placeholder
├── mcp/                     # MCP servers (catalog, sourcegraph, postgres) — placeholder
├── extractors/              # catalog auto-generators (pydeps, madge, etc.) — placeholder
├── catalog-schema/          # service.yaml schema + invariants — placeholder
└── systemd/                 # additional service units (catalog + MCP) — placeholder
```

Files in the placeholder dirs will be added in subsequent passes; the infrastructure works without them.

---

## Day 0: provision the box

One-time prerequisites on your laptop:

```bash
# 0. Install: terraform >= 1.6, awscli v2
# 1. Configure AWS credentials (`aws configure`) for the account/region you'll use.
# 2. Create an AWS EC2 key pair (or reuse one): the key_name must already exist.

cp .env.example .env                                      # edit values
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # edit values
```

The required variables are:

| Variable | Where | Notes |
|---|---|---|
| `aws_region` | tfvars + .env | Same in both |
| `key_name` | tfvars | Pre-existing AWS EC2 key pair name |
| `allowed_ssh_cidr` | tfvars | Run `curl -s ifconfig.me` then append `/32` |
| `backup_bucket_name` | tfvars | Must be globally unique |
| `SSH_PRIVATE_KEY` | .env | Path to the matching private key |

Then:

```bash
make set-restic-password   # one-time: stores a random password in SSM
make init                  # terraform init
make up                    # terraform apply
make ssh-config >> ~/.ssh/config   # optional: drop-in SSH alias
```

Cloud-init runs for 5–10 minutes after `apply`. Track it with `make logs`.

---

## Day 1: bring a repo onto the box

```bash
make ssh                   # or: ssh devbox (if you ran make ssh-config)

# on the box:
cd ~/repos
git clone git@github.com:you/app-monorepo.git
cd app-monorepo
# … (devbox init for catalog/sourcegraph/MCP setup — added in next pass)
```

The `~/repos` directory lives on the data EBS volume and is auto-backed up to S3.

---

## Day 2+: normal flow

```bash
make start          # if you stopped the box overnight
make ssh
# ... work ...
make stop           # release compute cost (preserves EBS state)
```

When the box stops, a shutdown systemd unit runs one final restic backup. When it starts, your state is already on the EBS volume — no restore needed unless the volume is destroyed.

Trigger a manual backup anytime:

```bash
make backup-now
make snapshots
```

---

## Cost

Rough monthly numbers (us-east-1, May 2026 pricing):

| Component | Cost |
|---|---|
| t3.xlarge always-on | ~$120/mo |
| t3.xlarge stopped 12h/day, weekends | ~$30/mo |
| EBS 150 GB gp3 | ~$12/mo |
| EIP (free attached, $0.005/hr unattached) | ~$0–4/mo |
| S3 backup (~10 GB dedup'd) | <$1/mo |

The `make stop` / `make start` workflow is the biggest lever. Treat the box as ephemeral *compute* over persistent *state*.

---

## Recovering from a disaster

If the EBS volume is destroyed but the S3 bucket survives:

1. `make destroy` (or just delete the EC2 + EBS in the console)
2. `make up` — a fresh box provisions, cloud-init's `restic-restore.sh` finds the snapshots and pulls them down to the new data volume.
3. SSH in — `~/repos` and `~/.claude` are exactly as you left them.

The restic password in SSM is the **one piece you cannot afford to lose**. Back it up to a password manager.

---

## Security posture

- SSH locked to a single CIDR (your IP). Override with care.
- IMDSv2 required on the instance.
- SSM Session Manager enabled as an SSH fallback (so a CIDR mistake doesn't lock you out).
- All EBS volumes encrypted at rest.
- S3 backup bucket: versioned, encrypted, public-access blocked, lifecycle-managed.
- No secrets in this repo. API keys go in SSM under `/devbox/*` and are pulled at runtime via the instance role.

---

## Roadmap (next passes)

- `cli/devbox` — per-repo bootstrap (`devbox init` for catalog + sourcegraph + MCP wiring)
- `catalog-schema/` — `service.yaml` schema + invariants
- `extractors/` — Python/Node/Terraform/DB extractors that populate `auto.yaml`
- `mcp/` — catalog-mcp, sourcegraph-mcp, postgres-mcp, terraform-mcp
- Claude Code config: project-specific MCP registration and prompt scaffolding
