SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

TF := terraform -chdir=terraform

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
tunnel: ## SSH tunnel: Sourcegraph (7080), MCP scratch ports
	ssh -i $(SSH_PRIVATE_KEY) -N \
		-L 7080:localhost:7080 \
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
