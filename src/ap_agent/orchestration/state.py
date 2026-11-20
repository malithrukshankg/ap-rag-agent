"""Graph state: small, JSON-only, and what the checkpoint holds (architecture §2.3).

Rich per-run objects (retrieval set, tool evidence, control results) live in the RunWork
held by the node context and in the database, not in graph state. A run that crashes before
AWAITING_APPROVAL is failed and re-run (O7), so only the paused state must be resumable, and
the resume graph needs only these fields plus the database.
"""

from typing import TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    case_id: str
    status: str  # RunStatus value
    outcome: str  # RecommendationCode value
    route: str  # "approval" | "submit"
    decision_fingerprint: str
    amount: str
    currency: str
    # resume input
    verdict: str  # ApprovalVerdict value
    approver_id: str
    grant_id: str
    # result
    decision: str  # DecisionCode value
    decision_id: str
