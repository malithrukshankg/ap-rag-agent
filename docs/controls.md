# Controls C1–C6 and deterministic arithmetic

This document explains how the agent reconciles an invoice: the six controls that decide the outcome, how their results combine, and how every number is calculated. It is a companion to the [design note](design-note.md). The controls live in [`src/ap_agent/controls/`](../src/ap_agent/controls/).

The rule behind all of it: **code decides, the model describes.** Every number, every match and every control status comes from deterministic Python. The language model never calculates anything, and it cannot switch a control off or loosen its result (brief §4).

## 1. Where the controls run

```
INTAKE → RETRIEVE → GATHER → RECONCILE → ASSESS → DECIDE
                                 │                    │
                       C1–C6 run here        C6 runs again with the model's
                                             verified risk indicators, then
                                             results are aggregated
```

- **RETRIEVE** fetches the policy sections the controls need (one query per control).
- **GATHER** fetches the vendor record, invoice history, purchase order and FX rate through the simulated tools.
- **RECONCILE** runs all six controls in a fixed order ([`engine.py`](../src/ap_agent/controls/engine.py)).
- **DECIDE** re-runs C6 with any risk indicators the model found and that passed citation checks. It then aggregates everything into one recommendation.

**Every control runs on every invoice.** The list is fixed in code. Nothing in the case text, a retrieved document or the model's output can skip a control or decide it doesn't apply. The only "not applicable" result is C6 when there is literally no free text to inspect, and that is a field check, not a judgement.

## 2. What every control returns

Each control returns a `ControlResult` ([`result.py`](../src/ap_agent/controls/result.py)):

| Field | Meaning |
|---|---|
| `status` | `PASS`, `FAIL`, `INDETERMINATE` (could not be evaluated) or `NOT_APPLICABLE` |
| `outcome_floor` | the least strict outcome this control allows (see §4) |
| `exception_category` | the policy's exception type (FIN-POL-007 §1), e.g. `BANK_CHANGE`, `MISSING_RECEIPT` |
| `rule` | the policy section the control implements |
| `expected` / `observed` / `threshold` | human-readable statements of what should be true, what was found, and the limit applied |
| `citations` | the policy text that grounds the rule (built by code, never by the model) |
| `calculation_ids` | links to every arithmetic record the control used (§5) |
| `tool_call_ids` | links to the tool calls that supplied its evidence |
| `missing_evidence` | what could not be obtained, in plain words |

### The literal check: why retrieval matters to a control

The numbers the controls use (tolerances, authority limits, the indicator threshold) live in [`config/policy_params.yaml`](../config/policy_params.yaml). Each one is tagged with the document, section and version it comes from, **and the exact sentence that document uses**. For example:

```yaml
c4_goods_abs: {value: "50", doc_id: FIN-POL-002, section: 2, version: "2.4",
               literal: "AUD 50 absolute variance"}
```

Before a control uses a parameter, it checks that the literal sentence appears in a **trusted** chunk retrieved **in this run**, at the right document, section and version ([`params.py`](../src/ap_agent/controls/params.py) `verify_literal`). If so, that chunk becomes the control's citation. If not, the control is `INDETERMINATE`.

This does three things:
- Every rule a control applies is grounded in a cited policy sentence.
- If someone edits the policy document or the config so they disagree, the control stops instead of silently applying a stale number.
- Untrusted documents (such as the adversarial ADV-001) can never supply a parameter, because only trusted chunks qualify.

### INDETERMINATE means "hold", never "guess"

A control that cannot get its evidence, such as a PO API timeout or a missing policy sentence, returns `INDETERMINATE` with the gap listed in `missing_evidence`. An indeterminate control always forces the outcome to at least `HOLD_FOR_INFORMATION`. Nothing is inferred to fill a gap (FIN-POL-001 §5).

## 3. The six controls

### C1: Minimum evidence (FIN-POL-001 §2)

**Question:** does the invoice carry the information policy says every invoice must have?

**Inputs:** the case only.

| Check | Result if it fails |
|---|---|
| Invoice reference, vendor id and vendor name are present and not blank | `FAIL`, floor **`REJECT_INVALID`**: the invoice is unprocessable and is returned to the sender. This is the only case where the agent itself writes a `REJECTED` decision. |
| A purchase-order reference is present | `FAIL`, floor **`HOLD_FOR_INFORMATION`**, category `MISSING_PO`. Policy also allows an "approved non-PO justification", but that is free text and can't be verified, so it can't satisfy the check. |

The request schema already rejects a missing amount, currency or date at the API boundary. C1 catches values that are present but blank.

