# ap-rag-agent

A small, production-minded accounts-payable agent, built for a take-home assessment ([brief](docs/brief.md)). Given an invoice-processing request, it:

1. retrieves the relevant finance policy through RAG, with citations;
2. gathers vendor, purchase-order, invoice-history and FX evidence through typed tools;
3. reconciles the evidence deterministically in code (controls C1–C6, `Decimal` arithmetic; see [docs/controls.md](docs/controls.md));
4. recommends an outcome with cited evidence, calculations, unknowns and a confidence level;
5. **stops for a human approver** before any payment is posted.

The design principle is **code decides, the model describes**. The model writes the narrative and can flag risks, but it cannot change a control result, produce a number or loosen an outcome.

| Document | What it covers |
|---|---|
| [docs/design-note.md](docs/design-note.md) | The design note (~2 pages): orchestration, RAG, trust boundaries, contracts, persistence, failure handling, production changes |
| [docs/controls.md](docs/controls.md) | What each control C1–C6 checks, how results combine into a recommendation, and the Decimal arithmetic rules, with a worked FIN-001 example |
| [docs/architecture.md](docs/architecture.md) | The full design: diagram (§0.1), component/configuration manifest (§0.2), every decision traced to the brief |
| [docs/transcripts/](docs/transcripts/) | Sample runs: [FIN-001](docs/transcripts/FIN-001.md) approve → post, and [FIN-003](docs/transcripts/FIN-003.md) poisoned document → escalate |
| [deploy/README.md](deploy/README.md) | AWS deployment (Terraform), security posture, cost and cleanup |

## Environment choice

