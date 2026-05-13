output "public_ip" {
  description = "Elastic IP of the devbox. Stable across stop/start."
  value       = aws_eip.devbox.public_ip
}

output "instance_id" {
  description = "EC2 instance ID — pass to `aws ec2 start/stop-instances`."
  value       = aws_instance.devbox.id
}

output "backup_bucket" {
  description = "S3 bucket holding restic state."
  value       = aws_s3_bucket.backup.id
}

output "ssh_config" {
  description = "Drop-in ~/.ssh/config block for the devbox."
  value       = <<-EOT
    Host devbox
      HostName ${aws_eip.devbox.public_ip}
      User ubuntu
      IdentitiesOnly yes
      ServerAliveInterval 30
      ServerAliveCountMax 4
  EOT
}
