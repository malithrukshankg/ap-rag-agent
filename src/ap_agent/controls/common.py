"""Shared helpers for controls: a result builder and the bank-change recency rule (O3)."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from ap_agent.controls.calc import Calculation
from ap_agent.controls.context import ControlContext, Evidence
from ap_agent.controls.params import verify_literal
from ap_agent.controls.result import ControlResult
from ap_agent.evidence.citation import Citation
from ap_agent.evidence.tools.schemas import InvoiceHistoryResponse, VendorRecord
from ap_agent.kernel.enums import ControlId, ControlStatus, ExceptionCategory, RecommendationCode

HOLD = RecommendationCode.HOLD_FOR_INFORMATION
APPROVE = RecommendationCode.APPROVE_FOR_POSTING


class ResultBuilder:
    """Collects citations, tool calls, calculations and missing evidence for one control."""

    def __init__(self, ctx: ControlContext, control_id: ControlId, rule: str) -> None:
        self.ctx = ctx
        self.control_id = control_id
        self.rule = rule
        self.citations: list[Citation] = []
        self.tool_call_ids: list[str] = []
        self.calculation_ids: list[str] = []
        self.missing: list[str] = []

    def cite(self, *keys: str) -> bool:
        """Literal check for each parameter. False if any literal is not in a retrieved chunk."""
        ok = True
        for key in keys:
            param = self.ctx.params[key]
            citation = verify_literal(param, self.ctx.retrieved)
            if citation is None:
                ok = False
                self.missing.append(
                    f"policy text {param.describe()} not retrieved: {param.literal!r}"
                )
            elif all(
                (c.chunk_id, c.quote) != (citation.chunk_id, citation.quote) for c in self.citations
            ):
                self.citations.append(citation)
        return ok

    def use(self, evidence: Evidence[Any] | None, what: str) -> bool:
        if evidence is None:
            self.missing.append(f"{what}: not requested")
            return False
        self.tool_call_ids.extend(evidence.tool_call_ids)
        if not evidence.ok:
            self.missing.append(f"{what}: {evidence.source} {evidence.error or 'no result'}")
            return False
        return True

    def calc(self, calculation: Calculation) -> Decimal:
        self.calculation_ids.append(calculation.id)
        return calculation.result

    def result(
        self,
        status: ControlStatus,
        floor: RecommendationCode,
        category: ExceptionCategory | None = None,
        *,
        expected: str | None = None,
        observed: str | None = None,
        threshold: str | None = None,
    ) -> ControlResult:
        return ControlResult(
            control_id=self.control_id,
            status=status,
            outcome_floor=floor,
            exception_category=category,
            rule=self.rule,
            expected=expected,
            observed=observed,
            threshold=threshold,
            calculation_ids=list(self.calculation_ids),
            citations=list(self.citations),
            tool_call_ids=list(dict.fromkeys(self.tool_call_ids)),
            missing_evidence=list(self.missing),
        )

    def passed(
        self,
        *,
        expected: str | None = None,
        observed: str | None = None,
        threshold: str | None = None,
    ) -> ControlResult:
        return self.result(
            ControlStatus.PASS,
            APPROVE,
            expected=expected,
            observed=observed,
            threshold=threshold,
        )

    def failed(
        self,
        floor: RecommendationCode,
        category: ExceptionCategory,
        *,
        expected: str | None = None,
        observed: str | None = None,
        threshold: str | None = None,
    ) -> ControlResult:
        return self.result(
            ControlStatus.FAIL,
            floor,
            category,
            expected=expected,
            observed=observed,
            threshold=threshold,
        )

    def indeterminate(self, category: ExceptionCategory | None = None) -> ControlResult:
        """Nothing is inferred to fill a gap (FIN-POL-001 §5): at least HOLD."""
        return self.result(
            ControlStatus.INDETERMINATE,
            HOLD,
            category,
            observed="cannot evaluate: " + "; ".join(self.missing),
        )

    def not_applicable(self, reason: str) -> ControlResult:
        return self.result(ControlStatus.NOT_APPLICABLE, APPROVE, observed=reason)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def bank_change_is_recent(vendor: VendorRecord, history: InvoiceHistoryResponse) -> bool:
    """O3: a bank change is recent if no invoice has been paid since it happened.

    The initial account set at onboarding (changed_at == created_at) is not a change.
    FIN-POL-004 §2: the first payment after a change needs Financial Control co-approval.
    """
    changed = vendor.payment_details.changed_at
    if changed is None or _ts(changed) <= _ts(vendor.created_at):
        return False
    changed_at = _ts(changed)
    return not any(
        r.status == "PAID" and r.paid_at is not None and _ts(r.paid_at) > changed_at
        for r in history.candidates
    )
