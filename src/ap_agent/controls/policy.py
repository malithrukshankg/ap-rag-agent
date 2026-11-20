"""Aggregation policy tables (architecture §4.6). Tables, not branching logic."""

from types import MappingProxyType
from typing import Final

from ap_agent.kernel.enums import ControlId, ExceptionCategory, RecommendationCode
from ap_agent.kernel.types import Strict

# Higher wins: ESCALATE > REJECT_DUPLICATE > REJECT_INVALID > HOLD > APPROVE.
OUTCOME_SEVERITY: Final = MappingProxyType(
    {
        RecommendationCode.APPROVE_FOR_POSTING: 0,
        RecommendationCode.HOLD_FOR_INFORMATION: 1,
        RecommendationCode.REJECT_INVALID: 2,
        RecommendationCode.REJECT_DUPLICATE: 3,
        RecommendationCode.ESCALATE_CONTROL_REVIEW: 4,
    }
)

# O4: the model may raise the outcome only to these. REJECT_* come only from controls.
MODEL_PROPOSAL_CEILING: Final = frozenset(
    {
        RecommendationCode.APPROVE_FOR_POSTING,
        RecommendationCode.HOLD_FOR_INFORMATION,
        RecommendationCode.ESCALATE_CONTROL_REVIEW,
    }
)


class EscalationRule(Strict):
    """O5: one entry of the precedence list in config/app.yaml `escalation_precedence`."""

    source: ControlId | None = None
    category: ExceptionCategory
    target: str
