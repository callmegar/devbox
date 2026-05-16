SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

TF_BIN ?= tofu
TF := $(TF_BIN) -chdir=terraform

# AWS region is needed for aws-cli operations against the running instance.
# It's also a terraform variable; resolved from .env or .tfvars.
-include .env
AWS_REGION ?= us-east-1
SSH_PRIVATE_KEY ?= ~/.ssh/id_ed25519

# Cached terraform outputs (avoids re-querying for every target).
INSTANCE_ID = $$($(TF) output -raw instance_id 2>/dev/null)
PUBLIC_IP   = $$($(TF) output -raw public_ip   2>/dev/null)

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n",$$1,$$2}'

# ---------- one-time setup ----------------------------------------------------

.PHONY: bootstrap-check
bootstrap-check: ## Print the manual steps you must do before `make up`
	@echo "Before running 'make up':"
	@echo "  1. cp .env.example .env  &&  fill in AWS_REGION, KEY_NAME, etc."
	@echo "  2. cp terraform/terraform.tfvars.example terraform/terraform.tfvars"
	@echo "     and edit (must set: key_name, allowed_ssh_cidr, backup_bucket_name)"
	@echo "  3. Ensure the AWS key pair named in key_name exists in your account."
	@echo "  4. Set the restic password in SSM (one-time, before first up):"
	@echo "       make set-restic-password"
	@echo "  5. make init && make up"

.PHONY: set-restic-password
set-restic-password: ## Generate a restic password and store it in SSM (idempotent)
	@if aws ssm get-parameter --name /devbox/restic-password --region $(AWS_REGION) >/dev/null 2>&1; then \
		echo "/devbox/restic-password already exists in SSM. Not overwriting."; \
	else \
		pw=$$(openssl rand -base64 32); \
		aws ssm put-parameter --name /devbox/restic-password --type SecureString \
			--value "$$pw" --region $(AWS_REGION) >/dev/null; \
		echo "Stored /devbox/restic-password in SSM ($(AWS_REGION))."; \
		echo "Back it up somewhere safe — losing it makes your S3 backups unrecoverable."; \
	fi

.PHONY: upload-sourcegraph-token
upload-sourcegraph-token: ## Upload a Sourcegraph access token to SSM (TOKEN=... or interactive prompt)
	@if [ -n "$(TOKEN)" ]; then \
	  T="$(TOKEN)"; \
	else \
	  read -p "Sourcegraph access token: " -s T; echo; \
	fi; \
	if [ -z "$$T" ]; then echo "no token provided"; exit 1; fi; \
	aws ssm put-parameter --name /devbox/sourcegraph-token --type SecureString --overwrite \
		--value "$$T" --region $(AWS_REGION) >/dev/null; \
	echo "Stored /devbox/sourcegraph-token in SSM ($(AWS_REGION))."; \
	echo "Mint one via: make tunnel  ->  http://localhost:7080  ->  Settings -> Access tokens."

.PHONY: init-ao-config
init-ao-config: SCRIPT = bootstrap/scripts/init-ao-config.py
init-ao-config: REMOTE = /opt/devbox/scripts/init-ao-config.py
init-ao-config: REPO ?= /home/ubuntu/repos/your-repo
init-ao-config: CHANNEL ?= agent-updates
init-ao-config: BRANCH ?=
init-ao-config: ## Merge Slack notifier (global) + Linear tracker (per-project) into ao config (TEAM=ABC [REPO=...] [CHANNEL=name] [BRANCH=develop]). CHANNEL takes the channel name without `#`.
	@if [ -z "$(TEAM)" ]; then echo "usage: make init-ao-config TEAM=<linear-team-key> [REPO=path] [CHANNEL=channel-name] [BRANCH=develop]"; exit 1; fi
	@[ -f $(SCRIPT) ] || (echo "$(SCRIPT) not found"; exit 1)
	@BRANCH_ARG=""; if [ -n "$(BRANCH)" ]; then BRANCH_ARG="--default-branch $(BRANCH)"; fi; \
	B64=$$(base64 < $(SCRIPT) | tr -d '\n'); \
	printf '%s\n' \
	  '{"commands":[' \
	  '"sudo mkdir -p /opt/devbox/scripts",' \
	  "\"echo $$B64 | base64 -d | sudo tee $(REMOTE) > /dev/null\"," \
	  "\"sudo chmod 0755 $(REMOTE)\"," \
	  "\"sudo -u ubuntu env AWS_REGION=$(AWS_REGION) /home/ubuntu/.local/bin/uv run --script $(REMOTE) --team $(TEAM) --repo $(REPO) --channel $(CHANNEL) $$BRANCH_ARG\"" \
	  ']}' > /tmp/devbox-init-ao.json
	@CMD=$$(aws ssm send-command --region $(AWS_REGION) --instance-ids $(INSTANCE_ID) --document-name AWS-RunShellScript --parameters file:///tmp/devbox-init-ao.json --query Command.CommandId --output text); \
	echo "command: $$CMD"; \
	aws ssm wait command-executed --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) || true; \
	aws ssm get-command-invocation --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) --query StandardOutputContent --output text

