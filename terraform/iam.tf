data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "devbox" {
  name               = "devbox-instance-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# SSM Session Manager as an SSH fallback — lets you reach the box even if
# the security group locks SSH out (e.g., your home IP changed).
resource "aws_iam_role_policy_attachment" "ssm_managed" {
  role       = aws_iam_role.devbox.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "s3_backup" {
  statement {
    sid     = "ListBucket"
    actions = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.backup.arn]
  }
  statement {
    sid     = "ObjectRW"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.backup.arn}/*"]
  }
}

resource "aws_iam_policy" "s3_backup" {
  name   = "devbox-s3-backup"
  policy = data.aws_iam_policy_document.s3_backup.json
}

resource "aws_iam_role_policy_attachment" "s3_backup" {
  role       = aws_iam_role.devbox.name
  policy_arn = aws_iam_policy.s3_backup.arn
}

# Read-only access to SSM Parameter Store under /devbox/* for secrets
# (Anthropic API keys, GitHub tokens, etc.). Write them out-of-band via the
# AWS console or CLI — never check them in.
data "aws_iam_policy_document" "ssm_params" {
  statement {
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.aws_region}:*:parameter/devbox/*"]
  }
}

resource "aws_iam_policy" "ssm_params" {
  name   = "devbox-ssm-read"
  policy = data.aws_iam_policy_document.ssm_params.json
}

resource "aws_iam_role_policy_attachment" "ssm_params" {
  role       = aws_iam_role.devbox.name
  policy_arn = aws_iam_policy.ssm_params.arn
}

resource "aws_iam_instance_profile" "devbox" {
  name = "devbox-instance-profile"
  role = aws_iam_role.devbox.name
}
