"""Assembles the typed FinalResult (brief §4; architecture §8) from code-produced parts.

Sourced facts come from tool responses (with tool_call_ids); calculations only from the
Calculator; inferences carry their origin; unknowns say what, why and impact; policy
findings are the control results; applied policy versions come from trusted citations.
"""

from collections.abc import Sequence
from datetime import datetime

from ap_agent.assessment.verify import VerifiedAssessment
from ap_agent.controls.aggregate import Aggregate
from ap_agent.controls.calc import Calculation
from ap_agent.controls.context import ControlContext
from ap_agent.controls.result import ControlResult
from ap_agent.decision.result import (
    ActionTaken,
    ApprovalSummary,
    Confidence,
    FinalResult,
    Inference,
    Recommendation,
    SourcedFact,
    Unknown,
)
from ap_agent.decision.routing import DECISION_ROUTES
from ap_agent.evidence.citation import Citation
from ap_agent.kernel.enums import AssessmentStatus, ControlStatus, RunStatus, Trust

ASSUMPTIONS = [
    "Invoices arrive as structured JSON; invoice line extraction from PDFs is out of scope.",
    "Approver identity is asserted by the caller and trusted (no authentication).",
    "Vendor, purchase-order, invoice-history, FX and ledger systems are simulated fixtures.",
]


def _sourced_facts(ctx: ControlContext) -> list[SourcedFact]:
    facts: list[SourcedFact] = []
    if ctx.vendor.ok and ctx.vendor.value is not None:
        v, tc = ctx.vendor.value, ctx.vendor.tool_call_ids[-1]
        facts.append(SourcedFact(statement="vendor status", value=v.status, tool_call_id=tc))
        facts.append(
            SourcedFact(
                statement="vendor master account (last four digits)",
                value=v.payment_details.last4,
                tool_call_id=tc,
            )
        )
    po = ctx.purchase_order
    if po is not None and po.ok and po.value is not None:
        tc = po.tool_call_ids[-1]
        facts.append(
            SourcedFact(
                statement=f"purchase order {po.value.po_ref} total",
                value=f"{po.value.currency} {po.value.total}",
                tool_call_id=tc,
            )
        )
        facts.append(
            SourcedFact(
                statement="goods receipts on the purchase order",
                value=str(len(po.value.receipts)),
                tool_call_id=tc,
            )
        )
    if ctx.history.ok and ctx.history.value is not None:
        facts.append(
            SourcedFact(
                statement="prior invoices for the vendor",
                value=str(len(ctx.history.value.candidates)),
                tool_call_id=ctx.history.tool_call_ids[-1],
            )
        )
    if ctx.fx is not None and ctx.fx.ok and ctx.fx.value is not None:
        r = ctx.fx.value
        facts.append(
            SourcedFact(
                statement=f"corporate daily rate {r.base}->{r.quote} on {r.rate_date}",
                value=f"{r.rate} ({r.source})",
                tool_call_id=ctx.fx.tool_call_ids[-1],
            )
        )
    return facts


def _model_inferences(ctx: ControlContext, verified: VerifiedAssessment | None) -> list[Inference]:
    if verified is None:
        return []
    chunks = {c.chunk_id: c for c in ctx.retrieved}
    out: list[Inference] = []
    for claim in verified.findings:
        citations = [
            Citation(
                chunk_id=ch.chunk_id,
                doc_id=ch.doc_id,
                version=ch.version,
                section=ch.section,
                text_sha256=ch.text_sha256,
                quote=c.quote,
                trust=ch.trust,
            )
            for c in claim.citations
            if (ch := chunks.get(c.chunk_id)) is not None
        ]
        out.append(Inference(text=claim.text, origin="model", citations=citations))
    out.extend(Inference(text=t, origin="model") for t in verified.inferences)
    return out


def _unknowns(
    results: Sequence[ControlResult],
    verified: VerifiedAssessment | None,
    assessment_status: AssessmentStatus,
) -> list[Unknown]:
    out = [
        Unknown(
            what=missing,
            why=f"{r.control_id.value} could not verify it",
            impact=f"{r.control_id.value} is INDETERMINATE, so the outcome is at least HOLD",
        )
        for r in results
        if r.status is ControlStatus.INDETERMINATE
        for missing in r.missing_evidence
    ]
    if assessment_status is AssessmentStatus.UNAVAILABLE:
        out.append(
            Unknown(
                what="model narrative",
                why="the model assessment was invalid after all repair attempts",
                impact="outcome raised to at least HOLD; no model claims are included",
            )
        )
    if verified is not None:
        out.extend(
            Unknown(what=u, why="reported by the model or not verifiable", impact="not used")
            for u in verified.unknowns
        )
    return out


def _reasons(results: Sequence[ControlResult], aggregate: Aggregate) -> list[str]:
    """Every control that pushed the outcome above APPROVE, plus a model raise if any.

    Lists all non-passing controls, not only `aggregate.drivers`: a lower-floor control is
    still a reason a human must clear before the invoice can be paid.
    """
    out = [
        f"{r.control_id.value} {r.status.value} (floor {r.outcome_floor.value}): {r.observed}"
        for r in results
        if r.status in (ControlStatus.FAIL, ControlStatus.INDETERMINATE)
    ]
    if aggregate.model_raised_outcome:
        out.append(f"model assessment raised the outcome to {aggregate.outcome.value}")
    return out


def applied_policy_versions(results: Sequence[ControlResult]) -> dict[str, str]:
    return {c.doc_id: c.version for r in results for c in r.citations if c.trust is Trust.TRUSTED}


def build_final_result(
    *,
    run_id: str,
    ctx: ControlContext,
    status: RunStatus,
    results: Sequence[ControlResult],
    calculations: Sequence[Calculation],
    aggregate: Aggregate,
    confidence: Confidence,
    verified: VerifiedAssessment | None,
    assessment_status: AssessmentStatus,
    decision_fingerprint: str,
) -> FinalResult:
    route = DECISION_ROUTES[aggregate.outcome]
    return FinalResult(
        run_id=run_id,
        case_id=ctx.case.case_id,
        status=status,
        recommendation=Recommendation(
            outcome=aggregate.outcome,
            exception_category=aggregate.exception_category,
            next_action=route.next_action,
            escalation_target=aggregate.escalation_target,
            confidence=confidence,
            reasons=_reasons(results, aggregate),
        ),
        sourced_facts=_sourced_facts(ctx),
        calculations=list(calculations),
        inferences=_model_inferences(ctx, verified),
        unknowns=_unknowns(results, verified, assessment_status),
        assumptions=list(ASSUMPTIONS),
        policy_findings=list(results),
        actions_taken=[],
        decision_fingerprint=decision_fingerprint,
        approval=None,
        applied_policy_versions=applied_policy_versions(results),
    )


def with_action(
    result: FinalResult, action: str, detail: str, reference: str | None
) -> FinalResult:
    return result.model_copy(
        update={
            "actions_taken": [
                *result.actions_taken,
                ActionTaken(
                    action=action,
                    at=datetime.now().astimezone(),
                    detail=detail,
                    reference=reference,
                ),
            ]
        }
    )


def with_approval(result: FinalResult, approval: ApprovalSummary) -> FinalResult:
    return result.model_copy(update={"approval": approval})