### C2: Duplicate invoice (FIN-POL-005 §1–2)

**Question:** has this invoice, or something suspiciously like it, been seen before?

**Inputs:** `check_invoice_history` (the vendor's prior invoices) and three policy parameters: the exact-match rule, the 14-day window and the 0.5% amount variance.

**Exact match.** The history tool computes a fingerprint, `sha256(vendor_id | normalised invoice number | currency | gross amount to 2 dp)`. Normalising means upper-case with spaces and punctuation removed, so `inv-1001/0815` and `INV 10010815` are the same number.

| Finding | Result |
|---|---|
| Exact match to a `PAID` or `POSTED` invoice | `FAIL`, floor **`REJECT_DUPLICATE`**, citing FIN-POL-005 §2. The decision written is a **`HOLD`** so that AP staff confirm the rejection; the agent does not reject on its own. |
| Exact match to a `HELD` or `REJECTED` invoice | `FAIL`, floor `HOLD_FOR_INFORMATION` (probable duplicate) |
| Same normalised invoice number, different details | `FAIL`, floor `HOLD_FOR_INFORMATION` |
| Same PO and currency, invoice dates **≤ 14 days** apart, **and** an amount variance **below 0.5%** of the earlier invoice | `FAIL`, floor `HOLD_FOR_INFORMATION`, with both record ids |
| None of the above | `PASS` |

The fuzzy check is arithmetic: `variance = |this amount − earlier amount|` against `limit = earlier amount × 0.5 / 100`, and it fires when `variance < limit`. Both values are recorded as calculations.

**Exercised by:** FIN-002 (exact match to a paid invoice).

### C3: Vendor status and payment details (FIN-POL-004 §2, §4)

**Question:** is the vendor allowed to be paid, and are we paying the account we verified?

**Inputs:**
- `get_vendor_record`: status and payment details. Payment details are `last4`, bank country, an HMAC fingerprint and the change date. Never the full account number.
- `check_invoice_history`: to tell when the vendor was last paid.
- Any account number the **case's own text** claims.

| Check | Result if it fails |
|---|---|
| Vendor status is not one of `BLOCKED`, `DORMANT`, `SANCTIONS_REVIEW`, `PENDING_VERIFICATION` | `FAIL`, floor `HOLD_FOR_INFORMATION`, category `VENDOR_BLOCK` |
| No account claimed in the case text differs from the vendor master. The text is searched for phrases like "account ending 8842", and the last four digits are compared in code. | `FAIL`, floor **`ESCALATE_CONTROL_REVIEW`**, category `BANK_CHANGE` |
| The bank details were not changed recently. **Recent** means changed after onboarding with no invoice paid since (our reading O3; FIN-POL-004 §2 requires co-approval of the first payment after a change). | `FAIL`, floor **`ESCALATE_CONTROL_REVIEW`**, category `BANK_CHANGE` |

A claimed account is evidence of risk, never an instruction. The agent always pays the verified master account or nothing. Only last-four digits are ever recorded, which FIN-POL-004 §2 permits in logs.

**Exercised by:** FIN-003. The attachment claims an account ending 8842; the master account ends 4417.

### C4: Three-way match (FIN-POL-002 §1–5, FIN-POL-009 §3, FIN-POL-012)

**Question:** do the invoice, the purchase order and the goods receipts agree, within tolerance?

**Inputs:** `get_purchase_order` (lines, prices, tolerance, approval status, receipts), `get_fx_rate` when the invoice currency differs from the PO's, and the tolerance parameters.

**If there is no PO**, C4 consults the non-PO policy (FIN-POL-012, "a missing PO is not automatically an emergency"). Non-PO evidence can't be verified, so the result is `FAIL`, floor `HOLD`, category `MISSING_PO`, with the missing items listed.

**If the PO call fails** (FIN-004: four timeouts, retries exhausted), C4 is `INDETERMINATE`. The receipt is listed as missing because it can't be checked without the PO.

**Currency.** If the invoice and PO currencies differ and the PO does not permit conversion, C4 fails with a hold (FIN-POL-009 §3). If conversion is permitted, invoice amounts are converted at the fixture's corporate daily rate first. Tolerances are in AUD, so a PO in another currency is `INDETERMINATE` rather than compared on a guessed basis.

**With a PO, these checks run:**

| # | Check | Failure category |
|---|---|---|
| 1 | The PO belongs to this vendor and is `APPROVED` | `OTHER_CONTROL_RISK` |
| 2 | Each invoice line maps to a PO line | `PRICE_VARIANCE` |
| 3 | Each PO line has a goods receipt | `MISSING_RECEIPT` (FIN-POL-002 §4) |
| 4 | Invoiced quantity ≤ quantity received (summed over receipts) | `QUANTITY_VARIANCE` |
| 5 | **Per line:** \|invoiced line total − quantity × PO unit price\| ≤ the line's tolerance | `PRICE_VARIANCE` |
| 6 | The invoice lines sum exactly to the invoice net amount (gross − tax) | `PRICE_VARIANCE` |
| 7 | **Total:** \|sum of invoiced lines − sum of expected lines\| ≤ the total tolerance | `PRICE_VARIANCE` |

Any failure gives floor `HOLD_FOR_INFORMATION`. The `observed` text names each failing line with its expected value, actual value, difference and threshold (FIN-POL-002 §3).

**Tolerance** (FIN-POL-002 §2) is the **lower** of an absolute amount and a percentage of the PO line value, by category:

| Category | Absolute | Percentage | Tolerance |
|---|---|---|---|
| Goods | AUD 50 | 1% | `min(50, line value × 1%)` |
| Services | AUD 100 | 2% | `min(100, line value × 2%)` |
| Freight | AUD 75 | none | `75` |

The total check uses the goods tolerance against the PO total. A variance exactly equal to the tolerance passes; only `variance > tolerance` fails.

**PO tolerance vs policy tolerance.** The PO record also carries a tolerance. Policy wins (our reading #8): the policy value is applied, and if the PO's value differs, the disagreement is written into `threshold` so a reviewer sees it.

**Exercised by:** FIN-001 (all lines match), FIN-004 (PO unavailable).

### C5: Delegated authority (FIN-POL-003 §1–3, FIN-POL-009 §2)

**Question:** who has the authority to approve this payment, and is a single approver enough?

**Inputs:** the vendor record, the invoice history (for bank-change recency), the FX rate for a foreign-currency invoice, and the authority limits.

**Step 1: the amount.** The **total commitment including tax** (FIN-POL-003 §1), converted to AUD at the corporate daily rate if needed (FIN-POL-009 §2).

**Step 2: the required role.** The lowest role whose limit covers the amount:

| Role | Limit (AUD, inclusive) |
|---|---|
| Cost Centre Manager | 10,000 |
| Department Director | 50,000 |
| Executive Director | 250,000 |
| Chief Financial Officer | 1,000,000 |
| Chief Executive Officer | above that |

**Step 3: two-approver triggers** (FIN-POL-003 §3). If any of these holds, policy requires two approvals:
- the vendor was created less than 30 days before the invoice date;
- the bank details changed recently (the same O3 rule as C3);
- the vendor's bank account is overseas (not AU);
- the vendor carries a fraud risk flag.

| Finding | Result |
|---|---|
| A two-approver trigger fires | `FAIL`, floor **`ESCALATE_CONTROL_REVIEW`**, category `AUTHORITY_GAP`. There is no multi-approver posting path, so these cases go to Financial Control. |
| No trigger | `PASS`; `observed` states the required role and its limit |

C5 works out *what authority is needed*. Whether the person who later approves actually *has* it is checked separately, at approval time: the approval gate compares the approver's limit with the amount and runs four other checks.

**Exercised by:** FIN-001 (AUD 6,050 → Cost Centre Manager, no triggers).

### C6: Untrusted-content risk (FIN-POL-005 §3–4)

**Question:** does the free text in this case show signs of fraud or manipulation?

**Inputs:** the case's own notes and attachments, plus any risk indicators the model reported whose quotes were verified against the case text.

Four indicator types are detected with fixed patterns ([`c6_risk.py`](../src/ap_agent/controls/c6_risk.py)):

| Indicator | Example phrases |
|---|---|
| `NEW_BANK_DETAILS` | "bank account has changed", "new account" |
| `URGENT_OR_SECRET_LANGUAGE` | "urgent", "immediately", "today", "confidential" |
| `BYPASS_APPROVAL_REQUEST` | "do not ask … approver", "skip approval", "already approved" |
| `DISABLE_CHECKS_INSTRUCTION` | "ignore … policies", "skip … detection", "mark … verified" |

A document telling the reader to disable checks is itself a risk indicator (FIN-POL-005 §4). It is counted, never obeyed.

| Finding | Result |
|---|---|
| No free text and no model indicators | `NOT_APPLICABLE` |
| **Two or more distinct** indicator types (FIN-POL-005 §3: "escalate when two or more indicators occur") | `FAIL`, floor **`ESCALATE_CONTROL_REVIEW`**; the target is Financial Crime and Controls |
| Fewer than two | `PASS` |

One indicator alone does not escalate. So a benign note such as "please pay today" on an otherwise clean invoice isn't treated as fraud.

**Only the case's own text counts.** A model indicator is accepted only if it quotes `case:notes` or `case:attachment:N`. Keyword search can return the adversarial ADV-001 document for an unrelated clean invoice, and its wording must not put that invoice on hold.

**Exercised by:** FIN-003, which finds all four indicator types.

## 4. Combining the results into one recommendation

[`aggregate.py`](../src/ap_agent/controls/aggregate.py) turns six control results, plus the model's proposal, into one outcome.

**Severity order** (strictest first):

```
ESCALATE_CONTROL_REVIEW > REJECT_DUPLICATE > REJECT_INVALID > HOLD_FOR_INFORMATION > APPROVE_FOR_POSTING
```

**Effective floor per control:**
- `PASS` or `NOT_APPLICABLE` → `APPROVE_FOR_POSTING`
- `FAIL` → its own floor, and never less than `HOLD`
- `INDETERMINATE` → at least `HOLD`

**The model's proposal** is limited before it counts:
- It may propose only `HOLD` or `ESCALATE`. A proposed rejection is treated as `HOLD` (our reading O4), so injected text cannot make the agent reject a supplier.
- A proposed `ESCALATE` counts only if the model also reported at least one verified risk indicator. Otherwise it counts as `HOLD` (our reading O8).

**Outcome** = the strictest of all six floors and the limited proposal. Consequences:
- `APPROVE_FOR_POSTING` happens **only** when every applicable control passed and the model did not object.
- The model can make the outcome stricter, never looser.

**Primary exception and owner.** When several controls tie at the winning outcome, a fixed precedence list in `config/app.yaml` picks one category and escalation target (our reading O5). Suspected fraud (C6) comes first, then bank change, authority gap, duplicate risk, vendor block, and the PO/receipt categories. All other exceptions are still listed in the result.

**From recommendation to decision:**

| Recommendation | Decision written through `submit_finance_decision` | Approval needed? |
|---|---|---|
| `APPROVE_FOR_POSTING` | none yet: an approval request is created and the run pauses. After approval → `POST_FOR_PAYMENT`; after rejection → `REJECTED`. | **Yes** |
| `HOLD_FOR_INFORMATION` | `HOLD` | No |
| `REJECT_DUPLICATE` | `HOLD`, for AP to confirm the rejection | No |
| `REJECT_INVALID` | `REJECTED` | No |
| `ESCALATE_CONTROL_REVIEW` | `HOLD`, routed to the named owner | No |

**Confidence** is also computed in code ([`decision/confidence.py`](../src/ap_agent/decision/confidence.py)): `LOW` if any control is indeterminate, `MEDIUM` if a model claim was dropped or the assessment was unavailable, `HIGH` otherwise.

## 5. Deterministic arithmetic

All arithmetic goes through one class, `Calculator` in [`calc.py`](../src/ap_agent/controls/calc.py). It is the only place in the system where a number in the result is produced.

### Rules

| Rule | Why |
|---|---|
| Inputs must be Python `Decimal` (or `int`). Passing a `float` raises `TypeError`. | Binary floating point cannot represent most cent values exactly: `0.1 + 0.2 != 0.3` in floats. Money must be exact. |
| Each operation is computed **exactly**, then the result is rounded **once**: `ROUND_HALF_UP` to 2 decimal places. | Rounding once avoids errors accumulating across steps. FIN-POL-002 §5 requires "decimal arithmetic and the invoice currency" and a record of the rounding method, but names no method. ROUND_HALF_UP (2.345 → 2.35) is our choice: the familiar commercial convention. |
| Inputs are recorded **unrounded**. | An FX rate such as 1.523917 keeps its precision in the record, so the calculation can be reproduced exactly. |
| Every operation returns a `Calculation` record that is stored and returned in the final result. | FIN-POL-002 §5: "the calculation record must store input values, formula, result and rounding method". An auditor can re-run each one by hand. |
| Record ids are sequential per run (`calc-001`, `calc-002`, …). | Re-running the same case produces the same ids, which keeps runs reproducible. |
| The model never produces a number that reaches the result. | Brief §4: no free-form model arithmetic. |

A `Calculation` record:

```json
{"id": "calc-003",
 "formula": "min(absolute_limit, po_line_value * percent_limit / 100)",
 "inputs": {"po_line_value": "4900.00", "absolute_limit": "50", "percent_limit": "1"},
 "result": "49.00",
 "rounding": "ROUND_HALF_UP to 2 dp",
 "currency": "AUD"}
```

### Operations

| Method | Formula | Used by |
|---|---|---|
| `line_value` | `quantity * unit_price` | C4 expected line value |
| `total` | `amount_1 + amount_2 + …` | C4 line and total sums |
| `variance` | `abs(actual - expected)` | C2 fuzzy match, C4 line and total checks |
| `difference` | `actual - expected` (signed) | available for signed comparisons |
| `percent_of` | `base * percent / 100` | C2 0.5% limit |
| `tolerance_limit` | `min(absolute_limit, po_line_value * percent_limit / 100)` | C4 tolerances |
| `convert` | `amount[FROM] * rate[FROM->TO]` | C4 and C5 for foreign-currency invoices. The rate's source and date are recorded with the tool call. |

Comparisons such as `variance > limit` happen in the control, on the already-rounded `Decimal` results. They are exact comparisons, not floating-point ones.

### Worked example: FIN-001

Invoice INV-1001-0815: gross **AUD 6,050.00**, tax AUD 550.00, two lines against PO-1001. The PO total is AUD 5,500.00, and both lines were fully received.

| id | What | Formula and inputs | Result |
|---|---|---|---|
| calc-001 | Line 1 expected value | 20 × 245.00 | 4,900.00 |
| calc-002 | Line 1 variance | \|4,900.00 − 4,900.00\| | 0.00 |
| calc-003 | Line 1 tolerance | min(50, 4,900.00 × 1%) = min(50, 49.00) | **49.00** |
| calc-004 | Line 2 expected value | 10 × 60.00 | 600.00 |
| calc-005 | Line 2 variance | \|600.00 − 600.00\| | 0.00 |
| calc-006 | Line 2 tolerance | min(50, 600.00 × 1%) = min(50, 6.00) | **6.00** |
| calc-007 | Sum of invoiced lines | 4,900.00 + 600.00 | 5,500.00 |
| calc-008 | Lines vs net amount | \|(6,050.00 − 550.00) − 5,500.00\| | 0.00 |
| calc-009 | Sum of expected lines | 4,900.00 + 600.00 | 5,500.00 |
| calc-010 | Total variance | \|5,500.00 − 5,500.00\| | 0.00 |
| calc-011 | Total tolerance | min(50, 5,500.00 × 1%) = min(50, 55.00) | **50.00** |

Every variance is within its tolerance, every line has a receipt, and the lines sum to the net amount, so C4 passes. C5 takes the gross AUD 6,050.00, which is ≤ 10,000, so a Cost Centre Manager can approve. No two-approver trigger applies. All controls pass, and the recommendation is `APPROVE_FOR_POSTING`, pending a human approver.

The percentage tolerance does real work here. On line 2 a variance of AUD 7 would be well under the AUD 50 absolute limit, but it exceeds 1% of the AUD 600 line (AUD 6.00), so it would fail.

The full output of this run, with its calculations, is in [transcripts/FIN-001.md](transcripts/FIN-001.md).

## 6. Limitations

- **Pattern-based indicators.** C6 and C3's claimed-account check use fixed English patterns. A paraphrased or translated manipulation attempt could slip past the patterns; the model's verified indicators are the second line of defence.
- **Changed meaning goes undetected.** The literal check catches a deleted or changed number in a policy, not a change in its meaning around an unchanged sentence.
- **Foreign-currency limits.** C4 assumes AUD tolerances, so a non-AUD PO is indeterminate. At approval time a foreign-currency posting fails closed, because approver limits are in AUD.
- **Narrow fuzzy matching.** C2's fuzzy rule relies on the same PO. Near-duplicates under a different PO are caught only by the same-invoice-number rule.
- **No multi-approver path.** C5 escalates whenever policy requires two approvers.

## 7. Tests

| What | Where |
|---|---|
| Each control's PASS, FAIL and INDETERMINATE paths | [`tests/unit/test_controls.py`](../tests/unit/test_controls.py) |
| Aggregation, the O4 cap and the O8 downgrade | [`tests/unit/test_aggregate.py`](../tests/unit/test_aggregate.py) |
| Decimal rules: rounding, float rejection | [`tests/unit/test_calc.py`](../tests/unit/test_calc.py) |
| Every policy literal present in the ingested corpus | [`tests/unit/test_policy_params.py`](../tests/unit/test_policy_params.py) |
| FIN-001…005 end to end | [`tests/scenarios/test_fin_cases.py`](../tests/scenarios/test_fin_cases.py) |
