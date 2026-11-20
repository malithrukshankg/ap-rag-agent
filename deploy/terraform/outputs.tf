output "api_url" {
  description = "Base URL of the API (reachable from allowed_cidr). Swagger UI at /docs."
  value       = "http://${aws_instance.app.public_ip}:8000"
}

output "instance_id" {
  value = aws_instance.app.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "data_volume_id" {
  value = aws_ebs_volume.data.id
}

output "ssm_session_command" {
  description = "Shell on the instance without SSH (needs the Session Manager plugin)."
  value       = "aws ssm start-session --target ${aws_instance.app.id} --region ${var.region} --profile ${var.aws_profile}"
}
