"""C5 delegated authority (FIN-POL-003 §1-3; FIN-POL-009 §2).

- The amount is the total commitment including tax (§1), in AUD at the corporate daily rate
  for the invoice date (FIN-POL-009 §2).
- The required role is the lowest FIN-POL-003 §2 role whose limit covers it; above the CFO
  limit, the CEO.
- Two approvals are required (§3) for a vendor less than 30 days old, a recent bank change
  (O3), an overseas bank account, or a fraud flag → ESCALATE (AUTHORITY_GAP). There is no
  multi-approver posting path.
"""

from datetime import date

from ap_agent.controls.common import ResultBuilder, bank_change_is_recent
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import ControlId, ExceptionCategory, RecommendationCode

RULE = "FIN-POL-003 §1-3 delegated financial authority"
ROLES = (
    ("Cost Centre Manager", "c5_limit_cost_centre_manager"),
    ("Department Director", "c5_limit_department_director"),
    ("Executive Director", "c5_limit_executive_director"),
    ("Chief Financial Officer", "c5_limit_chief_financial_officer"),
)
HOME_COUNTRY = "AU"


def check(ctx: ControlContext) -> ControlResult:
    b = ResultBuilder(ctx, ControlId.C5, RULE)
    case, base = ctx.case, ctx.facets.base_currency
    keys = [k for _, k in ROLES] + ["c5_two_approvals", "c5_new_vendor_days"]
    if ctx.facets.fx_applies:
        keys.append("c5_fx_authority")
    cited = b.cite(*keys)
    has_vendor = b.use(ctx.vendor, "vendor master record")
    has_history = b.use(ctx.history, "invoice history (for bank-change recency)")
    has_rate = not ctx.facets.fx_applies or b.use(
        ctx.fx, f"{case.currency}->{base} corporate daily rate"
    )
    vendor, history = ctx.vendor.value, ctx.history.value
    if not (cited and has_vendor and has_history and has_rate) or vendor is None or history is None:
        return b.indeterminate(ExceptionCategory.AUTHORITY_GAP)

    if ctx.facets.fx_applies:
        assert ctx.fx is not None and ctx.fx.value is not None
        amount = b.calc(ctx.calc.convert(case.amount, ctx.fx.value.rate, case.currency, base))
    else:
        amount = case.amount

    role, limit = "Chief Executive Officer", None
    for name, key in ROLES:
        if amount <= ctx.params[key].decimal():
            role, limit = name, ctx.params[key].decimal()
            break

    new_vendor_days = int(ctx.params["c5_new_vendor_days"].value)
    created = date.fromisoformat(vendor.created_at[:10])
    triggers = []
    if (case.invoice_date - created).days < new_vendor_days:
        triggers.append(f"vendor created {created.isoformat()}, under {new_vendor_days} days")
    if bank_change_is_recent(vendor, history):
        triggers.append("bank account changed with no payment since")
    if vendor.payment_details.bank_country != HOME_COUNTRY:
        triggers.append(f"overseas bank account ({vendor.payment_details.bank_country})")
    if any("FRAUD" in flag.upper() for flag in vendor.risk_flags):
        triggers.append("vendor fraud flag")

    needed = f"{role}" + (f" (limit {base} {limit:,.2f})" if limit is not None else "")
    if triggers:
        return b.failed(
            RecommendationCode.ESCALATE_CONTROL_REVIEW,
            ExceptionCategory.AUTHORITY_GAP,
            expected=f"single approver: {needed}",
            observed="two approvals required (FIN-POL-003 §3): " + "; ".join(triggers),
            threshold=f"total commitment {base} {amount:,.2f}",
        )
    return b.passed(
        expected=f"approver with authority for {base} {amount:,.2f}",
        observed=f"required role: {needed}; no higher-risk triggers",
        threshold=f"total commitment {base} {amount:,.2f}",
    )
