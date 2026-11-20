"""C4 three-way match (FIN-POL-002 §1-5; FIN-POL-009 §3; FIN-POL-012 §1).

With a PO: per line and for the total. Invoiced quantity must not exceed received quantity;
a line without a receipt fails (§4). Price variance per line must be within the lower of the
absolute limit and the percentage of the PO line value, by category (§2). On failure the
record names the line, expected and actual values, difference and threshold (§3).

Currency: an invoice in a different currency from the PO is held unless the PO permits
conversion (FIN-POL-009 §3); when permitted, invoice amounts are converted at the corporate
daily rate. Tolerances are AUD, so a PO in a non-base currency is INDETERMINATE here.

PO tolerance vs policy tolerance (RA#8): both are recorded and any disagreement is flagged in
`threshold`; the policy value is the one applied.

No PO: FIN-POL-012 is consulted and cited; non-PO evidence is not verifiable → HOLD.
"""

from decimal import Decimal

from ap_agent.controls.common import ResultBuilder
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.evidence.tools.schemas import POLine
from ap_agent.kernel.enums import ControlId, ExceptionCategory, RecommendationCode

RULE = "FIN-POL-002 §1-5 three-way match and tolerances"
TOLERANCE_PARAMS = (
    "c4_goods_abs",
    "c4_goods_pct",
    "c4_services_abs",
    "c4_services_pct",
    "c4_freight_abs",
)
HOLD = RecommendationCode.HOLD_FOR_INFORMATION


def _policy_tolerance(ctx: ControlContext, line: POLine) -> tuple[Decimal, Decimal]:
    """(absolute limit, percent of PO line value) for the line's category (FIN-POL-002 §2)."""
    p = ctx.params
    if line.category == "services":
        return p["c4_services_abs"].decimal(), p["c4_services_pct"].decimal()
    if line.category == "freight":
        return p["c4_freight_abs"].decimal(), Decimal(100)  # absolute limit only
    return p["c4_goods_abs"].decimal(), p["c4_goods_pct"].decimal()


def _no_po(ctx: ControlContext) -> ControlResult:
    b = ResultBuilder(ctx, ControlId.C4, "FIN-POL-012 §1, §3 non-PO invoices")
    if not b.cite("c4_nopo"):
        return b.indeterminate(ExceptionCategory.MISSING_PO)
    b.missing.append("exception reason, receipt evidence, cost centre and procurement review")
    return b.failed(
        HOLD,
        ExceptionCategory.MISSING_PO,
        expected="a purchase order, or verified non-PO evidence (FIN-POL-012 §3)",
        observed="no PO reference; non-PO evidence is not verifiable",
    )


