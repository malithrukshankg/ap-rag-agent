# Single-instance topology by design (architecture §12): SQLite permits one writer and the
# idempotency guarantees rely on it, so there is no load balancer or autoscaling group.

locals {
  ssm_prefix          = "/${var.project}"
  anthropic_key_param = "${local.ssm_prefix}/anthropic-api-key"
  hmac_key_param      = "${local.ssm_prefix}/payment-hmac-key"
  langfuse_pub_param  = "${local.ssm_prefix}/langfuse-public-key"
  langfuse_sec_param  = "${local.ssm_prefix}/langfuse-secret-key"
  image_uri           = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

data "aws_caller_identity" "current" {}

# Default VPC keeps the footprint small. Production path: dedicated VPC, private subnet, ALB with
# TLS and authentication in front.
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

data "aws_subnet" "selected" {
  id = sort(data.aws_subnets.default.ids)[0]
}

data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ---------------------------------------------------------------------------------------------
# Container registry
# ---------------------------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "MUTABLE"
  force_delete         = true # lets `terraform destroy` remove pushed images (cleanup, §6)

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------------------------
# Network access: API port from one CIDR only. No SSH: shell access is via SSM Session Manager.
# ---------------------------------------------------------------------------------------------

resource "aws_security_group" "app" {
  name        = "${var.project}-sg"
  description = "ap-rag-agent API from allowed_cidr only"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "api" {
  security_group_id = aws_security_group.app.id
  description       = "API (unauthenticated: restricted to one CIDR)"
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = 8000
  to_port           = 8000
}

# Egress: ECR, SSM and the Anthropic API, all over HTTPS; dnf repos over HTTPS as well.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS out (ECR, SSM, Anthropic API, package repos)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# ---------------------------------------------------------------------------------------------
# Persistent data volume, mounted at /data. Separate from the instance so that replacing the
# instance keeps run state, approvals, decisions and audit events (restart/resume, §2.6).
# ---------------------------------------------------------------------------------------------

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.selected.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true

  tags = { Name = "${var.project}-data" }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
}

# ---------------------------------------------------------------------------------------------
# Instance
# ---------------------------------------------------------------------------------------------

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.selected.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  # IMDSv2 only, hop limit 1: the container cannot reach instance credentials.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region              = var.region
    registry            = split("/", aws_ecr_repository.app.repository_url)[0]
    image_uri           = local.image_uri
    volume_id           = aws_ebs_volume.data.id
    model_adapter       = var.model_adapter
    anthropic_key_param = local.anthropic_key_param
    hmac_key_param      = local.hmac_key_param
    tracing_enabled     = var.tracing_enabled
    tracing_host        = var.tracing_host
    langfuse_pub_param  = local.langfuse_pub_param
    langfuse_sec_param  = local.langfuse_sec_param
  })
  user_data_replace_on_change = true

  lifecycle {
    # A newer AMI must not silently replace the instance on the next apply.
    ignore_changes = [ami]
  }

  tags = { Name = var.project }
}