.PHONY: upload-linear-api-token
upload-linear-api-token: ## Upload a Linear personal API key to SSM (TOKEN=... or interactive prompt). Wrapper at /usr/local/bin/ao exports it as LINEAR_API_KEY.
	@if [ -n "$(TOKEN)" ]; then \
	  T="$(TOKEN)"; \
	else \
	  read -p "Linear personal API key: " -s T; echo; \
	fi; \
	if [ -z "$$T" ]; then echo "no token provided"; exit 1; fi; \
	aws ssm put-parameter --name /devbox/linear-api-key --type SecureString --overwrite \
		--value "$$T" --region $(AWS_REGION) >/dev/null; \
	echo "Stored /devbox/linear-api-key in SSM ($(AWS_REGION))."; \
	echo "Mint one via: https://linear.app/settings/api  ->  Personal API keys."

.PHONY: upload-linear-default-team
upload-linear-default-team: ## Set the default Linear team key in SSM (TEAM=MAT). Lets devbox-linear-mcp omit team= in tool calls.
	@if [ -z "$(TEAM)" ]; then echo "usage: make upload-linear-default-team TEAM=<KEY>"; exit 1; fi
	@aws ssm put-parameter --name /devbox/linear-default-team-key --type String --overwrite \
		--value "$(TEAM)" --region $(AWS_REGION) >/dev/null
	@echo "Stored /devbox/linear-default-team-key=$(TEAM) in SSM ($(AWS_REGION))."

.PHONY: upload-slack-webhook
upload-slack-webhook: ## Upload a Slack incoming-webhook URL to SSM (URL=... or interactive prompt). Wrapper at /usr/local/bin/ao exports it as SLACK_WEBHOOK_URL.
	@if [ -n "$(URL)" ]; then \
	  U="$(URL)"; \
	else \
	  read -p "Slack incoming-webhook URL: " -s U; echo; \
	fi; \
	if [ -z "$$U" ]; then echo "no URL provided"; exit 1; fi; \
	aws ssm put-parameter --name /devbox/slack-webhook-url --type SecureString --overwrite \
		--value "$$U" --region $(AWS_REGION) >/dev/null; \
	echo "Stored /devbox/slack-webhook-url in SSM ($(AWS_REGION))."; \
	echo "Mint one via: https://api.slack.com/apps  ->  Create App  ->  Incoming Webhooks."

.PHONY: set-sourcegraph-default-repo
set-sourcegraph-default-repo: ## Scope sourcegraph-mcp queries to a default repo (REPO=your-org/your-repo). Empty REPO removes the pin.
	@if [ -z "$(REPO)" ]; then \
	  aws ssm delete-parameter --name /devbox/sourcegraph-default-repo --region $(AWS_REGION) 2>/dev/null && echo "Removed /devbox/sourcegraph-default-repo (no default repo)" || echo "/devbox/sourcegraph-default-repo was not set"; \
	else \
	  aws ssm put-parameter --name /devbox/sourcegraph-default-repo --type String --overwrite \
	    --value "$(REPO)" --region $(AWS_REGION) >/dev/null; \
	  echo "Stored /devbox/sourcegraph-default-repo=$(REPO). sourcegraph-mcp queries now inject repo:$(REPO) by default."; \
	fi