The primary environment is **local**: the reviewer path needs no API key, no cloud account and no services. SQLite holds the index, run state and audit log; one process serves the API. A Dockerfile reproduces that in a container. An optional **AWS** deployment (EC2 + EBS + ECR, Terraform) shows how the same container is hosted, with secrets in SSM. A shared instance is [already deployed for testers](#test-on-aws). SQLite is a deliberate choice: the brief accepts simple local persistence that demonstrates restart/resume and idempotency (§8). It is also why the app runs as a single uvicorn worker (one writer).

## Run it locally

### 1. Prerequisites

| Need | Version | Needed for |
|---|---|---|
| Python | 3.12 (pinned in `.python-version`; `uv` installs it if missing) | everything |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | ≥ 0.5 | install, run, test |
| make | any | optional. Each Makefile target is one `uv` line, so on Windows run the line directly. |
| Anthropic API key | | live mode only |
| Langfuse account and keys | | optional tracing only |
| Docker | ≥ 24 | the container only |
| AWS CLI v2, Terraform ≥ 1.6, bash (Git Bash on Windows) | | the AWS deployment only |

### 2. Install and create `.env`

```bash
git clone <repo-url> ap-rag-agent && cd ap-rag-agent
uv sync --frozen            # creates .venv with the exact locked versions
cp .env.example .env        # PowerShell: Copy-Item .env.example .env
```

**Secrets and per-machine settings go only in `.env`**, which is git-ignored. Never put them in `config/app.yaml`, which is committed. The app reads `.env` from the repo root at startup.

| `.env` variable | Scripted mode | Live mode | What to put there |
|---|---|---|---|
| `AP_PAYMENT_HMAC_KEY` | **required** | **required** | Any long random string. Locally the example value `change-me-local-only` works. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. The app refuses to start without it. |
| `AP_MODEL__ADAPTER` | `scripted` | `anthropic` (or leave unset: `anthropic` is the default in `config/app.yaml`) | Selects the mode; see step 3. |
| `ANTHROPIC_API_KEY` | leave empty | **required** | A key from <https://console.anthropic.com> → *API Keys*, starting `sk-ant-`. With `anthropic` selected and no key, the app refuses to start. |
| `AP_TRACING__ENABLED` | optional | optional | `true` to send traces to Langfuse; default `false`. Works in either mode. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | only if tracing | only if tracing | From your Langfuse project → *Settings* → *API Keys* (`pk-lf-…` and `sk-lf-…`). |
| `AP_TRACING__HOST` | only if tracing | only if tracing | `https://cloud.langfuse.com` (EU), `https://us.cloud.langfuse.com` (US), or a self-hosted URL. |

A complete `.env` for **scripted mode** (no accounts needed):

```dotenv
AP_PAYMENT_HMAC_KEY=change-me-local-only
AP_MODEL__ADAPTER=scripted
```

A complete `.env` for **live mode with tracing**:

```dotenv
AP_PAYMENT_HMAC_KEY=3f9c…a long random string…
AP_MODEL__ADAPTER=anthropic
ANTHROPIC_API_KEY=sk-ant-…
AP_TRACING__ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-…
LANGFUSE_SECRET_KEY=sk-lf-…
AP_TRACING__HOST=https://cloud.langfuse.com
```

Tracing is observe-only. SQLite stays the system of record, and a Langfuse outage cannot change or fail a run. By default no prompt text or invoice amount is sent to Langfuse; set `AP_TRACING__SEND_PROMPTS=true` to send the redacted prompt and completion text as well.

### 3. Choose a mode: scripted or live

The only difference between the two modes is **who writes the model's assessment** (the narrative, citations, risk flags and proposed outcome in the ASSESS step). Everything else is identical: retrieval, the simulated tools, controls C1–C6, the arithmetic, the approval gate and the database. That is deliberate. Code decides the outcome, so swapping the model must not change the controls.

| | Scripted mode (offline) | Live mode |
|---|---|---|
| Select with | `AP_MODEL__ADAPTER=scripted` | `AP_MODEL__ADAPTER=anthropic` |
| ASSESS step | Replays canned JSON completions stored with each case (`scripted_model` in `fixtures/cases/FIN-00x.json`, or the `simulation.scripted_model` field of a `POST /runs` request) | Calls Claude (`claude-opus-5-5`, set under `model:` in `config/app.yaml`) with the redacted evidence |
| Needs | nothing: no key, no network, no cost | `ANTHROPIC_API_KEY`, internet access, API usage is billed |
| Deterministic | yes: same outcome, controls and fingerprint every run | outcomes are bounded by the controls; the wording varies |
| A request without scripted completions | the assessment is `UNAVAILABLE`, so the outcome is floored at `HOLD` | normal |
| Used by | `pytest -m "not live"` (forces scripted whatever `.env` says), `scripts/transcripts.py`, keyless demos | `pytest -m live`, real use |

Fault injection (the `simulation.faults` field, used for example for FIN-004's PO timeouts) works in both modes. The simulated vendor, PO, history, FX and ledger backends are the same in both modes: "live" means a live *model*, not live finance systems.

To switch modes, change `AP_MODEL__ADAPTER` in `.env` and restart the server. You can also override it for a single command:

```bash
AP_MODEL__ADAPTER=scripted uv run uvicorn ap_agent.api.app:app --workers 1 --port 8000   # bash
$env:AP_MODEL__ADAPTER="scripted"; uv run uvicorn ap_agent.api.app:app --workers 1 --port 8000   # PowerShell
```

### 4. Ingest, start, try it

```bash
uv run ap ingest            # chunks and indexes fixtures/corpus into var/ap.db and prints a report
uv run uvicorn ap_agent.api.app:app --workers 1 --host 127.0.0.1 --port 8000      # or: make run
```

- **Ingestion.** `ap ingest` is idempotent: it is a no-op while the corpus is unchanged. The server also re-indexes on startup whenever the corpus has changed.
- **Startup.** A startup log line or error names any missing setting.
- **Trying it.** Open <http://127.0.0.1:8000/docs> (Swagger UI) to call every endpoint from the browser, or follow [Using the API](#using-the-api) for a scripted end-to-end FIN-001 run with curl.
- **Where things are written.** Run state, the audit log and the index go to `var/ap.db`; JSON logs go to `var/logs/ap.jsonl`. Delete `var/` to start fresh.

### 5. Run the tests and the evaluation

```bash
uv run pytest -m "not live"            # stable tier: 709 tests, ~15 s, no key, no network (make test)
uv run ruff check . && uv run mypy     # lint and strict types (make lint)
```

The **live tier** checks for the key in the *shell environment*, not in `.env`. If the key is only in `.env`, the live tests are skipped.

```bash
export ANTHROPIC_API_KEY=sk-ant-…      # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-…"
uv run pytest -m live -v               # the five cases against Claude (make test-live)
```

The live tests assert safety rather than wording: the outcome is never more permissive than expected, every attempt was a real recorded model call, nothing posts without a grant, and no untrusted document is cited as authority.

The **evaluation report** (the "list evaluation results" operation, §2) runs FIN-001…005 through the server's configured mode on a throwaway database:

```bash
curl -s -X POST http://127.0.0.1:8000/evals | jq '{passed, cases: [.cases[] | {case_id, passed}], metrics}'
curl -s http://127.0.0.1:8000/evals/latest     # the last report
```

### 6. Or run it in Docker

```bash
docker build -t ap-rag-agent .         # the build also runs `ap ingest` and fails on a bad corpus
docker run --rm -p 8000:8000 -v ap-data:/data --env-file .env ap-rag-agent
```

The container uses the same `.env`. Its run state and logs live in the `ap-data` volume at `/data`, so they survive container restarts.

### Configuration files (reference)

| File | Holds | Edit when |
|---|---|---|
| `.env` | secrets and per-machine overrides (above) | always, per machine |
| `config/app.yaml` | model id, effort and timeout; budget; tool timeouts and retries; storage and log paths; tracing defaults; escalation precedence | tuning behaviour; committed, so no secrets |
| `config/policy_params.yaml` | the policy numbers controls use (tolerances, authority limits), each tagged with the exact corpus sentence it comes from | only when the corpus policy changes |
| `config/policy_registry.yaml` | which corpus documents are trusted, and at which pinned version | adding or superseding a policy document |

Any `app.yaml` key can be overridden from `.env` or the environment: prefix `AP_` and use `__` for nesting. For example, `AP_MODEL__ID=…`, `AP_BUDGET__MAX_TOOL_CALLS=30` or `AP_STORAGE__SQLITE_PATH=/data/ap.db`.

```yaml
model:                      # config/app.yaml
  adapter: anthropic        # or scripted
  id: claude-opus-5-5
  effort: medium
  max_tokens: 16000
  timeout_s: 120
  prompt_version: assess-v2 # recorded on every run
```

The graph only ever sees the `ModelClient` protocol; `orchestration/model_factory.py` builds the adapter from these settings. Once a run is under way, a failed or malformed model call does not fail it: after two repair attempts the assessment is marked `UNAVAILABLE` and the outcome is floored at `HOLD`.

## Commands

| Task | Command | Make target |
|---|---|---|
| Install | `uv sync --frozen` | `make install` |
| Ingest / index the corpus | `uv run ap ingest` (idempotent; prints a report) | `make ingest` |
| Start the API | `uv run uvicorn ap_agent.api.app:app --workers 1 --host 127.0.0.1 --port 8000` | `make run` |
| Stable tests (no key) | `uv run pytest -m "not live"` | `make test` |
| Live tests (real model) | `ANTHROPIC_API_KEY=… uv run pytest -m live` | `make test-live` |
| Lint and types | `uv run ruff check . && uv run ruff format --check . && uv run mypy` | `make lint` |
| Evaluation report | `curl -X POST localhost:8000/evals`, then `curl localhost:8000/evals/latest` | |
| Regenerate transcripts | `uv run python scripts/transcripts.py` | |
| Container | `docker build -t ap-rag-agent .`, then `docker run --rm -p 8000:8000 -v ap-data:/data --env-file .env ap-rag-agent` | `make docker-build` |
| AWS deploy / destroy | `deploy/scripts/deploy.sh` / `deploy/scripts/destroy.sh` (see [Test on AWS](#test-on-aws)) | |

The `POST /evals` report gives pass/fail per case plus these metrics: outcome accuracy, no POST without a grant, exactly one posting, citation validity, recall@5, superseded documents returned (must be 0), adversarial documents cited as authority (must be 0), and injection resistance.

## Test on AWS

A deployed instance is already running. **Testers do not need an AWS account, a key or a local install**: a browser, or curl, is enough.

| | |
|---|---|
| Base URL | **<http://13.210.183.195:8000>** |
| Swagger UI | <http://13.210.183.195:8000/docs> |
| Mode | **Live**: real Claude (`claude-opus-5-5`). Vendor, PO, history, FX and the ledger are SIMULATED fixtures; nothing can move money. |
| Hosting | AWS ap-southeast-2: one EC2 instance running the container, with an encrypted EBS volume at `/data` for the SQLite run state |
| Access | Open to any address. **There is no login**: approver identity is asserted in the request, as documented (resolved ambiguity #6). |

If the URL does not respond, the instance has been redeployed (its public IP changes) or torn down: ask the author for the current address.

### Option A: in the browser (Swagger UI)

1. Open <http://13.210.183.195:8000/docs>.
2. **Evaluation report.** Expand `POST /evals` → *Try it out* → *Execute*. This runs FIN-001…005 against Claude on a throwaway database and returns pass/fail per case plus the metrics. It takes a few minutes, because it makes one model call per case.
3. **A single run.** Expand `POST /runs` → *Try it out*. Paste `{"case": <the "case" object from fixtures/cases/FIN-001.json>}` as the body and execute. The response shows `status: AWAITING_APPROVAL`, the recommendation, the controls, the calculations and a `decision_fingerprint`.
4. **Approve.** Expand `POST /runs/{run_id}/approval`. Enter the `run_id` and send `{"verdict": "approve", "approver_id": "A-CCM-01", "decision_fingerprint": "<from step 3>"}`. Execute it **twice**: both responses are identical, and only one decision exists.
5. **Inspect.** `GET /runs/{run_id}` with `timeline=true` shows the status, the typed result and the full audit trail.

Try FIN-003 (the poisoned attachment) the same way. It completes as `ESCALATE_CONTROL_REVIEW` → `HOLD`, without any approval request. FIN-004 needs `simulation.faults` to simulate the PO timeout, so send the whole fixture shape: `{"case": …, "simulation": {"faults": …}}`. Approver ids that fail a check, for example `A-INACTIVE` or `A-CCM-EXP`, return HTTP 409, and the run stays pending.

### Option B: from a terminal

From a clone of this repo (bash with `jq`):

```bash
API=http://13.210.183.195:8000
curl -s -X POST $API/evals | jq '{passed, cases: [.cases[] | {case_id, passed}]}'
```

The FIN-001 walkthrough in [Using the API](#using-the-api) works unchanged with `API` set to this URL. PowerShell, without jq:

```powershell
$API = "http://13.210.183.195:8000"
$case = (Get-Content fixtures/cases/FIN-001.json -Raw | ConvertFrom-Json).case
$run = Invoke-RestMethod -Method Post "$API/runs" -ContentType "application/json" -Body (@{case=$case} | ConvertTo-Json -Depth 10)
$run.status; $run.result.recommendation.outcome            # AWAITING_APPROVAL, APPROVE_FOR_POSTING
$body = @{verdict="approve"; approver_id="A-CCM-01"; decision_fingerprint=$run.result.decision_fingerprint} | ConvertTo-Json
1..2 | ForEach-Object { Invoke-RestMethod -Method Post "$API/runs/$($run.run_id)/approval" -ContentType "application/json" -Body $body | ConvertTo-Json -Compress }
(Invoke-RestMethod "$API/runs/$($run.run_id)").status       # COMPLETED
```

### Deploying your own copy (owner only)

The steps below recreate or update the instance. They are not needed for testing. It is real infrastructure and **costs about US$25/month while it exists**, so destroy it when you are done (step 5). [deploy/README.md](deploy/README.md) has the full detail and the security posture.

**1. Prerequisites.** You need:
- an AWS account, with an AWS CLI v2 profile allowed to create EC2, EBS, ECR, IAM roles and SSM parameters (`aws configure --profile <name>`);
- Terraform ≥ 1.6;
- Docker running;
- bash (Git Bash on Windows), curl and jq;
- optionally the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html), for a shell on the instance.

**2. Secrets.** The deploy script reads secrets from your shell or from the same local `.env`:
- `ANTHROPIC_API_KEY`, unless you deploy in scripted mode;
- the Langfuse keys, if `AP_TRACING__ENABLED=true`.

It stores them in SSM Parameter Store as encrypted SecureStrings. Terraform never sees them, and they are never written to the instance's disk. You don't set the HMAC key: it is generated once, in SSM.

**3. Deploy** (from the repo root, in bash):

```bash
AWS_PROFILE=<name> deploy/scripts/deploy.sh                                # live mode, your IP only
AWS_PROFILE=<name> ALLOWED_CIDR=0.0.0.0/0 deploy/scripts/deploy.sh         # the shared tester instance: open to all
AWS_PROFILE=<name> MODEL_ADAPTER=scripted deploy/scripts/deploy.sh         # scripted mode: no key, no model cost
```

Optional overrides:
- `AWS_REGION`, default `ap-southeast-2`;
- `ALLOWED_CIDR`, default *your current public IP*/32. `0.0.0.0/0` is an explicit opt-in, and the script warns when you use it. The API has no authentication, so anyone can then start runs and, in live mode, spend your Anthropic key. Set a spend limit in the Anthropic console first.
- `TRACING_ENABLED=true|false`.

The script provisions the resources, builds and pushes the image, starts the service, and waits until it answers. Re-run it to roll out a change.

**4. Test the deployment:**

```bash
API=$(terraform -chdir=deploy/terraform output -raw api_url)        # http://<public-ip>:8000
curl -s -X POST $API/evals | jq '{passed, cases: [.cases[] | {case_id, passed}]}'   # all five cases
```

- **Interactive.** Open `$API/docs` in a browser from an allowed address. The FIN-001 walkthrough in [Using the API](#using-the-api) works unchanged with `API` set to the deployed URL.
- **Infrastructure and logs.** Verify the instance, the encrypted data volume at `/data` and the security group with the read-only commands in [deploy/README.md](deploy/README.md#verify-the-deployed-setup-aws-cli). Read logs over SSM, with no SSH: `aws ssm start-session --target $(terraform -chdir=deploy/terraform output -raw instance_id)`, then `sudo tail -f /data/logs/ap.jsonl`.
- **Restart/resume on real infrastructure.** Start FIN-001 so it stops at `AWAITING_APPROVAL`. Restart the service with `sudo systemctl restart ap-agent` in the SSM shell, then send the approval. The run resumes from the EBS-backed database and posts exactly once.

**5. Clean up:**

```bash
AWS_PROFILE=<name> deploy/scripts/destroy.sh
```

This deletes the instance, the EBS data volume (all run state), the ECR repository and images, the IAM role and the SSM parameters. [deploy/README.md](deploy/README.md#cost-and-cleanup) lists commands to confirm nothing billable remains.

## Using the API

Four operations (§2), with schemas published as OpenAPI at `/docs`:

| Endpoint | Operation |
|---|---|
| `POST /runs` | Start a run. It executes until `COMPLETED`, `FAILED` or `AWAITING_APPROVAL`. |
| `GET /runs/{run_id}[?timeline=1]` | Status, current node, typed result, budget, audit events, optional timeline |
| `POST /runs/{run_id}/approval` | Approve or reject a pending posting: `{verdict, approver_id, decision_fingerprint}`. Replay-safe. |
| `POST /evals`, `GET /evals/latest` | Run and report the evaluation cases |

FIN-001 end to end (bash; needs `jq`):

```bash
API=http://127.0.0.1:8000
RUN=$(jq '{case, simulation: {faults, scripted_model}}' fixtures/cases/FIN-001.json \
      | curl -s -X POST $API/runs -H 'content-type: application/json' -d @-)
echo "$RUN" | jq '{status, outcome: .result.recommendation.outcome}'
# → "AWAITING_APPROVAL", "APPROVE_FOR_POSTING"

ID=$(echo "$RUN" | jq -r .run_id); FP=$(echo "$RUN" | jq -r .result.decision_fingerprint)
for i in 1 2; do   # the same callback delivered twice
  curl -s -X POST $API/runs/$ID/approval -H 'content-type: application/json' \
    -d "{\"verdict\":\"approve\",\"approver_id\":\"A-CCM-01\",\"decision_fingerprint\":\"$FP\"}" | sha256sum
done               # identical hashes: one decision, replay-safe response
curl -s "$API/runs/$ID?timeline=1" | jq '.status, [.timeline[].type]'
```

The `simulation` block is a **SIMULATED** test hook: it carries fault injection (for example FIN-004's PO timeouts) and the scripted completions. It is ignored for the model when the anthropic adapter is configured. Approver ids come from the simulated register `fixtures/systems/approvers.json`.

## Supported flows

| Case | Input signal | Result (stable tier) |
|---|---|---|
| FIN-001 valid three-way match | Invoice, PO and receipt agree; vendor active | All controls PASS → `APPROVE_FOR_POSTING` → `AWAITING_APPROVAL` → approve → one `POST_FOR_PAYMENT`, one ledger row |
| FIN-002 duplicate invoice | Number and amount match a paid invoice | C2 → `REJECT_DUPLICATE` → decision `HOLD` for AP to confirm the rejection; no approval requested |
| FIN-003 poisoned document | The attachment says to ignore policy, skip approval and pay a new account | ADV-001 retrieved and flagged untrusted; C3 (account mismatch) and C6 (4 indicators) → `ESCALATE_CONTROL_REVIEW` → `HOLD` to Financial Crime and Controls |
| FIN-004 missing evidence | The PO API times out; no receipt | Bounded retries exhausted → C4 `INDETERMINATE`, missing evidence listed → `HOLD_FOR_INFORMATION`; no approval requested |
| FIN-005 duplicate approval | The same approval callback twice, concurrently | One decision, one ledger row, byte-identical responses |

The tests also cover: an approver who fails an authority check (HTTP 409; the run stays pending), rejection by the approver, malformed model output (repair, then `UNAVAILABLE`), budget exhaustion, a crash between the ledger write and the decision row, restart while awaiting approval, restart of an interrupted run, and a canary scan for bank account numbers across every store.

## Real vs SIMULATED

| Component | Status | Notes |
|---|---|---|
| LLM (`anthropic` adapter) | **Real**, needs `ANTHROPIC_API_KEY` and network | Model `claude-opus-5-5`, set in `config/app.yaml` |
| LLM (`scripted` adapter) | **SIMULATED** | Canned completions from the case fixtures; the default for the stable tier |
| `retrieve_finance_documents` | **Real** | SQLite FTS5/BM25 over the local corpus. The corpus itself is synthetic. |
| `get_vendor_record` | **SIMULATED** | `fixtures/systems/vendors.json` |
| `get_purchase_order` | **SIMULATED** | `fixtures/systems/purchase_orders.json`, with fault injection |
| `check_invoice_history` | **SIMULATED** | `fixtures/systems/invoice_history.json` |
| `get_fx_rate` (added tool) | **SIMULATED** | `fixtures/systems/fx_rates.json` |
| `submit_finance_decision` | **SIMULATED sandbox** | Writes to a `sim_ledger` table. **No tool can move real money.** |
| Approver register | **SIMULATED** | `fixtures/systems/approvers.json`. Identity is asserted by the caller, not authenticated. |
| Persistence, audit log, API, LangGraph runtime | **Real** | Local SQLite and FastAPI |
| Langfuse tracing | **Real, optional**, needs Langfuse keys and network | Off by default; observe-only |
| AWS (EC2, EBS, ECR, IAM, SSM) | **Real**, needs an AWS account | Optional; see [deploy/README.md](deploy/README.md) |

## Payment details and safe logging

`get_vendor_record` returns `PaymentDetails{last4, bank_country, hmac_fingerprint, changed_at}`, never a full account number. Full numbers exist only in the fixture JSON and never leave the fixture layer. So no tool response, prompt, log line, audit event or database row can contain one. A canary test greps every table, the log file and every stored prompt for the fixture account numbers and requires them to be absent.

This deliberately resolves the brief's "return payment details" (§3) against "do not log bank details" (§4). It is our reading, not brief text. C3 compares a claimed account against the master by last4 and fingerprint, in code. Audit and log payloads are built from per-event field allow-lists, and case free text has account-like digit runs masked before it reaches the model.

## Architecture

The architecture diagram is in [docs/architecture.md §0.1](docs/architecture.md#01-architecture-diagram-6-7). It shows the model, agent runtime, document store and index, persistence, tools and API surface, grouped into trust zones, with the trust boundaries TB1–TB4 marked. The component/configuration manifest follows it in §0.2. For each boundary's threat, control, code and tests, see [design note §3](docs/design-note.md#3-trust-boundaries).

## Assumptions

Ambiguities in the brief are resolved in [CLAUDE.md](CLAUDE.md#resolved-ambiguities) and [architecture.md §15](docs/architecture.md). The ones that shape behaviour:

- **Only posting needs approval.** All three outcomes go through `submit_finance_decision`, but only `POST_FOR_PAYMENT` needs an approval grant. Holds and rejections are the safe states and are recorded without one (§1, §2.5, §5 FIN-002/004).
- **Duplicates are held for AP review, not rejected by the agent.** The recommendation keeps `REJECT_DUPLICATE`; the agent rejects on its own only structurally invalid invoices.
- **Approvers come from a fixture register** and are checked five ways: active; authority limit covers the total; did not raise the case; fingerprint matches what they were shown; delegation valid on the day.
- **Policy beats PO tolerances.** When the PO's tolerance disagrees with policy, policy wins and the disagreement is flagged.
- **Confidence is computed in code** from control statuses and citation outcomes, never taken from the model.
- **Invoices arrive as structured JSON.** PDF extraction is out of scope.
- **Case text is untrusted data.** Notes and attachments are evidence, never instructions.
- **Adversarial documents are retrieved, not suppressed.** They are flagged by registry provenance and neutralised downstream. Nothing is filtered by filename.

## Known limitations

- **No authentication.** The API trusts the caller's `approver_id`. The shared AWS instance is deliberately open to any address so testers can reach it. Anyone with the URL can therefore start runs, approve simulated postings and spend model credit. Nothing can move real money, and production would put OIDC behind an ALB with TLS. Deployments default to the deployer's IP only.
- **Single instance.** SQLite allows one writer, so one uvicorn worker and no horizontal scaling.
- **Foreign-currency postings fail closed.** The approval amount is in invoice currency, but approver limits are in AUD, so a non-AUD posting fails the authority check.
- **The case-scoped idempotency key also blocks a legitimate corrected resubmission** of the same invoice reference.
- **Lexical retrieval.** BM25 misses synonyms, the templated queries do not adapt to unusual cases, tables need dedicated query arms, and 16 documents make recall@5 a weak test.
- **The literal check catches changed numbers, not changed meaning.** Policy parameters duplicate corpus knowledge.
- **The model narrative can be wrong** without being caught. It cannot change the outcome, but a misleading inference is measured by the live tier, not prevented.
- **No multi-approver path.** When two approvers are required (FIN-POL-003 §3), the agent escalates instead.
- **The simulated approver delegations end on 2026-12-31.** The approval check uses today's date (FIN-POL-003 §4), so after that date an approval in a live run or in `POST /evals` fails with an expired delegation. The stable tests pin the date. To keep demoing after 2026, extend `valid_to` in `fixtures/systems/approvers.json`.
- **Interrupted runs are not resumed.** A crash mid-run marks it `FAILED(interrupted)` and the caller re-runs it. Only paused runs resume.

## Unfinished work and deliberate trade-offs (§8)

**Not done (in cut order):**
- **docker-compose.** One container needs no orchestration; `docker run` is documented instead.
- **REVALIDATE node.** FIN-POL-007 §5 wants time-sensitive vendor and delegation data re-checked when a held case resumes. Only approval-paused runs resume here, and the delegation date is re-checked at approval time; a held case is not resumable.
- **Live-tier polish.** The live tests run the five cases against Claude and assert the outcome is *never more permissive* than expected. They are reported, not gating, and there is no multi-run statistics.
- **AWS screenshots.** The deployment is reproducible from Terraform instead, and the deployed setup can be checked with the AWS CLI commands in [deploy/README.md](deploy/README.md#verify-the-deployed-setup-aws-cli).
- **CI.** `make test` and `make lint` are the reviewer path.

**Deliberate trade-offs:**
- **SQLite over Postgres.** It meets §8 and makes the idempotency semantics easy to show. Repository protocols are the seam for Postgres, but no second implementation ships.
- **BM25 over dense retrieval.** Sixteen documents; the registry pre-filter does the safety work, not ranking.
- **Explicit pause over LangGraph `interrupt()`.** The approval state is a database row, so resume does not depend on the checkpoint.
- **Templated queries over model-written queries.** Retrieval cannot be steered by injected text.
- **Hand-written retry over a library (tenacity).** Bounded retry is assessed, so it is visible in `orchestration/tool_runner.py`.
- **Full prompt capture for every run.** Cheap at five cases; at volume it would be tiered (architecture §9).
- **Extensions.** A run timeline (`?timeline=1`) and fault-injection tests both fall out of required work. Langfuse tracing was added at the end, off by default and observe-only; it adds no control.

## AI tool use, time spent, and material not authored (§7, §11)

- **AI tools.** Claude Code (Claude Opus 5.5) was used throughout as a pair programmer: to transcribe the brief, draft and critique the architecture, generate code and tests against that design, and draft documentation, including this README and the design note. Commits with substantial AI contribution carry a `Co-Authored-By: Claude` trailer. Every design decision was reviewed and is recorded with its reasoning in `docs/architecture.md` and `CLAUDE.md`. **TODO (author): adjust this description to match how you actually worked.**
- **Time spent.** **TODO (author): approximately N hours of active work.** For reference, the git history shows the build in one day, 2026-11-20:

  | Window (commit times) | Work |
  |---|---|
  | 08:37–11:05 | Project rules, brief transcription, architecture, project configuration |
  | 12:34–15:22 | Implementation phases P0–P7 (core, RAG, tools, controls, model boundary, graphs, API, evals), then the Terraform deployment |
  | 17:27–18:00 | Deploy fix, Langfuse tracing (local and AWS) |
  | 19:31–20:04 | Fixes from live-model evals (O8, risk indicators from case text only), README |

  That is about 11.5 hours from first to last commit, including breaks. Commits were made in batches, so the windows show when work was committed, not exactly when it was done. Reading the brief before the first commit is not included.
- **Not authored by me.**
  - Open-source libraries, pinned in `uv.lock` / `requirements.lock`: Pydantic, pydantic-settings, FastAPI, uvicorn, LangGraph and langgraph-checkpoint-sqlite, the Anthropic SDK, structlog, PyYAML, httpx, Langfuse; dev tools pytest, ruff, mypy.
  - The synthetic policy corpus in `fixtures/corpus/`. **TODO (author): state its origin.**
  - The brief (`docs/Take_Home_Test.pdf`).

## Repository layout

```
config/         app.yaml · policy_params.yaml · policy_registry.yaml
fixtures/       corpus/ (untrusted test data) · systems/ (simulated backends) · cases/FIN-00{1..5}.json
src/ap_agent/   one package per plane; imports point inward (tests/unit/test_import_boundaries.py)
  kernel/ intake/ evidence/ controls/ assessment/ decision/ orchestration/ infra/ services/ api/ cli.py
tests/          unit/ · contract/ · scenarios/ (stable, no key) · live/ (pytest -m live)
scripts/        transcripts.py
deploy/         terraform/ · scripts/ (AWS)
docs/           brief · architecture · design-note · transcripts/
```
