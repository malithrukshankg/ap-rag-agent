"""Schema a model completion must satisfy at TB3 (architecture §5). Unknown fields fail."""

from pydantic import Field

from ap_agent.kernel.enums import CitationUse, ControlId, IndicatorCode, RecommendationCode
from ap_agent.kernel.types import Strict


class ClaimCitation(Strict):
    chunk_id: str
    quote: str = Field(min_length=1)
    use: CitationUse


class Claim(Strict):
    finding_ref: ControlId
    text: str
    citations: list[ClaimCitation] = Field(default_factory=list)


class RiskIndicator(Strict):
    indicator: IndicatorCode
    chunk_id: str
    quote: str = Field(min_length=1)


class ModelAssessment(Strict):
    findings: list[Claim]
    inferences: list[str]
    unknowns: list[str]
    risk_indicators: list[RiskIndicator]
    proposed_outcome: RecommendationCode
