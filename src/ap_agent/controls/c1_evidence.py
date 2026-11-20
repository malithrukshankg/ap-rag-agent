"""C1 minimum evidence (FIN-POL-001 §2).

- Unprocessable (no invoice reference, amount or vendor) → REJECT_INVALID. The schema already
  requires these fields; this catches values that are present but blank.
- No purchase-order reference → HOLD (MISSING_PO): FIN-POL-001 §2 requires a PO reference
  or an approved non-PO justification, and the latter is not a verifiable typed field.
"""

from ap_agent.controls.common import ResultBuilder
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import ControlId, ExceptionCategory, RecommendationCode

RULE = "FIN-POL-001 §2 minimum invoice evidence"


def check(ctx: ControlContext) -> ControlResult:
    b = ResultBuilder(ctx, ControlId.C1, RULE)
    case = ctx.case
    blank = [
        name
        for name, value in (
            ("invoice_ref", case.invoice_ref),
            ("vendor_id", case.vendor_id),
            ("vendor_name", case.vendor_name),
        )
        if not value.strip()
    ]
    if blank:
        b.cite("c1_required_fields")  # cite if available; structural failure stands anyway
        return b.failed(
            RecommendationCode.REJECT_INVALID,
            ExceptionCategory.OTHER_CONTROL_RISK,
            expected="invoice reference, vendor and gross amount present",
            observed=f"blank: {', '.join(blank)}",
        )

    if not b.cite("c1_required_fields"):
        return b.indeterminate()

    missing = []
    if case.po_ref is None:
        missing.append("purchase-order reference or approved non-PO justification")
    if missing:
        b.missing.extend(missing)
        return b.failed(
            RecommendationCode.HOLD_FOR_INFORMATION,
            ExceptionCategory.MISSING_PO,
            expected="every FIN-POL-001 §2 field present",
            observed="missing: " + "; ".join(missing),
        )
    return b.passed(
        expected="every FIN-POL-001 §2 field present",
        observed="supplier, invoice number and date, currency, gross amount, PO reference present",
    )
