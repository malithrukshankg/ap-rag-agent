"""Case fixtures FIN-001…005 (brief §5): the request, a fault profile, the expected outcome
and the scripted model completions for the stable tier."""

from pathlib import Path
from typing import Any

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
    # Documents retrieval must surface for this case (recall@5, architecture §11).
    golden_doc_ids: list[str] = Field(default_factory=list)


class CaseFixture(Strict):
    description: str
    case: CaseRequest
    faults: FaultProfile = Field(default_factory=dict)
    expected: ExpectedOutcome
    # Completions for the scripted adapter, in order. A dict is sent as JSON; a string is
    # sent verbatim (so a fixture can script malformed output first).
    scripted_model: list[str | dict[str, Any]] = Field(default_factory=list)


def load_case(path: Path) -> CaseFixture:
    return CaseFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(cases_dir: Path) -> list[CaseFixture]:
    return [load_case(p) for p in sorted(cases_dir.glob("FIN-*.json"))]