.PHONY: set-sourcegraph-default-rev
set-sourcegraph-default-rev: ## Pin sourcegraph-mcp searches to a default branch/rev (REV=develop). Empty REV removes the pin.
	@if [ -z "$(REV)" ]; then \
	  aws ssm delete-parameter --name /devbox/sourcegraph-default-rev --region $(AWS_REGION) 2>/dev/null && echo "Removed /devbox/sourcegraph-default-rev (no default rev)" || echo "/devbox/sourcegraph-default-rev was not set"; \
	else \
	  aws ssm put-parameter --name /devbox/sourcegraph-default-rev --type String --overwrite \
	    --value "$(REV)" --region $(AWS_REGION) >/dev/null; \
	  echo "Stored /devbox/sourcegraph-default-rev=$(REV). sourcegraph-mcp queries now inject rev:$(REV) by default."; \
	fi

.PHONY: upload-aws-credentials
upload-aws-credentials: AWS_LOCAL_PROFILE ?= pablo
upload-aws-credentials: ## Upload AWS keys to SSM for the box's pablo profile. Auto-pulls from local ~/.aws (AWS_LOCAL_PROFILE=pablo); override with TOKEN=AKIA... SECRET=...
	@if [ -n "$(TOKEN)" ] && [ -n "$(SECRET)" ]; then \
	  K="$(TOKEN)"; S="$(SECRET)"; SRC="overrides"; \
	else \
	  K=$$(aws configure get aws_access_key_id --profile $(AWS_LOCAL_PROFILE) 2>/dev/null || true); \
	  S=$$(aws configure get aws_secret_access_key --profile $(AWS_LOCAL_PROFILE) 2>/dev/null || true); \
	  SRC="local profile '$(AWS_LOCAL_PROFILE)'"; \
	fi; \
	if [ -z "$$K" ] || [ -z "$$S" ]; then \
	  echo "no credentials found. Pass TOKEN=AKIA... SECRET=... or configure profile $(AWS_LOCAL_PROFILE) in ~/.aws/credentials"; \
	  exit 1; \
	fi; \
	aws ssm put-parameter --name /devbox/aws-access-key-id     --type SecureString --overwrite --value "$$K" --region $(AWS_REGION) >/dev/null; \
	aws ssm put-parameter --name /devbox/aws-secret-access-key --type SecureString --overwrite --value "$$S" --region $(AWS_REGION) >/dev/null; \
	echo "Stored /devbox/aws-access-key-id + /devbox/aws-secret-access-key in SSM ($(AWS_REGION)) from $$SRC."; \
	echo "The box resolves these via credential_process when AWS_PROFILE=pablo."

.PHONY: upload-gitlab-api-token
upload-gitlab-api-token: ## Upload a GitLab personal access token to SSM (TOKEN=... or interactive prompt). Min scopes: read_api+read_repository for indexing; api for `glab mr create`.
	@if [ -n "$(TOKEN)" ]; then \
	  T="$(TOKEN)"; \
	else \
	  read -p "GitLab personal access token (scope: api for MR creation, else read_api+read_repository): " -s T; echo; \
	fi; \
	if [ -z "$$T" ]; then echo "no token provided"; exit 1; fi; \
	aws ssm put-parameter --name /devbox/gitlab-api-token --type SecureString --overwrite \
		--value "$$T" --region $(AWS_REGION) >/dev/null; \
	echo "Stored /devbox/gitlab-api-token in SSM ($(AWS_REGION))."; \
	echo "Mint one via: https://gitlab.com/-/user_settings/personal_access_tokens"

