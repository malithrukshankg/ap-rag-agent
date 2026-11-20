"""Schema a model completion must satisfy at TB3 (architecture §5). Unknown fields fail.

The model cannot emit numbers or control statuses that reach the result: there is no field
for either. `agrees_with_control` is the structured way for a claim to dispute a control's
result (our decision, P5); a disputing claim is moved to unknowns, never applied.
"""

from pydantic import Field

from ap_agent.kernel.enums import CitationUse, ControlId, IndicatorCode, RecommendationCode
from ap_agent.kernel.types import Strict


class ClaimCitation(Strict):
    chunk_id: str  # a source id from the <untrusted_evidence> block
    quote: str = Field(min_length=1, max_length=400)
    use: CitationUse


class Claim(Strict):
    finding_ref: ControlId
    agrees_with_control: bool
    text: str = Field(min_length=1, max_length=1000)
    citations: list[ClaimCitation] = Field(default_factory=list)


class RiskIndicator(Strict):
    indicator: IndicatorCode
    chunk_id: str
    quote: str = Field(min_length=1, max_length=400)


class ModelAssessment(Strict):
    findings: list[Claim]
    inferences: list[str]
    unknowns: list[str]
    risk_indicators: list[RiskIndicator]
    proposed_outcome: RecommendationCode
