# Implementation plan — ap-rag-agent

Internal working document. Builds `docs/architecture.md` phase by phase.

## Context
`docs/architecture.md` holds the final design for the take-home AP agent. This plan builds the design within the 8-hour timebox (§8). Phases follow the architecture's "never cut" list, so the approval gate, idempotency, redaction, citations, the superseded filter, budget/retry and restart-resume land before the polish items. Each phase ends green under `make test`.

**Status:** P0 ✅ · P1 ✅ · P2 ✅ · P3 ✅ · P4 ✅ · P5 ✅ · P6–P8 pending.

**Resolved in P4:** the C5 limits-table gap found in P2 (and the same gap for C3's FIN-POL-004 §4) is fixed by two always-on query arms, C3-STATUS and C5-LIMITS (architecture §3.4). Confidence moved to `decision/confidence.py` because it returns the `Confidence` type from `decision/`; `EscalationRule` moved to `controls/policy.py` so controls never import infra.

**Open-item resolutions (user-confirmed, recorded in architecture.md §15):**
- **O1:** a document with no `##` heading becomes one chunk with section number 0.
- **O2:** FIN-POL-003-OLD is listed in the registry with `pinned_version: null`.
- **O3:** a bank change is "recent" when `changed_at` is after the vendor's last paid invoice (FIN-POL-004 §2).
- **O4:** the model's proposal is capped at HOLD_FOR_INFORMATION or ESCALATE_CONTROL_REVIEW. REJECT_* comes only from controls.
- **O5:** fixed precedence for the primary category and target, held in config: C6 fraud > BANK_CHANGE > AUTHORITY_GAP > DUPLICATE_RISK > VENDOR_BLOCK > PO/receipt > OTHER_CONTROL_RISK.
- **O6:** 2 repair attempts after the initial model call.
- **O7:** on startup, stale RUNNING rows become FAILED(interrupted) with an audit event, and the caller re-runs the case.

## Layout (our decision; architecture §0.4)
```
pyproject.toml  uv.lock  requirements.lock  Makefile  Dockerfile  .env.example
config/   app.yaml  policy_params.yaml  policy_registry.yaml
fixtures/ corpus/ (exists)  systems/{vendors,purchase_orders,invoice_history,fx_rates,approvers}.json
          cases/FIN-00{1..5}.json   # request + faults + expected + scripted model completion(s)
src/ap_agent/          # one package per plane, enforced by tests/unit/test_import_boundaries.py
  kernel/            # enums, types (Strict, Money, Currency)
  intake/            # TB1: case.py (CaseRequest, Tainted)
  evidence/          # TB2: citation.py; rag/ (ingest, index, facets, queries);
                     #      tools/ (base, schemas, faults, retrieve, vendor, purchase_order, history, fx)
  controls/          # calc.py, result.py, policy.py, params.py (literal check), c1…c6,
                     # aggregate.py, engine.py (confidence.py is in decision/)
  assessment/        # TB3: client.py (ModelClient), schema.py, adapters/ (anthropic, scripted),
                     #      prompt.py, redaction.py, verify.py (citations), assess.py (repair loop)
  decision/          # TB4: routing.py, grant.py, result.py, fingerprint.py, approvals.py (5 checks),
                     #      submit.py (submit_finance_decision)
  orchestration/     # state.py, budget.py, tool_runner.py, tool_registry.py (allow-lists), nodes.py,
                     # start_graph.py, resume_graph.py, evals/ (runner, metrics)
  infra/             # settings.py (ONLY place reading env/YAML); persistence/ (protocols, records,
                     # sqlite/); observability/ (events, logger)
  api/               # app.py, routes for runs, approval, evals
  cli.py             # `ap ingest`
tests/ unit/  contract/  scenarios/  live/   (pytest markers: live)
```

## Phases (timebox ≈ 8h)

### P0: Scaffold and decisions (0.5h) ✅
- `uv` project with pinned dependencies: pydantic, pydantic-settings, fastapi, uvicorn, langgraph, langgraph-checkpoint-sqlite, anthropic, structlog, pyyaml, httpx; dev: pytest, ruff, mypy. `uv.lock` and exported `requirements.lock`.
- Makefile targets: `install`, `ingest`, `test` (`-m "not live"`), `test-live`, `lint` (ruff + mypy --strict), `run`, `docker-build`.
- `config/app.yaml` (budget, model, tools, storage, retrieval, logging, O5 precedence), `policy_registry.yaml`, `.env.example`.
- O1–O7 recorded in `docs/architecture.md` §15 and CLAUDE.md "Open questions".

### P1: Kernel, calc, persistence, logging (1h) ✅
- Types from architecture §4.3, §5, §7, §8, split by plane: `kernel/`, `intake/`, `evidence/`, `controls/result.py`, `assessment/schema.py`, `decision/result.py`. The recommendation→decision mapping is a table (`decision/routing.py`); the severity order and O4 cap are tables (`controls/policy.py`).
- `controls/calc.py`: Decimal ROUND_HALF_UP 2dp. Every op returns a `Calculation{id, formula, inputs, result, rounding, currency}`.
- `infra/persistence/sqlite/schema.sql`: all tables in architecture §9 with the three UNIQUE constraints and append-only audit triggers. WAL mode, `BEGIN IMMEDIATE` for writes. Repository Protocols in `infra/persistence/protocols.py`.
- `infra/observability/`: one payload model per event type plus the logger. `run_id` is bound as the correlation id.
- Import-boundary test enforces the plane layering.

### P2: RAG (1h) ✅
- `evidence/rag/ingest.py`: front matter parse (versions read as strings), lowercase jurisdiction, chunk per `##` (O1), `{doc_id}§{n}@v{version}`, sha256 per chunk, registry table. Corpus+registry hash in `ingest_meta` makes it idempotent. `cli.py` exposes `ap ingest`.
- `evidence/rag/index.py`: the FTS5 query with the registry-scoped filter from architecture §3.3. Trust is derived from registry membership. Free-text terms are quoted tokens.
- `evidence/rag/facets.py`, `evidence/rag/queries.py`: the template table from architecture §3.4 (9 templates, at most 8 per run). Empty placeholders are skipped.
- `infra/settings.py`: pydantic-settings over `config/app.yaml` + env.
- Tests:
  - superseded doc never returned, for any invoice date in fixtures;
  - ADV-001 returned via CASE query with `trust=untrusted`;
  - ADV-002 untrusted;
  - FTS operator injection inert;
  - re-ingest is a no-op;
  - recall@5 helper.

### P3: Tools and fixtures (1h) ✅
- `orchestration/tool_runner.py` ToolRunner: input/output schema validation, timeout (thread + deadline), transient-only retry with bounded backoff, budget charge per attempt, `tool_calls` row and audit event with duration and outcome.
- `evidence/tools/faults.py`: per-case `faults: {tool: [timeout, transient, ok…]}`, consumed per attempt.
- Read tools in `evidence/tools/` backed by `fixtures/systems/*.json`. `vendor.py` returns `PaymentDetails{last4, bank_country, hmac_fingerprint, changed_at}`, with the HMAC key from Settings. Full numbers stay in fixture JSON only.
- `orchestration/tool_registry.py`: node → allowed tools. Calling a tool outside the allow-list raises.
- Fixtures for FIN-001…005, with invoice dates ≥ 2026-08-01:
  - FIN-002: an exact paid record.
  - FIN-003: an injection attachment, plus a claimed account ending 8842 that mismatches master.
  - FIN-004: a PO timeout ×4 and no receipt.
- Tests (contract): schemas, timeout→retry→exhaust, transient vs permanent, allow-list enforcement.

### P4: Controls (1.25h) ✅
- `controls/params.py`: load `policy_params.yaml`. `verify_literal(param, retrieved_chunks)` → INDETERMINATE if the literal is missing. **First grep the corpus for every literal** and store exact strings.
- C1–C6 per architecture §4.4:
  - C3 uses O3 and compares last4/fingerprint in code.
  - C4 handles line and total, receipt qty, min(AUD 50, 1%), PO vs policy tolerance flag, FX hold (FIN-POL-009 §2–3), and the no-PO path.
  - C5 converts to AUD via get_fx_rate and applies the two-approver flag.
  - C6 uses deterministic indicator patterns over untrusted text.
- Each control emits code-built citations.
- `controls/aggregate.py`: precedence, model proposal capped (O4), INDETERMINATE → ≥ HOLD, primary category and target by O5.
- `decision/confidence.py`: LOW / MEDIUM / HIGH rule (lives in decision/: it returns the `Confidence` result type).
- Tests (unit): each control's PASS, FAIL and INDETERMINATE paths; aggregation table; a model proposal of REJECT_INVALID gets capped; applicability can't be skipped by text.

### P5: Model boundary (0.75h) ✅
- `assessment/adapters/scripted.py` (completions from case fixture, including a malformed-first variant), `assessment/adapters/anthropic.py` (model id and limits from Settings; JSON output parsed into `ModelAssessment`).
- `assessment/prompt.py`: system prompt is static. Evidence goes into an `<untrusted_evidence>` block built from allow-listed, redacted fields (`assessment/redaction.py`) with provenance and trust labels.
- `assessment/assess.py`: validate → repair with errors (O6) → UNAVAILABLE + HOLD floor. Every attempt writes a `model_calls` row (`prompt_sha256`, `prompt_redacted`, completion) and charges the budget.
- `assessment/verify.py` (TB3): chunk in the run's retrieval set, verbatim quote, hash match. An untrusted chunk cited as authority is rejected. Rejected claims become unknowns, plus a `citation.rejected` event. Claims contradicting a control status become unknowns too.
- Tests: repair succeeds on the 2nd attempt; three bad completions → UNAVAILABLE; forged quote, foreign chunk and untrusted-as-authority all rejected.

### P6: Graph, approval gate, submit, idempotency (1.25h)
- `orchestration/budget.py` charges steps and calls and raises `BudgetExhausted` → FAILED, with no decision.
- `orchestration/start_graph.py` and `resume_graph.py` are LangGraph StateGraphs with SqliteSaver, `thread_id=run_id`. Nodes receive Settings and ModelClient by injection. Checkpoints are deleted on terminal state.
- `decision/fingerprint.py`: canonical JSON → sha256.
- `decision/approvals.py`: REQUEST_APPROVAL writes a row. `resolve(run_id, verdict, approver_id, fingerprint)` runs in one transaction:
  - an already-resolved row returns the response rendered from stored rows;
  - otherwise it runs the 5 checks and mints a single-use grant;
  - a failed check writes an audit event and leaves the run pending.
- `decision/submit.py` follows the 8 ordered steps in architecture §6.3. The idempotency lookup comes before the grant check. The ledger post is idempotent on the key and runs in its own step. Then one transaction writes the decision, consumes the grant and appends the audit event.
- Startup hook: stale RUNNING → FAILED(interrupted) (O7).
- Tests (scenarios):
  - FIN-001…005 end to end via the scripted adapter;
  - budget exhaustion;
  - crash injected between ledger and decision → restart → exactly one posting;
  - restart while AWAITING_APPROVAL → approve resumes;
  - POST without a grant refused;
  - same key with a different payload → conflict;
  - canary grep over every DB text column, logs and `prompt_redacted`.

### P7: API and evals (0.5h)
- `api/`: the endpoints in architecture §10, with Pydantic request and response models (OpenAPI). `GET /runs/{id}?timeline=1` renders from audit_events (extension).
- `orchestration/evals/`: run FIN-001…005 and compute the architecture §11 metrics → pass/fail table. Served by `POST /evals` and `GET /evals/latest`.
- FIN-005 concurrent double callback test via httpx (`asyncio.gather` of two approval POSTs): one decision, byte-identical bodies.
- `tests/live/`: the same five cases with `-m live` and the anthropic adapter.

### P8: Deliverables (0.75h)
- Dockerfile runs `ap ingest` at build. Container runs `uvicorn --workers 1` with `/data` as a volume.
- AWS: CLI commands (EC2, separate EBS at /data, security group), screenshot placeholders, cost and cleanup notes (EBS deleted explicitly).
- `README.md`: prerequisites, env vars, model config, ingest/start/test/eval commands, real vs SIMULATED table, the PaymentDetails redaction statement, assumptions, limitations, AI-use declaration and time spent.
- `docs/design-note.md` (1–2 pages) distilled from architecture.md.
- Transcripts generated from FIN-001 (approve → post) and FIN-003 (exception).
- Unfinished work and trade-offs list (§8).

**Cut order if behind:** docker-compose → REVALIDATE node → live-tier polish → timeline → Langfuse (per architecture §13). Never cut the items in architecture §13 "Never cut".

## Verification
- `make lint`: ruff and `mypy --strict` clean.
- `make ingest && make test`: all unit, contract and scenario tests green, with no key and no network.
- `make run`, then `curl POST /runs` with `fixtures/cases/FIN-001.json` → AWAITING_APPROVAL. `POST /runs/{id}/approval` twice → identical bodies, one `sim_ledger` row. `GET /runs/{id}?timeline=1` shows the audit trail.
- `POST /evals` → table showing all five cases passing, superseded-returned = 0, adversarial-as-authority = 0.
- `ANTHROPIC_API_KEY=… make test-live`: the five cases against the real model (reported, not gating).
- `docker build` and `docker run -v data:/data`: same smoke flow.
