"""Approve / reject (brief §2). Translates HTTP to ApprovalService calls.

200: accepted; the body is identical on every delivery (FIN-005).
409: a check failed; the run stays AWAITING_APPROVAL.
404: unknown run.
"""

from fastapi import APIRouter, HTTPException, Response, status

from ap_agent.api.dependencies import Approvals
from ap_agent.api.schemas import ApprovalRequest, ErrorResponse
from ap_agent.services import ApprovalResult, RunNotFound

router = APIRouter(tags=["approval"])


@router.post(
    "/runs/{run_id}/approval",
    response_model=ApprovalResult,
    responses={404: {"model": ErrorResponse}, 409: {"model": ApprovalResult}},
    summary="Approve or reject a pending payment posting",
)
def resolve_approval(
    run_id: str, body: ApprovalRequest, approvals: Approvals, response: Response
) -> ApprovalResult:
    try:
        result = approvals.resolve(
            run_id,
            verdict=body.verdict,
            approver_id=body.approver_id,
            decision_fingerprint=body.decision_fingerprint,
        )
    except RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    if not result.accepted:
        response.status_code = status.HTTP_409_CONFLICT
    return result
