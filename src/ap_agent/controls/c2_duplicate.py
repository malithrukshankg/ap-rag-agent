"""C2 duplicate detection (FIN-POL-005 §1-2).

- Exact match (vendor, normalised invoice number, currency, gross amount) to a PAID or POSTED
  record → REJECT_DUPLICATE, citing FIN-POL-005 §2. The decision is recorded as HOLD for AP
  review (RA#1 note).
- Probable duplicate → HOLD with both record ids: an exact match to a HELD or REJECTED
  record, the same punctuation-stripped number with different details, or the same PO with
  the invoice date within the window and the amount variance below the threshold.
"""

from ap_agent.controls.common import ResultBuilder
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.evidence.tools.history import normalize_invoice_ref
from ap_agent.kernel.enums import ControlId, ExceptionCategory, RecommendationCode

RULE = "FIN-POL-005 §1-2 duplicate invoice detection"
PARAMS = ("c2_exact_match_rule", "c2_fuzzy_date_window_days", "c2_fuzzy_amount_pct")


def check(ctx: ControlContext) -> ControlResult:
    b = ResultBuilder(ctx, ControlId.C2, RULE)
    cited = b.cite(*PARAMS)
    has_history = b.use(ctx.history, "invoice history")
    if not (cited and has_history) or ctx.history.value is None:
        return b.indeterminate(ExceptionCategory.DUPLICATE_RISK)

    case, history = ctx.case, ctx.history.value
    window = int(ctx.params["c2_fuzzy_date_window_days"].value)
    pct = ctx.params["c2_fuzzy_amount_pct"].decimal()
    ref = normalize_invoice_ref(case.invoice_ref)

    exact = [r for r in history.candidates if r.fingerprint == history.request_fingerprint]
    paid = [r for r in exact if r.status in ("PAID", "POSTED")]
    if paid:
        ids = ", ".join(f"{r.record_id} ({r.status})" for r in paid)
        return b.failed(
            RecommendationCode.REJECT_DUPLICATE,
            ExceptionCategory.DUPLICATE_RISK,
            expected="no exact match to a paid or posted invoice",
            observed=f"invoice {case.invoice_ref} exactly matches {ids}",
        )

    probable: list[str] = []
    for r in history.candidates:
        if r in exact:
            probable.append(f"{r.record_id}: exact match to a {r.status} record")
            continue
        if r.normalized_ref == ref:
            probable.append(f"{r.record_id}: same invoice number {r.invoice_ref}")
            continue
        if r.currency != case.currency or case.po_ref is None or r.po_ref != case.po_ref:
            continue
        days = abs((r.invoice_date - case.invoice_date).days)
        variance = b.calc(ctx.calc.variance(case.amount, r.amount, case.currency))
        limit = b.calc(ctx.calc.percent_of(r.amount, pct, case.currency))
        if days <= window and variance < limit:
            probable.append(
                f"{r.record_id}: same PO, {days} days apart, variance {variance} < {limit}"
            )

    if probable:
        return b.failed(
            RecommendationCode.HOLD_FOR_INFORMATION,
            ExceptionCategory.DUPLICATE_RISK,
            expected="no probable duplicate",
            observed=f"invoice {case.invoice_ref} vs " + "; ".join(probable),
            threshold=f"date within {window} days, amount variance below {pct}%",
        )
    return b.passed(
        expected="no exact or probable duplicate",
        observed=f"{len(history.candidates)} prior invoice(s) for vendor; none match",
    )
