#!/usr/bin/env bash
# Tear down everything deploy.sh created (cost/cleanup, §6): instance, EBS data volume (run state
# is deleted with it), ECR repository and images, security group, IAM role, SSM parameters.
#
# Usage: AWS_PROFILE=<profile> deploy/scripts/destroy.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: keep "/ap-rag-agent/..." parameter names verbatim

# pwd -W gives a native D:/... path under Git Bash, needed by the Windows terraform binary.
ROOT="$(cd "$(dirname "$0")/../.." && (pwd -W 2>/dev/null || pwd))"
TF_DIR="$ROOT/deploy/terraform"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-ap-southeast-2}"

# allowed_cidr is required by the configuration but irrelevant to destroy.
terraform -chdir="$TF_DIR" destroy -input=false -auto-approve \
  -var "aws_profile=$AWS_PROFILE" -var "region=$AWS_REGION" -var "allowed_cidr=127.0.0.1/32"

for p in /ap-rag-agent/payment-hmac-key /ap-rag-agent/anthropic-api-key; do
  aws ssm delete-parameter --name "$p" 2>/dev/null && echo "deleted $p" || echo "absent $p"
done

echo "Done. Verify nothing remains:"
echo "  aws ec2 describe-volumes --filters Name=tag:Project,Values=ap-rag-agent --query 'Volumes[].VolumeId'"
