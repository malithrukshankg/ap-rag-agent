"""Output of every control, C1 to C6 (architecture §4.3)."""

from pydantic import Field

from ap_agent.evidence.citation import Citation
from ap_agent.kernel.enums import ControlId, ControlStatus, ExceptionCategory, RecommendationCode
from ap_agent.kernel.types import Strict


class ControlResult(Strict):
    control_id: ControlId
    status: ControlStatus
    outcome_floor: RecommendationCode
    exception_category: ExceptionCategory | None = None
    rule: str
    expected: str | None = None
    observed: str | None = None
    threshold: str | None = None
    calculation_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
