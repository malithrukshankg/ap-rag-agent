"""Approve / reject use case (brief §2): resolve a pending posting decision and resume.

Accepted replies are identical on every delivery (FIN-005); a failed check leaves the run
AWAITING_APPROVAL and says which check failed.
"""

from ap_agent.kernel.enums import ApprovalVerdict
from ap_agent.orchestration.runtime import ApprovalReply, Orchestrator
from ap_agent.services.errors import RunNotFound

ApprovalResult = ApprovalReply


class ApprovalService:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator

    def resolve(
        self,
        run_id: str,
        *,
        verdict: ApprovalVerdict,
        approver_id: str,
        decision_fingerprint: str,
    ) -> ApprovalResult:
        reply = self._orchestrator.resolve_approval(
            run_id,
            verdict=verdict,
            approver_id=approver_id,
            decision_fingerprint=decision_fingerprint,
        )
        if not reply.accepted and reply.reason == "unknown run":
            raise RunNotFound(run_id)
        return reply
