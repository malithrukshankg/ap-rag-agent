variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "ap-rag-agent"
}

variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "aws_profile" {
  description = "AWS CLI profile used by the provider."
  type        = string
  default     = "default"
}

variable "allowed_cidr" {
  description = <<-EOT
    The only CIDR allowed to reach the API on port 8000. The API has no authentication
    (approver identity is asserted by the caller, CLAUDE.md resolved ambiguity #6), so it must
    never be open to 0.0.0.0/0. scripts/deploy.sh sets this to your current public IP /32.
  EOT
  type        = string

  validation {
    condition     = can(cidrhost(var.allowed_cidr, 0)) && var.allowed_cidr != "0.0.0.0/0"
    error_message = "allowed_cidr must be a valid CIDR and must not be 0.0.0.0/0."
  }
}

variable "instance_type" {
  description = "x86_64 instance type (the image is built for linux/amd64)."
  type        = string
  default     = "t3.small"
}

variable "data_volume_gb" {
  description = "Size of the EBS volume mounted at /data (SQLite run state + logs)."
  type        = number
  default     = 5
}

variable "image_tag" {
  description = "ECR image tag the instance runs."
  type        = string
  default     = "latest"
}

variable "tracing_enabled" {
  description = "Mirror audit events to Langfuse (needs the Langfuse keys in SSM; deploy.sh writes them)."
  type        = bool
  default     = false
}

variable "tracing_host" {
  description = "Langfuse host URL (not secret)."
  type        = string
  default     = "https://cloud.langfuse.com"

  validation {
    condition     = can(regex("^https://", var.tracing_host))
    error_message = "tracing_host must be an https:// URL."
  }
}

variable "model_adapter" {
  description = "anthropic (real Claude, needs the API key in SSM) or scripted (no key, no network)."
  type        = string
  default     = "anthropic"

  validation {
    condition     = contains(["anthropic", "scripted"], var.model_adapter)
    error_message = "model_adapter must be anthropic or scripted."
  }
}
