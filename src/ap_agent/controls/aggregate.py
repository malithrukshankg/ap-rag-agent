"""Aggregation (architecture §4.6): code decides, the model can only tighten.

- Each control contributes an effective floor: FAIL → its floor (at least HOLD);
  INDETERMINATE → at least HOLD; PASS and NOT_APPLICABLE → APPROVE.
- The model's proposal is capped first (O4: REJECT_* become HOLD), then combined.
- O8: a model ESCALATE stands only with grounds, meaning at least one risk indicator that
  survived TB3 verification (cited from this case's own text). An ungrounded ESCALATE
  is downgraded to HOLD. Without this, a model that agrees with every control but picks
  "the strictest option" overrides a control floor such as REJECT_DUPLICATE (seen live
  on FIN-002). HOLD still blocks payment, so the downgrade never loosens safety.
- Outcome = the highest of all floors and the capped proposal. So APPROVE_FOR_POSTING
  only happens when every applicable control passed and the model did not object.
- The primary exception category and escalation target come from the first entry in the
  configured precedence list that matches a control at the winning outcome (O5).
"""

from collections.abc import Sequence

from ap_agent.controls.policy import MODEL_PROPOSAL_CEILING, OUTCOME_SEVERITY, EscalationRule
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import (
    ControlId,
    ControlStatus,
    ExceptionCategory,
    RecommendationCode,
)
from ap_agent.kernel.types import Strict

APPROVE = RecommendationCode.APPROVE_FOR_POSTING
HOLD = RecommendationCode.HOLD_FOR_INFORMATION
ESCALATE = RecommendationCode.ESCALATE_CONTROL_REVIEW


class Aggregate(Strict):
    outcome: RecommendationCode
    exception_category: ExceptionCategory | None
    escalation_target: str | None
    drivers: list[ControlId]  # controls whose effective floor equals the outcome
    model_proposal: RecommendationCode | None
    model_proposal_capped: bool  # O4 cap or O8 ungrounded-escalation downgrade applied
    model_escalation_ungrounded: bool  # O8: ESCALATE proposed with no verified indicator
    model_raised_outcome: bool


def _max(a: RecommendationCode, b: RecommendationCode) -> RecommendationCode:
    return a if OUTCOME_SEVERITY[a] >= OUTCOME_SEVERITY[b] else b


def effective_floor(result: ControlResult) -> RecommendationCode:
    if result.status in (ControlStatus.PASS, ControlStatus.NOT_APPLICABLE):
        return APPROVE
    return _max(result.outcome_floor, HOLD)  # FAIL and INDETERMINATE are never approve


def cap_model_proposal(
    proposal: RecommendationCode, *, verified_indicators: int = 0
) -> RecommendationCode:
    if proposal not in MODEL_PROPOSAL_CEILING:  # O4
        return HOLD
    if proposal is ESCALATE and verified_indicators == 0:  # O8
        return HOLD
    return proposal


def aggregate(
    results: Sequence[ControlResult],
    *,
    model_proposal: RecommendationCode | None,
    precedence: Sequence[EscalationRule],
    verified_indicators: int = 0,
) -> Aggregate:
    """`verified_indicators`: count of model risk indicators that passed TB3 verification."""
    control_outcome = APPROVE
    for r in results:
        control_outcome = _max(control_outcome, effective_floor(r))

    capped = (
        cap_model_proposal(model_proposal, verified_indicators=verified_indicators)
        if model_proposal is not None
        else None
    )
    outcome = control_outcome if capped is None else _max(control_outcome, capped)
    drivers = [r.control_id for r in results if effective_floor(r) == outcome]

    category: ExceptionCategory | None = None
    target: str | None = None
    if outcome is not APPROVE:
        candidates: list[tuple[ControlId | None, ExceptionCategory]] = [
            (r.control_id, r.exception_category)
            for r in results
            if effective_floor(r) == outcome and r.exception_category is not None
        ]
        if not candidates:  # raised by the model alone, or an uncategorised INDETERMINATE
            candidates = [(None, ExceptionCategory.OTHER_CONTROL_RISK)]
        for rule in precedence:
            if any(
                cat == rule.category and (rule.source is None or rule.source == cid)
                for cid, cat in candidates
            ):
                category, target = rule.category, rule.target
                break
        else:
            category = candidates[0][1]

    return Aggregate(
        outcome=outcome,
        exception_category=category,
        escalation_target=target,
        drivers=drivers,
        model_proposal=model_proposal,
        model_proposal_capped=capped is not None and capped != model_proposal,
        model_escalation_ungrounded=model_proposal is ESCALATE and verified_indicators == 0,
        model_raised_outcome=OUTCOME_SEVERITY[outcome] > OUTCOME_SEVERITY[control_outcome],
    )
