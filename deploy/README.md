# AWS deployment (architecture §12)

One EC2 instance running the app container, with a separate EBS volume at `/data` for SQLite run
state and JSON logs. Single instance by design: SQLite allows one writer and the idempotency
guarantees rely on it.

| Resource | Real / simulated |
|---|---|
| EC2, EBS, ECR, IAM, SSM Parameter Store | Real AWS (needs an AWS account) |
| Anthropic API | Real, unless `MODEL_ADAPTER=scripted` |
| Vendor/PO/history tools, `submit_finance_decision` ledger | Simulated (fixtures, sandbox ledger), as locally. Nothing can move money. |

## Prerequisites
AWS CLI v2 with a profile, Terraform ≥ 1.6, Docker running, curl, bash (Git Bash works on Windows).
Optional: the Session Manager plugin, for a shell on the instance.

## Deploy / update
```bash
AWS_PROFILE=<profile> deploy/scripts/deploy.sh                          # real Claude; key from env or ./.env
AWS_PROFILE=<profile> MODEL_ADAPTER=scripted deploy/scripts/deploy.sh   # no key, no model calls
```
The script:
1. Syncs the secrets to SSM as SecureStrings, and only when they differ from the stored value (compared by hash, never printed). It reads the Anthropic key, and the Langfuse keys if tracing is on, from the environment or `./.env`. The HMAC key is random, created once and then kept stable, because it fingerprints bank accounts in stored run state. Terraform never sees any secret, so the state holds none.
2. Runs `terraform apply`, opening port 8000 to your current public IP only.
3. Builds and pushes the image.
4. Restarts the service.
5. Waits for `/openapi.json`.

Re-run it to roll out code changes or a changed key.

**Langfuse tracing** (optional) follows `AP_TRACING__ENABLED` and `AP_TRACING__HOST` in `.env`. Override them with `TRACING_ENABLED=true|false` and `TRACING_HOST=...`. Traces are tagged `environment=aws`. Toggling tracing changes the instance's boot script, which **replaces the instance**: the data volume is kept and reattached, but the public IP changes.

## Use
```bash
API=$(terraform -chdir=deploy/terraform output -raw api_url)
curl -X POST $API/evals            # runs FIN-001..FIN-005 and reports pass/fail
open $API/docs                     # Swagger UI: start run, get run, approve/reject
```
Logs: `aws ssm start-session --target <instance_id>`, then `sudo tail -f /data/logs/ap.jsonl`
(the app's structured logs) or `sudo journalctl -u ap-agent` (service and container).

## Security posture
- The API has **no authentication**: approver identity is asserted by the caller (CLAUDE.md #6). It is reachable only from `allowed_cidr`, and Terraform refuses `0.0.0.0/0`.
- No SSH and no key pair. IMDSv2 is required, with hop limit 1, so the container can't read instance credentials.
- The instance role can only pull this one ECR repo and read the two parameters.
- Secrets are fetched at each service start into `/run/ap-agent.env` (tmpfs, mode 600). They are never written to the EBS volume.
- Root and data volumes are encrypted.

## Cost and cleanup
About US$25/month while running in ap-southeast-2: t3.small, a public IPv4 address and 25 GB gp3. ECR storage is negligible. Anthropic usage is extra.

```bash
AWS_PROFILE=<profile> deploy/scripts/destroy.sh
```
This destroys everything, **including the EBS volume and all run state**, plus the ECR images and the SSM parameters.

## Production changes (not done)
- Remote state (S3 with locking).
- Private subnet behind an ALB with TLS and real authentication (OIDC) for approvers.
- CloudWatch log shipping and alarms.
- EBS snapshots via AWS Backup, to meet the FIN-POL-010 retention period.
- Postgres (RDS) to allow more than one instance.
