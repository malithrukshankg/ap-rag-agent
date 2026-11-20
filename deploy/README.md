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

## Verify the deployed setup (AWS CLI)
These read-only commands show the pieces the brief asks to demonstrate (§6): the instance, the separate data volume at `/data`, the single-CIDR security group, and the image. Run them from the repo root with `AWS_PROFILE` and `AWS_REGION` set.
```bash
TF="terraform -chdir=deploy/terraform output -raw"
aws ec2 describe-instances --instance-ids $($TF instance_id) \
  --query 'Reservations[0].Instances[0].{type:InstanceType,state:State.Name,imds:MetadataOptions.HttpTokens,hop:MetadataOptions.HttpPutResponseHopLimit,ip:PublicIpAddress}'
aws ec2 describe-volumes --volume-ids $($TF data_volume_id) \
  --query 'Volumes[0].{size:Size,type:VolumeType,encrypted:Encrypted,attached:Attachments[0].Device,deleteOnTermination:Attachments[0].DeleteOnTermination}'
aws ec2 describe-security-groups --filters Name=group-name,Values=ap-rag-agent-sg \
  --query 'SecurityGroups[0].IpPermissions[].{port:FromPort,cidr:IpRanges[0].CidrIp}'
aws ecr describe-images --repository-name ap-rag-agent --query 'imageDetails[].{tags:imageTags,pushed:imagePushedAt}'
curl -s -X POST $($TF api_url)/evals | jq '{passed, cases: [.cases[] | {case_id, passed}]}'
```
On the instance (`$($TF ssm_session_command)`), `findmnt /data` shows the EBS volume and `sudo ls /data` shows `ap.db` and `logs/`.

## Screenshots
Placeholders, to be captured from a live deployment and saved under `deploy/screenshots/`:

| File | Shows |
|---|---|
| `01-ec2-instance.png` | EC2 console: the `ap-rag-agent` instance running, IMDSv2 required |
| `02-ebs-data-volume.png` | EBS console: the `ap-rag-agent-data` volume, encrypted, attached as `/dev/sdf` |
| `03-security-group.png` | Inbound rule: port 8000 from `allowed_cidr` (0.0.0.0/0 on the shared tester instance); no SSH rule |
| `04-swagger.png` | `<api_url>/docs` served from the instance |
| `05-evals.png` | `POST /evals` response on AWS: all five cases passing |
| `06-destroy.png` | `destroy.sh` output: volume, repository and parameters deleted |

## Security posture
- The API has **no authentication**: approver identity is asserted by the caller (CLAUDE.md #6). It is reachable only from `allowed_cidr`. `deploy.sh` defaults that to your public IP /32. The shared tester instance is deployed with `ALLOWED_CIDR=0.0.0.0/0`, an explicit opt-in that the script warns about. On that instance anyone with the URL can start runs, approve simulated postings and, in live mode, spend the Anthropic key, so set a spend limit on the key. No tool can move real money.
- No SSH and no key pair. IMDSv2 is required, with hop limit 1, so the container can't read instance credentials.
- The instance role can only pull this one ECR repo and read the two parameters.
- Secrets are fetched at each service start into `/run/ap-agent.env` (tmpfs, mode 600). They are never written to the EBS volume.
- Root and data volumes are encrypted.

## Cost and cleanup
About US$25/month while running in ap-southeast-2: t3.small, a public IPv4 address and 25 GB gp3. ECR storage is negligible. Anthropic usage is extra.

```bash
AWS_PROFILE=<profile> deploy/scripts/destroy.sh
```
This destroys everything, **including the EBS volume and all run state**, plus the ECR images and the SSM parameters. The data volume is a separate `aws_ebs_volume` resource, not the instance's root disk, so terminating the instance alone would **not** delete it. Run `destroy.sh` rather than terminating the instance in the console. To confirm nothing billable is left:
```bash
aws ec2 describe-volumes --filters Name=tag:Name,Values=ap-rag-agent-data --query 'length(Volumes)'   # 0
aws ec2 describe-instances --filters Name=tag:Name,Values=ap-rag-agent Name=instance-state-name,Values=running,stopped --query 'length(Reservations)'   # 0
aws ecr describe-repositories --repository-names ap-rag-agent 2>&1 | grep -q RepositoryNotFound && echo "ECR gone"
```

## Production changes (not done)
- Remote state (S3 with locking).
- Private subnet behind an ALB with TLS and real authentication (OIDC) for approvers.
- CloudWatch log shipping and alarms.
- EBS snapshots via AWS Backup, to meet the FIN-POL-010 retention period.
- Postgres (RDS) to allow more than one instance.
