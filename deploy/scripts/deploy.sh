#!/usr/bin/env bash
# Provision (or update) the AWS deployment: secrets → Terraform → image build/push → restart →
# health check. Idempotent: re-run it after a code change to roll out a new image.
#
# Usage:   AWS_PROFILE=<profile> deploy/scripts/deploy.sh
# Env:     AWS_PROFILE (default "default"), AWS_REGION (default ap-southeast-2),
#          MODEL_ADAPTER (anthropic|scripted, default anthropic),
#          ANTHROPIC_API_KEY (else read from ./.env), ALLOWED_CIDR (default: your public IP/32),
#          ROTATE_ANTHROPIC_KEY=1 to overwrite the stored key.
# Works in bash on Linux/macOS and in Git Bash on Windows.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: keep "/ap-rag-agent/..." parameter names verbatim

# pwd -W gives a native D:/... path under Git Bash, needed by the Windows terraform/docker binaries.
ROOT="$(cd "$(dirname "$0")/../.." && (pwd -W 2>/dev/null || pwd))"
TF_DIR="$ROOT/deploy/terraform"
PROJECT="ap-rag-agent"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-ap-southeast-2}"
MODEL_ADAPTER="${MODEL_ADAPTER:-anthropic}"
HMAC_PARAM="/$PROJECT/payment-hmac-key"
KEY_PARAM="/$PROJECT/anthropic-api-key"

log() { printf '\n==> %s\n' "$*"; }
param_exists() { aws ssm get-parameter --name "$1" --query Parameter.Name --output text >/dev/null 2>&1; }

for cmd in aws terraform docker curl; do
  command -v "$cmd" >/dev/null || { echo "missing prerequisite: $cmd" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker daemon is not running" >&2; exit 1; }
aws sts get-caller-identity --query Account --output text >/dev/null

# --- 1. Secrets (SSM SecureString; never in Terraform state, never echoed) -------------------
log "Secrets in SSM Parameter Store"
if ! param_exists "$HMAC_PARAM"; then
  aws ssm put-parameter --name "$HMAC_PARAM" --type SecureString \
    --value "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >/dev/null
  echo "created $HMAC_PARAM (random)"
else
  echo "kept existing $HMAC_PARAM"
fi

if [ "$MODEL_ADAPTER" = "anthropic" ]; then
  if ! param_exists "$KEY_PARAM" || [ "${ROTATE_ANTHROPIC_KEY:-0}" = "1" ]; then
    KEY="${ANTHROPIC_API_KEY:-}"
    if [ -z "$KEY" ] && [ -f "$ROOT/.env" ]; then
      KEY="$(grep -E '^ANTHROPIC_API_KEY=' "$ROOT/.env" | tail -n1 | cut -d= -f2- \
        | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
    fi
    [ -n "$KEY" ] || { echo "ANTHROPIC_API_KEY not set (env or .env); or use MODEL_ADAPTER=scripted" >&2; exit 1; }
    aws ssm put-parameter --name "$KEY_PARAM" --type SecureString --overwrite --value "$KEY" >/dev/null
    unset KEY
    echo "stored $KEY_PARAM"
  else
    echo "kept existing $KEY_PARAM"
  fi
fi

# --- 2. Infrastructure -----------------------------------------------------------------------
ALLOWED_CIDR="${ALLOWED_CIDR:-$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')/32}"
log "Terraform apply (API open to $ALLOWED_CIDR only)"
terraform -chdir="$TF_DIR" init -input=false >/dev/null
terraform -chdir="$TF_DIR" apply -input=false -auto-approve \
  -var "aws_profile=$AWS_PROFILE" -var "region=$AWS_REGION" \
  -var "allowed_cidr=$ALLOWED_CIDR" -var "model_adapter=$MODEL_ADAPTER"

REPO_URL="$(terraform -chdir="$TF_DIR" output -raw ecr_repository_url)"
INSTANCE_ID="$(terraform -chdir="$TF_DIR" output -raw instance_id)"
API_URL="$(terraform -chdir="$TF_DIR" output -raw api_url)"

# --- 3. Image ----------------------------------------------------------------------------------
GIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
log "Build and push $REPO_URL:latest ($GIT_SHA)"
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO_URL%%/*}" >/dev/null
docker build --platform linux/amd64 -t "$REPO_URL:latest" -t "$REPO_URL:$GIT_SHA" "$ROOT"
docker push "$REPO_URL:latest"
docker push "$REPO_URL:$GIT_SHA"

# --- 4. Roll out ------------------------------------------------------------------------------
# A fresh instance picks the image up on its own (systemd retries the pull every 15 s). An
# existing one is told to restart; this fails harmlessly if the SSM agent is not registered yet.
log "Restart service on $INSTANCE_ID"
aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl restart ap-agent"]' --query Command.CommandId --output text \
  2>/dev/null || echo "(instance not yet registered with SSM; the service will start on its own)"

# --- 5. Health ----------------------------------------------------------------------------------
log "Waiting for $API_URL (first boot takes 2-4 minutes)"
for _ in $(seq 1 60); do
  # Shell redirect, not `curl -o /dev/null`: with MSYS_NO_PATHCONV a native curl can't open it.
  if curl -fsS --max-time 5 "$API_URL/openapi.json" >/dev/null 2>&1; then
    echo "UP: $API_URL   (Swagger UI: $API_URL/docs)"
    exit 0
  fi
  sleep 10
done
echo "API did not come up in 10 minutes. Inspect with:" >&2
echo "  $(terraform -chdir="$TF_DIR" output -raw ssm_session_command)" >&2
echo "  then: sudo journalctl -u ap-agent -n 100; sudo cat /var/log/ap-agent-bootstrap.log" >&2
exit 1
