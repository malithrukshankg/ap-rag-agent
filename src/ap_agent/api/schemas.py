"""HTTP request models (brief §3: explicit schemas; FastAPI publishes them as OpenAPI).
Requests are validated here, at TB1. Response models are the service DTOs."""

from typing import Any

from pydantic import Field

from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import ApprovalVerdict
from ap_agent.kernel.types import Sha256Hex, Strict
from ap_agent.services import FaultProfile


class Simulation(Strict):
    """SIMULATED test hooks: fault injection for the fixture backends and completions for the
    scripted model adapter (ignored when the anthropic adapter is configured)."""

    faults: FaultProfile = Field(default_factory=dict)
    scripted_model: list[str | dict[str, Any]] = Field(default_factory=list)


class StartRunRequest(Strict):
    case: CaseRequest
    simulation: Simulation | None = None


class ApprovalRequest(Strict):
    verdict: ApprovalVerdict
    approver_id: str = Field(min_length=1, max_length=64)
    decision_fingerprint: Sha256Hex


class ErrorResponse(Strict):
    detail: str