def check(ctx: ControlContext) -> ControlResult:
    if not ctx.facets.po_present:
        return _no_po(ctx)

    b = ResultBuilder(ctx, ControlId.C4, RULE)
    case, base = ctx.case, ctx.facets.base_currency
    cited = b.cite(*TOLERANCE_PARAMS)
    po_ev = ctx.purchase_order
    has_po = b.use(po_ev, f"purchase order {case.po_ref}")
    if not (cited and has_po) or po_ev is None or po_ev.value is None:
        if not has_po:
            b.missing.append("goods receipt: not verifiable without the purchase order")
        return b.indeterminate(
            ExceptionCategory.MISSING_RECEIPT if not has_po else ExceptionCategory.PRICE_VARIANCE
        )
    po = po_ev.value

    if po.currency != base:
        b.missing.append(f"AUD tolerance conversion for a {po.currency} purchase order")
        return b.indeterminate(ExceptionCategory.PRICE_VARIANCE)

    rate: Decimal | None = None
    if case.currency != po.currency:
        if not b.cite("c4_fx_hold"):
            return b.indeterminate(ExceptionCategory.PRICE_VARIANCE)
        if not po.permits_conversion:
            return b.failed(
                HOLD,
                ExceptionCategory.OTHER_CONTROL_RISK,
                expected=f"invoice currency {po.currency}, or a PO that permits conversion",
                observed=f"invoice in {case.currency}; PO {po.po_ref} in {po.currency} "
                "and does not permit conversion",
            )
        if not b.use(ctx.fx, f"{case.currency}->{po.currency} corporate daily rate"):
            return b.indeterminate(ExceptionCategory.PRICE_VARIANCE)
        assert ctx.fx is not None and ctx.fx.value is not None
        rate = ctx.fx.value.rate

    def in_po_currency(amount: Decimal) -> Decimal:
        if rate is None:
            return amount
        return b.calc(ctx.calc.convert(amount, rate, case.currency, po.currency))

    failures: list[tuple[ExceptionCategory, str]] = []
    if po.vendor_id != case.vendor_id:
        failures.append((ExceptionCategory.OTHER_CONTROL_RISK, f"PO vendor {po.vendor_id}"))
    if po.approval_status != "APPROVED":
        failures.append(
            (ExceptionCategory.OTHER_CONTROL_RISK, f"PO approval status {po.approval_status}")
        )

    po_lines = {line.line_no: line for line in po.lines}
    received: dict[int, Decimal] = {}
    for r in po.receipts:
        received[r.line_no] = received.get(r.line_no, Decimal(0)) + r.quantity_received

    expected_values: list[Decimal] = []
    invoiced_values: list[Decimal] = []
    disagreements: set[str] = set()
    for line in case.lines:
        pol = po_lines.get(line.po_line_no) if line.po_line_no is not None else None
        if pol is None:
            failures.append(
                (ExceptionCategory.PRICE_VARIANCE, f"line {line.line_no}: no matching PO line")
            )
            continue
        got = received.get(pol.line_no, Decimal(0))
        if got == 0:
            b.missing.append(f"goods receipt for PO line {pol.line_no}")
            failures.append(
                (ExceptionCategory.MISSING_RECEIPT, f"line {line.line_no}: no goods receipt")
            )
        elif line.quantity > got:
            failures.append(
                (
                    ExceptionCategory.QUANTITY_VARIANCE,
                    f"line {line.line_no}: invoiced {line.quantity}, received {got}",
                )
            )
        invoiced = in_po_currency(line.line_total)
        expected = b.calc(ctx.calc.line_value(line.quantity, pol.unit_price, po.currency))
        variance = b.calc(ctx.calc.variance(invoiced, expected, po.currency))
        policy_abs, policy_pct = _policy_tolerance(ctx, pol)
        limit = b.calc(
            ctx.calc.tolerance_limit(pol.line_value, policy_abs, policy_pct, po.currency)
        )
        expected_values.append(expected)
        invoiced_values.append(invoiced)
        if (po.tolerance.absolute, po.tolerance.percent) != (policy_abs, policy_pct):
            disagreements.add(
                f"PO tolerance {po.tolerance.absolute}/{po.tolerance.percent}% differs from "
                f"policy for {pol.category}; policy applied"
            )
        if variance > limit:
            failures.append(
                (
                    ExceptionCategory.PRICE_VARIANCE,
                    f"line {line.line_no}: expected {expected}, actual {invoiced}, "
                    f"difference {variance}, threshold {limit} {po.currency}",
                )
            )

    if expected_values:
        net = case.amount - (case.tax_amount or Decimal(0))
        line_sum = b.calc(ctx.calc.total(invoiced_values, po.currency))
        if b.calc(ctx.calc.variance(in_po_currency(net), line_sum, po.currency)) != 0:
            failures.append(
                (ExceptionCategory.PRICE_VARIANCE, "invoice lines do not sum to the net amount")
            )
        expected_total = b.calc(ctx.calc.total(expected_values, po.currency))
        total_variance = b.calc(ctx.calc.variance(line_sum, expected_total, po.currency))
        total_limit = b.calc(
            ctx.calc.tolerance_limit(
                po.total,
                ctx.params["c4_goods_abs"].decimal(),
                ctx.params["c4_goods_pct"].decimal(),
                po.currency,
            )
        )
        if total_variance > total_limit:
            failures.append(
                (
                    ExceptionCategory.PRICE_VARIANCE,
                    f"total: expected {expected_total}, actual {line_sum}, "
                    f"difference {total_variance}, threshold {total_limit} {po.currency}",
                )
            )
    else:
        failures.append((ExceptionCategory.PRICE_VARIANCE, "no invoice lines to match"))

    threshold = "lower of absolute limit and % of PO line value (FIN-POL-002 §2)"
    if disagreements:
        threshold += "; " + "; ".join(sorted(disagreements))
    if failures:
        return b.failed(
            HOLD,
            failures[0][0],
            expected=f"invoice, PO {po.po_ref} and receipts agree within tolerance",
            observed="; ".join(text for _, text in failures),
            threshold=threshold,
        )
    return b.passed(
        expected=f"invoice, PO {po.po_ref} and receipts agree within tolerance",
        observed=f"{len(case.lines)} line(s) matched; quantities received; total within tolerance",
        threshold=threshold,
    )