.PHONY: configure-sourcegraph-gitlab
configure-sourcegraph-gitlab: SCRIPT = bootstrap/scripts/configure-sourcegraph-gitlab.py
configure-sourcegraph-gitlab: REMOTE = /opt/devbox/scripts/configure-sourcegraph-gitlab.py
configure-sourcegraph-gitlab: GITLAB_URL ?= https://gitlab.com
configure-sourcegraph-gitlab: ## Register GitLab repos with Sourcegraph (REPOS="namespace/repo [..]" [GITLAB_URL=...])
	@if [ -z "$(REPOS)" ]; then echo "usage: make configure-sourcegraph-gitlab REPOS=\"namespace/repo [namespace/repo2 ...]\""; exit 1; fi
	@[ -f $(SCRIPT) ] || (echo "$(SCRIPT) not found"; exit 1)
	@B64=$$(base64 < $(SCRIPT) | tr -d '\n'); \
	printf '%s\n' \
	  '{"commands":[' \
	  '"sudo mkdir -p /opt/devbox/scripts",' \
	  "\"echo $$B64 | base64 -d | sudo tee $(REMOTE) > /dev/null\"," \
	  "\"sudo chmod 0755 $(REMOTE)\"," \
	  "\"sudo -u ubuntu env AWS_REGION=$(AWS_REGION) python3 $(REMOTE) --url $(GITLAB_URL) $(REPOS)\"" \
	  ']}' > /tmp/devbox-cfg-sg.json
	@CMD=$$(aws ssm send-command --region $(AWS_REGION) --instance-ids $(INSTANCE_ID) --document-name AWS-RunShellScript --parameters file:///tmp/devbox-cfg-sg.json --query Command.CommandId --output text); \
	echo "command: $$CMD"; \
	aws ssm wait command-executed --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) || true; \
	aws ssm get-command-invocation --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) --query StandardOutputContent --output text

.PHONY: sourcegraph-repos
sourcegraph-repos: SCRIPT = bootstrap/scripts/list-sourcegraph-repos.sh
sourcegraph-repos: REMOTE = /opt/devbox/scripts/list-sourcegraph-repos.sh
sourcegraph-repos: ## List repos Sourcegraph has cloned/indexed
	@[ -f $(SCRIPT) ] || (echo "$(SCRIPT) not found"; exit 1)
	@B64=$$(base64 < $(SCRIPT) | tr -d '\n'); \
	JSON=$$(printf '{"commands":["sudo mkdir -p /opt/devbox/scripts","echo %s | base64 -d | sudo tee %s > /dev/null","sudo chmod 0755 %s","sudo -u ubuntu %s"]}' "$$B64" "$(REMOTE)" "$(REMOTE)" "$(REMOTE)"); \
	CMD=$$(aws ssm send-command --region $(AWS_REGION) --instance-ids $(INSTANCE_ID) --document-name AWS-RunShellScript --parameters "$$JSON" --query Command.CommandId --output text); \
	aws ssm wait command-executed --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) || true; \
	aws ssm get-command-invocation --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) --query StandardOutputContent --output text

.PHONY: upload-gitlab-key
upload-gitlab-key: KEY_FILE ?= ~/.ssh/id_ed25519
upload-gitlab-key: ## Upload GitLab SSH key (private+public) to SSM under /devbox/ssh-keys/gitlab
	@if [ ! -f $(KEY_FILE) ]; then echo "private key not found: $(KEY_FILE)"; exit 1; fi
	@if [ ! -f $(KEY_FILE).pub ]; then echo "public key not found: $(KEY_FILE).pub"; exit 1; fi
	@echo "Uploading $(KEY_FILE) -> /devbox/ssh-keys/gitlab (SecureString)"
	@aws ssm put-parameter --name /devbox/ssh-keys/gitlab --type SecureString --overwrite \
		--value "$$(cat $(KEY_FILE))" --region $(AWS_REGION) >/dev/null
	@echo "Uploading $(KEY_FILE).pub -> /devbox/ssh-keys/gitlab.pub"
	@aws ssm put-parameter --name /devbox/ssh-keys/gitlab.pub --type String --overwrite \
		--value "$$(cat $(KEY_FILE).pub)" --region $(AWS_REGION) >/dev/null
	@echo "Done. Run 'make sync-ssh-keys' to install on the running box."

.PHONY: sync-ssh-keys
sync-ssh-keys: SCRIPT = bootstrap/scripts/setup-ssh-keys.sh
sync-ssh-keys: REMOTE = /opt/devbox/scripts/setup-ssh-keys.sh
sync-ssh-keys: ## Push latest setup-ssh-keys.sh to the box and run it
	@[ -f $(SCRIPT) ] || (echo "$(SCRIPT) not found"; exit 1)
	@B64=$$(base64 < $(SCRIPT) | tr -d '\n'); \
	JSON=$$(printf '{"commands":["echo %s | base64 -d | sudo tee %s > /dev/null","sudo chmod 0755 %s","sudo %s"]}' "$$B64" "$(REMOTE)" "$(REMOTE)" "$(REMOTE)"); \
	CMD=$$(aws ssm send-command --region $(AWS_REGION) --instance-ids $(INSTANCE_ID) \
		--document-name AWS-RunShellScript --parameters "$$JSON" \
		--query Command.CommandId --output text); \
	echo "command: $$CMD"; \
	aws ssm wait command-executed --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID); \
	aws ssm get-command-invocation --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) --query StandardOutputContent --output text

