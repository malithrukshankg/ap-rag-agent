"""C3 vendor and payment details (FIN-POL-004 §2, §4).

- Vendor status in the FIN-POL-004 §4 hold list → HOLD (VENDOR_BLOCK).
- An account claimed in the case's own text whose last four digits differ from the vendor
  master, or a recent bank change (O3) → ESCALATE (BANK_CHANGE). Compared in code; the
  claimed account is never trusted, and only last-four digits are ever recorded
  (FIN-POL-004 §2 allows last four digits in logs).
"""

import re

from ap_agent.controls.common import ResultBuilder, bank_change_is_recent
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import ControlId, ExceptionCategory, RecommendationCode

RULE = "FIN-POL-004 §2, §4 vendor status and bank details"
PARAMS = ("c3_hold_statuses", "c3_bank_change_coapproval")
_ACCOUNT_ENDING = re.compile(r"account\s+(?:number\s+)?ending\s+(?:in\s+)?(\d{4})\b", re.I)


def claimed_last4(texts: list[str]) -> list[str]:
    return list(dict.fromkeys(m.group(1) for t in texts for m in _ACCOUNT_ENDING.finditer(t)))


def check(ctx: ControlContext) -> ControlResult:
    b = ResultBuilder(ctx, ControlId.C3, RULE)
    cited = b.cite(*PARAMS)
    has_vendor = b.use(ctx.vendor, "vendor master record")
    has_history = b.use(ctx.history, "invoice history (for bank-change recency)")
    vendor, history = ctx.vendor.value, ctx.history.value
    if not (cited and has_vendor and has_history) or vendor is None or history is None:
        return b.indeterminate(ExceptionCategory.VENDOR_BLOCK)

    hold_statuses = set(ctx.params["c3_hold_statuses"].value.split(","))
    if vendor.status in hold_statuses:
        return b.failed(
            RecommendationCode.HOLD_FOR_INFORMATION,
            ExceptionCategory.VENDOR_BLOCK,
            expected="vendor not in a hold status",
            observed=f"vendor {vendor.vendor_id} status {vendor.status}",
        )

    master = vendor.payment_details.last4
    mismatched = [c for c in claimed_last4(ctx.free_text()) if c != master]
    recent = bank_change_is_recent(vendor, history)
    if mismatched or recent:
        findings = []
        if mismatched:
            findings.append(
                f"case text claims account ending {', '.join(mismatched)}; "
                f"vendor master account ends {master}"
            )
        if recent:
            findings.append(
                f"bank details changed {vendor.payment_details.changed_at}; "
                "no payment since the change"
            )
        return b.failed(
            RecommendationCode.ESCALATE_CONTROL_REVIEW,
            ExceptionCategory.BANK_CHANGE,
            expected="payment instructions match the verified vendor master",
            observed="; ".join(findings),
        )
    return b.passed(
        expected="active vendor; payment instructions match the vendor master",
        observed=f"vendor {vendor.vendor_id} {vendor.status}; account ending {master}",
    )
