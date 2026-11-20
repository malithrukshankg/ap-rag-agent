"""Start run and Get run (brief §2). Translates HTTP to RunService calls, nothing more."""

from fastapi import APIRouter, HTTPException, status

from ap_agent.api.dependencies import Runs
from ap_agent.api.schemas import ErrorResponse, StartRunRequest
from ap_agent.services import RunDetails, RunNotFound

router = APIRouter(tags=["runs"])


@router.post(
    "/runs",
    status_code=status.HTTP_201_CREATED,
    response_model=RunDetails,
    summary="Start run: execute until completion, failure or approval required",
)
def start_run(body: StartRunRequest, runs: Runs) -> RunDetails:
    sim = body.simulation
    return runs.start(
        body.case,
        faults=sim.faults if sim else None,
        scripted=sim.scripted_model if sim else (),
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunDetails,
    responses={404: {"model": ErrorResponse}},
    summary="Get run: status, current state, result and audit events",
)
def get_run(run_id: str, runs: Runs, timeline: bool = False) -> RunDetails:
    try:
        return runs.get(run_id, timeline=timeline)
    except RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
