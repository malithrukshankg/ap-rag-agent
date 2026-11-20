"""Case fixtures FIN-001…005 (brief §5): the request, a fault profile and the expected outcome.
Scripted model completions are added in P5."""

from pathlib import Path

from pydantic import Field

from ap_agent.evidence.tools.base import FaultProfile
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import DecisionCode, RecommendationCode
from ap_agent.kernel.types import Strict


class ExpectedOutcome(Strict):
    recommendation: RecommendationCode
    approval_requested: bool
    final_decision: DecisionCode | None
    duplicate_callbacks: int = Field(default=0, ge=0)


class CaseFixture(Strict):
    description: str
    case: CaseRequest
    faults: FaultProfile = Field(default_factory=dict)
    expected: ExpectedOutcome


def load_case(path: Path) -> CaseFixture:
    return CaseFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(cases_dir: Path) -> list[CaseFixture]:
    return [load_case(p) for p in sorted(cases_dir.glob("FIN-*.json"))]
