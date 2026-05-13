variable "aws_region" {
  description = "AWS region the devbox lives in."
  type        = string
  default     = "us-east-1"
}

variable "owner_tag" {
  description = "Free-form owner tag applied to all resources."
  type        = string
  default     = "devbox-user"
}

variable "instance_type" {
  description = "EC2 instance type. t3.xlarge (4 vCPU / 16 GB) is the starter default."
  type        = string
  default     = "t3.xlarge"
}

variable "key_name" {
  description = "Name of an existing AWS EC2 key pair (must already exist in aws_region)."
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to reach SSH on the devbox. Default rejects everything; set to your IP/32."
  type        = string
  default     = "127.0.0.1/32"

  validation {
    condition     = can(cidrhost(var.allowed_ssh_cidr, 0))
    error_message = "allowed_ssh_cidr must be a valid CIDR block, e.g. 1.2.3.4/32."
  }
}

variable "root_volume_size_gb" {
  description = "Size of the root EBS volume in GiB."
  type        = number
  default     = 50
}

variable "data_volume_size_gb" {
  description = "Size of the /data EBS volume in GiB. Holds repos, indexes, docker volumes."
  type        = number
  default     = 100
}

variable "backup_bucket_name" {
  description = "Globally-unique S3 bucket name for restic backups."
  type        = string
}
