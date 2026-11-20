"""Recommendation → decision mapping at TB4 (architecture §4.7). Tables, not branching logic.

Two vocabularies are kept apart on purpose: the recommendation uses corpus codes
(FIN-POL-001 §3); the decision is the submit_finance_decision argument. A recommendation is
not an approval.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ap_agent.kernel.enums import ApprovalVerdict, DecisionCode, RecommendationCode


@dataclass(frozen=True)
class DecisionRoute:
    decision: DecisionCode | None  # None: no decision until an approver resolves it
    requires_approval: bool
    next_action: str
    uses_escalation_target: bool


DECISION_ROUTES: Final = MappingProxyType(
    {
        RecommendationCode.APPROVE_FOR_POSTING: DecisionRoute(
            decision=None,
            requires_approval=True,
            next_action="Await an authorised approver's decision (§2.5).",
            uses_escalation_target=False,
        ),
        RecommendationCode.HOLD_FOR_INFORMATION: DecisionRoute(
            decision=DecisionCode.HOLD,
            requires_approval=False,
            next_action="Obtain the missing evidence listed in unknowns, then resubmit.",
            uses_escalation_target=False,
        ),
        RecommendationCode.REJECT_DUPLICATE: DecisionRoute(
            decision=DecisionCode.HOLD,
            requires_approval=False,
            next_action="AP review must confirm the duplicate rejection (FIN-POL-005 §2).",
            uses_escalation_target=False,
        ),
        RecommendationCode.REJECT_INVALID: DecisionRoute(
            decision=DecisionCode.REJECTED,
            requires_approval=False,
            next_action="Return to sender: the case is structurally invalid.",
            uses_escalation_target=False,
        ),
        RecommendationCode.ESCALATE_CONTROL_REVIEW: DecisionRoute(
            decision=DecisionCode.HOLD,
            requires_approval=False,
            next_action="Escalate to the named control owner (FIN-POL-007 §3).",
            uses_escalation_target=True,
        ),
    }
)

# Decision written by resume_after_approval for each approver verdict.
VERDICT_DECISIONS: Final = MappingProxyType(
    {
        ApprovalVerdict.APPROVE: DecisionCode.POST_FOR_PAYMENT,
        ApprovalVerdict.REJECT: DecisionCode.REJECTED,
    }
)