.PHONY: sync-mcp-config
sync-mcp-config: SCRIPT = bootstrap/scripts/sync-mcp-config.py
sync-mcp-config: REMOTE = /opt/devbox/scripts/sync-mcp-config.py
sync-mcp-config: ## Register devbox MCP servers in user-scope ~/.claude.json on the box
	@[ -f $(SCRIPT) ] || (echo "$(SCRIPT) not found"; exit 1)
	@B64=$$(base64 < $(SCRIPT) | tr -d '\n'); \
	JSON=$$(printf '{"commands":["sudo mkdir -p /opt/devbox/scripts","echo %s | base64 -d | sudo tee %s > /dev/null","sudo chmod 0755 %s","sudo -u ubuntu python3 %s"]}' "$$B64" "$(REMOTE)" "$(REMOTE)" "$(REMOTE)"); \
	CMD=$$(aws ssm send-command --region $(AWS_REGION) --instance-ids $(INSTANCE_ID) \
		--document-name AWS-RunShellScript --parameters "$$JSON" \
		--query Command.CommandId --output text); \
	echo "command: $$CMD"; \
	aws ssm wait command-executed --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID); \
	aws ssm get-command-invocation --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) --query StandardOutputContent --output text

.PHONY: sync-tooling
sync-tooling: ## Package cli/ tooling, push to the box via S3, uv sync, install `devbox`, register MCP servers
	@[ -d cli ] || (echo "cli/ not found"; exit 1)
	@tar czf /tmp/devbox-tooling.tar.gz -C cli .
	@aws s3 cp /tmp/devbox-tooling.tar.gz s3://$(BACKUP_BUCKET)/tooling/devbox-tooling.tar.gz --region $(AWS_REGION) >/dev/null
	@echo "uploaded tooling to s3://$(BACKUP_BUCKET)/tooling/"
	@printf '%s\n' \
	  '{"commands":[' \
	  '"aws s3 cp s3://$(BACKUP_BUCKET)/tooling/devbox-tooling.tar.gz /tmp/dt.tar.gz --region $(AWS_REGION)",' \
	  '"rm -rf /opt/devbox/tooling && mkdir -p /opt/devbox/tooling",' \
	  '"tar xzf /tmp/dt.tar.gz -C /opt/devbox/tooling",' \
	  '"chown -R ubuntu:ubuntu /opt/devbox/tooling",' \
	  '"mkdir -p /data/home/.devbox && chown ubuntu:ubuntu /data/home/.devbox",' \
	  '"sudo -u ubuntu bash -c \"test -L ~/.devbox || ln -sfn /data/home/.devbox ~/.devbox\"",' \
	  '"sudo -u ubuntu bash -lc \"cd /opt/devbox/tooling && /home/ubuntu/.local/bin/uv sync --quiet\"",' \
	  '"install -m 0755 /opt/devbox/tooling/devbox.sh /usr/local/bin/devbox",' \
	  '"install -m 0755 /opt/devbox/tooling/devbox-catalog-mcp.sh /usr/local/bin/devbox-catalog-mcp",' \
	  '"install -m 0755 /opt/devbox/tooling/devbox-sourcegraph-mcp.sh /usr/local/bin/devbox-sourcegraph-mcp",' \
	  '"install -m 0755 /opt/devbox/tooling/devbox-linear-mcp.sh /usr/local/bin/devbox-linear-mcp",' \
	  '"sudo -u ubuntu /usr/local/bin/devbox --help >/dev/null 2>&1 && echo \"tooling installed ok\" || echo \"tooling install FAILED\""' \
	  ']}' > /tmp/devbox-sync-tooling.json
	@CMD=$$(aws ssm send-command --region $(AWS_REGION) --instance-ids $(INSTANCE_ID) \
		--document-name AWS-RunShellScript --parameters file:///tmp/devbox-sync-tooling.json \
		--query Command.CommandId --output text); \
	echo "command: $$CMD"; \
	aws ssm wait command-executed --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) || true; \
	aws ssm get-command-invocation --region $(AWS_REGION) --command-id $$CMD --instance-id $(INSTANCE_ID) --query StandardOutputContent --output text
	@$(MAKE) sync-mcp-config

