# API request formats

Request and response formats for the four operations (brief §2). The live OpenAPI schema is served at `/docs`. The endpoint summary and a FIN-001 walkthrough are in the [README](../README.md#using-the-api).

## `POST /runs`: start a run

The body is a `case` and an optional `simulation` block:

```json
{
  "case": {
    "case_id": "FIN-001",
    "invoice_ref": "INV-1001-0815",
    "vendor_id": "V-1001",
    "vendor_name": "Acme Office Supplies Pty Ltd",
    "amount": "6050.00",
    "tax_amount": "550.00",
    "currency": "AUD",
    "invoice_date": "2026-08-15",
    "po_ref": "PO-1001",
    "requested_by": "u-req-01",
    "lines": [
      {"line_no": 1, "description": "Ergonomic office chair", "quantity": "20",
       "unit_price": "245.00", "line_total": "4900.00", "po_line_no": 1},
      {"line_no": 2, "description": "LED desk lamp", "quantity": "10",
       "unit_price": "60.00", "line_total": "600.00", "po_line_no": 2}
    ],
    "notes": "optional free text",
    "attachments": [{"name": "remittance.txt", "text": "optional free text"}]
  },
  "simulation": {"faults": {}, "scripted_model": []}
}
```

- **Required:** `case_id`, `invoice_ref`, `vendor_id`, `vendor_name`, `amount` (gross), `currency`, `invoice_date`, `requested_by`.
- **Optional:** `tax_amount`, `po_ref`, `lines`, `notes`, `attachments`.
- Send amounts as strings. They are parsed as decimals, never floats.
- `notes`, `attachments` and line descriptions are untrusted case text. They are evidence, never instructions.
- `simulation` is a **SIMULATED** test hook and can be omitted. It carries fault injection (for example FIN-004's PO timeouts) and the scripted model's completions. The scripted completions are ignored when the anthropic adapter is configured.
- The fixture vendors and POs are in `fixtures/systems/`, and each `fixtures/cases/FIN-00x.json` has a ready-made `case`.

The reply is `201` with the run view: `run_id`, `status`, `current_node`, `budget`, `result` and `audit_events`.

## `GET /runs/{run_id}`: get a run

No body. Add `?timeline=1` for a compact event timeline.

## `POST /runs/{run_id}/approval`: approve or reject

For a run whose status is `AWAITING_APPROVAL`:

```json
{
  "verdict": "approve",
  "approver_id": "A-CCM-01",
  "decision_fingerprint": "0336c973682684d92c9f99a1b9f6873ed93b8054fd1b1b200497cd3d6aa2608a"
}
```

- `verdict` is `approve` or `reject`.
- `approver_id` must exist in the simulated register `fixtures/systems/approvers.json`. Use `A-CCM-01` (up to AUD 10,000), `A-DD-01` (up to AUD 50,000) or `A-FC-01` (up to AUD 250,000).
- `decision_fingerprint`: see below.

### The decision fingerprint

A SHA-256 hex string (64 characters) that pins the approval to exactly what the approver reviewed. It is computed in code over the case id, invoice ref, vendor, amount, currency, the recommended outcome, every control's status, and the hashes of the cited evidence.

- **Getting it:** the run's response carries it as `result.decision_fingerprint`. The same value is in `result.approval.decision_fingerprint` while the approval is `PENDING`, and in the `approval.requested` audit event. Read it from the `POST /runs` reply or from `GET /runs/{run_id}`, and send it back unchanged:
  ```bash
  FP=$(curl -s $API/runs/$ID | jq -r .result.decision_fingerprint)
  ```
- **Why it's required:** if the evidence or outcome changed after the approver looked, the fingerprints no longer match, the approval is refused, and the run stays pending.

### Responses

| Status | Meaning | Body |
|---|---|---|
| `200` | Accepted. Every repeat of the same approval returns the same body. | `{"run_id", "accepted": true, "response": {"approval_id", "approval_status", "approver_id", "verdict", "decided_at", "decision", "decision_id", "idempotency_key", "ledger_ref", "run_id"}}` |
| `409` | A check failed; the run stays `AWAITING_APPROVAL` | `{"accepted": false, "failed_check": …}`. `failed_check` is one of: `fingerprint_mismatch`, `approver_unknown_or_inactive`, `authority_limit`, `approver_raised_case`, `delegation_not_valid` |
| `404` | Unknown `run_id` | `{"detail": …}` |

## `POST /evals` and `GET /evals/latest`: evaluation results

`POST /evals` takes no body. It runs FIN-001 to FIN-005 against the configured model adapter and returns the metrics and per-case checks. `GET /evals/latest` returns the last report without re-running, or `404` if no evaluation has been run yet.
