"""Start run and Get run use cases (brief §2)."""

from collections.abc import Sequence
from typing import Any

from ap_agent.evidence.tools.base import FaultProfile
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.types import Strict
from ap_agent.orchestration.runtime import Orchestrator, RunView
from ap_agent.services.errors import RunNotFound


class TimelineEntry(Strict):
    ts: str
    node: str | None
    type: str
    outcome: str
    duration_ms: int | None
    detail: str


class RunDetails(RunView):
    """Get run: status, current state, result, audit events; optionally a timeline (§10)."""

    timeline: list[TimelineEntry] | None = None


def _detail(payload: dict[str, Any]) -> str:
    """One-line summary of an allow-listed payload (scalars only)."""
    return ", ".join(f"{k}={v}" for k, v in payload.items() if not isinstance(v, list | dict))


def build_timeline(events: Sequence[dict[str, Any]]) -> list[TimelineEntry]:
    return [
        TimelineEntry(
            ts=e["ts"],
            node=e["node"],
            type=e["type"],
            outcome=e["outcome"],
            duration_ms=e["duration_ms"],
            detail=_detail(e["payload"]),
        )
        for e in events
    ]


class RunService:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator

    def start(
        self,
        case: CaseRequest,
        *,
        faults: FaultProfile | None = None,
        scripted: Sequence[str | dict[str, Any]] = (),
    ) -> RunDetails:
        view = self._orchestrator.start_run(case, faults=faults, scripted=scripted)
        return RunDetails(**view.model_dump())

    def get(self, run_id: str, *, timeline: bool = False) -> RunDetails:
        try:
            view = self._orchestrator.get_run(run_id)
        except KeyError:
            raise RunNotFound(run_id) from None
        return RunDetails(
            **view.model_dump(),
            timeline=build_timeline(view.audit_events) if timeline else None,
        )

    def recover_interrupted(self) -> list[str]:
        """Startup hook (O7): stale RUNNING runs become FAILED(interrupted)."""
        return self._orchestrator.recover_interrupted()
