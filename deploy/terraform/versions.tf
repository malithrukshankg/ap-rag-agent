terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # Local state on purpose: one developer, one instance. The state holds no secrets, because
  # secrets are written to SSM by scripts/deploy.sh and Terraform only references their names.
  # Production path: S3 backend with locking.
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