# ---------- terraform lifecycle -----------------------------------------------

.PHONY: init
init: ## terraform init
	$(TF) init

.PHONY: plan
plan: ## terraform plan
	$(TF) plan

.PHONY: up
up: ## terraform apply — create/update the devbox
	$(TF) apply
	@echo
	@echo "Devbox provisioning kicked off. Cloud-init runs for ~5-10 minutes after apply."
	@echo "Watch progress: make logs"
	@echo "SSH config:     make ssh-config >> ~/.ssh/config"

.PHONY: destroy
destroy: ## terraform destroy — destroys EC2 + EBS + EIP. S3 bucket is protected.
	@echo "This destroys the EC2 instance and EBS volumes. S3 backup state is preserved."
	@read -p "Type 'destroy' to confirm: " ans && [ "$$ans" = "destroy" ] || (echo "aborted"; exit 1)
	$(TF) destroy

# ---------- runtime lifecycle (cheap stop/start, no terraform) ---------------

.PHONY: stop
stop: ## Stop the EC2 instance (preserves EBS, releases compute cost)
	aws ec2 stop-instances --instance-ids $(INSTANCE_ID) --region $(AWS_REGION)

.PHONY: start
start: ## Start the EC2 instance
	aws ec2 start-instances --instance-ids $(INSTANCE_ID) --region $(AWS_REGION)
	@echo "Waiting for instance to reach 'running' state..."
	aws ec2 wait instance-running --instance-ids $(INSTANCE_ID) --region $(AWS_REGION)
	@echo "Running. EIP: $(PUBLIC_IP)"

.PHONY: status
status: ## Show EC2 state + EIP
	@aws ec2 describe-instances --instance-ids $(INSTANCE_ID) --region $(AWS_REGION) \
		--query 'Reservations[0].Instances[0].{State:State.Name,Type:InstanceType,IP:PublicIpAddress,Launch:LaunchTime}' \
		--output table

# ---------- access ------------------------------------------------------------

.PHONY: ssh
ssh: ## SSH to the devbox
	ssh -i $(SSH_PRIVATE_KEY) -o IdentitiesOnly=yes ubuntu@$(PUBLIC_IP)

.PHONY: ssh-config
ssh-config: ## Print drop-in ~/.ssh/config block
	@$(TF) output -raw ssh_config

.PHONY: tunnel
tunnel: ## SSH tunnel: Sourcegraph (7080), ao dashboard (3000) + terminal WS (14800/14801), MCP scratch ports
	ssh -i $(SSH_PRIVATE_KEY) -N \
		-L 7080:localhost:7080 \
		-L 3000:localhost:3000 \
		-L 14800:localhost:14800 \
		-L 14801:localhost:14801 \
		-L 6070:localhost:6070 \
		ubuntu@$(PUBLIC_IP)

# ---------- observability -----------------------------------------------------

.PHONY: logs
logs: ## Tail the cloud-init / setup log on the box
	ssh -i $(SSH_PRIVATE_KEY) -o IdentitiesOnly=yes ubuntu@$(PUBLIC_IP) \
		'sudo tail -f /var/log/devbox-setup.log /var/log/cloud-init-output.log'

.PHONY: backup-now
backup-now: ## Trigger an immediate restic backup on the box
	ssh -i $(SSH_PRIVATE_KEY) -o IdentitiesOnly=yes ubuntu@$(PUBLIC_IP) \
		'sudo systemctl start restic-backup.service && sudo journalctl -u restic-backup.service -n 50 --no-pager'

.PHONY: snapshots
snapshots: ## List restic snapshots
	ssh -i $(SSH_PRIVATE_KEY) -o IdentitiesOnly=yes ubuntu@$(PUBLIC_IP) \
		'sudo /usr/local/bin/devbox-restic snapshots'
