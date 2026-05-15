# Contributing

Thanks for considering a contribution. This project is a personal infra setup that was useful enough to publish — the upstream is small, expectations are pragmatic, and patches that genericize or extend it are welcome.

## Scope, briefly

devbox provisions an AWS EC2 dev box opinionated around:

- **GitLab** as the SCM (Sourcegraph code-host config, outbound SSH keys, project paths).
- **Linear** as the task tracker (ao integration).
- **Slack** as the notifier (incoming webhooks).
- **OpenTofu** (`tofu`) over Terraform.

If you want to swap any of those (GitHub, Jira, Discord, etc.), the integration points are isolated: the relevant `bootstrap/scripts/*.py` config-writers and Makefile targets per service. PRs that add alternate adapters in parallel — without ripping out the GitLab/Linear/Slack paths — are easier to merge than PRs that replace them wholesale.

## Dev setup

```bash
git clone <your fork>
cp .env.example .env                                              # edit values
cp terraform/terraform.tfvars.example terraform/terraform.tfvars  # edit values
```

You'll need: OpenTofu ≥ 1.6, AWS CLI v2, an AWS account, an EC2 key pair, and one S3 bucket name unused by anyone else on the planet.

The Python catalog tooling under `cli/` uses [uv](https://github.com/astral-sh/uv):

```bash
cd cli
uv sync
uv run python -m devbox_catalog --help
```

There aren't automated tests at the moment — the project has been validated end-to-end on the live box. Test changes by provisioning a real box (`make up`) and exercising the relevant target. If you add tests, that's great.

## Code style

- **Python**: 4-space indent, type hints encouraged, prefer the stdlib over new dependencies (the catalog server uses only `pyyaml` + `mcp`; the sourcegraph server only `mcp`). `from __future__ import annotations` at the top of every module.
- **Bash**: shellcheck-clean. `set -euo pipefail` at the top of every script. Quote variables.
- **YAML**: 2-space indent, `$schema:` directive when there's one available.
- **Comments**: explain the WHY (constraints, surprises, history), not the WHAT. The existing codebase is comment-heavy on purpose — context for the next reader matters more than line economy.
- **Commit messages**: descriptive single-line subject + a body that explains *why* the change is needed and what was verified. See existing `git log` for the style.

## What kind of PRs are welcome

- Bug fixes (especially around the cloud-init lifecycle).
- New integrations behind feature flags or as alternate Makefile targets (e.g. GitHub instead of GitLab).
- New MCP servers under `cli/` following the same wrapper pattern as `catalog-mcp` and `sourcegraph-mcp`.
- Documentation fixes — the README is the user-facing source of truth.
- Genericizing further (the original project was tuned for one specific monorepo; if you spot residual leakage, send a fix).

## What's deferred / under-baked

If you want a starting point, the open work listed at the bottom of `README.md` (Investigation #3, postgres-mcp, GitLab webhook routing for ao's auto-react) is fair game.

## Filing issues

Reproducer first, hypothesis second. A copy-pasted `make logs` excerpt + the `make` command that triggered the failure is usually enough.

## License

By contributing, you agree your contribution will be licensed under the MIT License (see `LICENSE`).
