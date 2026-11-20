"""List evaluation results (brief §2). Translates HTTP to EvalService calls."""

from fastapi import APIRouter, HTTPException, status

from ap_agent.api.dependencies import Evals
from ap_agent.api.schemas import ErrorResponse
from ap_agent.services import EvalReport, NoEvalReport

router = APIRouter(tags=["evals"])


@router.post("/evals", response_model=EvalReport, summary="Run the evaluation cases")
def run(evals: Evals) -> EvalReport:
    return evals.run()


@router.get(
    "/evals/latest",
    response_model=EvalReport,
    responses={404: {"model": ErrorResponse}},
    summary="The most recent evaluation report",
)
def latest(evals: Evals) -> EvalReport:
    try:
        return evals.latest()
    except NoEvalReport:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no evaluation has been run yet") from None
