# Use the account's default VPC for the starter — one fewer thing to manage.
# To graduate to a dedicated VPC later, swap these data sources for `aws_vpc` /
# `aws_subnet` resources without touching the EC2/SG references.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Always-latest Ubuntu 24.04 LTS AMI via SSM Parameter Store.
data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_security_group" "devbox" {
  name        = "devbox-sg"
  description = "Devbox: SSH from operator only. All other dev services tunnel over SSH."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH from operator"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Render cloud-init at apply time, injecting the bucket name + region so the
# box knows where to push/pull restic state without needing instance metadata
# lookups in shell scripts.
locals {
  cloud_init = templatefile("${path.module}/../bootstrap/cloud-init.yaml", {
    backup_bucket               = var.backup_bucket_name
    aws_region                  = var.aws_region
    setup_ssh_keys_script       = file("${path.module}/../bootstrap/scripts/setup-ssh-keys.sh")
    install_closedloop_script   = file("${path.module}/../bootstrap/scripts/install-closedloop.sh")
    install_local_review_script = file("${path.module}/../bootstrap/scripts/install-local-review.sh")
    setup_tmux_script           = file("${path.module}/../bootstrap/scripts/setup-tmux.sh")
    devbox_tmux_attach_script   = file("${path.module}/../bootstrap/scripts/devbox-tmux-attach.sh")
    aws_credentials_script      = file("${path.module}/../bootstrap/scripts/devbox-aws-credentials.sh")
  })
}

resource "aws_instance" "devbox" {
  ami                    = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.devbox.id]
  iam_instance_profile   = aws_iam_instance_profile.devbox.name
  # gzip+base64 to fit larger cloud-init (raw limit is 16 KB; gzipped limit is 64 KB).
  # cloud-init transparently decompresses on first boot.
  user_data_base64       = base64gzip(local.cloud_init)
  user_data_replace_on_change = false

  # IMDSv2 required — blocks SSRF-style metadata exfiltration.
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "devbox-root" }
  }

  tags = { Name = "devbox" }

  lifecycle {
    ignore_changes = [
      # Avoid forced replacement when AWS rotates the SSM-published AMI.
      ami,
      # Don't stop/start a running box just to update user_data — AWS requires
      # the instance be stopped to modify it. cloud-init only runs on first
      # boot anyway; push script changes to live boxes via `make sync-*`
      # targets instead. New boxes still get the current cloud-init at launch.
      user_data,
      user_data_base64,
    ]
  }
}

# Separate data volume so the root AMI can be replaced without touching state.
# Holds /home, repos, sourcegraph indexes, docker volumes.
resource "aws_ebs_volume" "data" {
  availability_zone = aws_instance.devbox.availability_zone
  size              = var.data_volume_size_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "devbox-data" }
}

resource "aws_volume_attachment" "data" {
  device_name  = "/dev/sdf"
  volume_id    = aws_ebs_volume.data.id
  instance_id  = aws_instance.devbox.id
  stop_instance_before_detaching = true
}

# Stable public IP so SSH config doesn't churn on stop/start.
resource "aws_eip" "devbox" {
  domain   = "vpc"
  instance = aws_instance.devbox.id
  tags     = { Name = "devbox-eip" }
}
