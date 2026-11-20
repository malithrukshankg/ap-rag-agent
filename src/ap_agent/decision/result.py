"""Typed final result (§2.4, §4; architecture §8)."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from ap_agent.controls.calc import Calculation
from ap_agent.controls.result import ControlResult
from ap_agent.evidence.citation import Citation
from ap_agent.kernel.enums import (
    ApprovalStatus,
    ApprovalVerdict,
    ConfidenceLevel,
    ExceptionCategory,
    RecommendationCode,
    RunStatus,
)
from ap_agent.kernel.types import Strict


class Confidence(Strict):
    """Computed in code from control statuses and citation outcomes (RA#9)."""

    level: ConfidenceLevel
    basis: list[str]


class Recommendation(Strict):
    outcome: RecommendationCode
    exception_category: ExceptionCategory | None = None
    next_action: str
    escalation_target: str | None = None
    confidence: Confidence


class SourcedFact(Strict):
    """A fact with exactly one source: a tool call or a retrieved document."""

    statement: str
    value: str
    tool_call_id: str | None = None
    citation: Citation | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.tool_call_id is None) == (self.citation is None):
            raise ValueError("a sourced fact needs exactly one of tool_call_id or citation")
        return self


class Inference(Strict):
    text: str
    origin: Literal["rule", "model"]
    citations: list[Citation] = Field(default_factory=list)


class Unknown(Strict):
    what: str
    why: str
    impact: str


class ActionTaken(Strict):
    action: str
    at: datetime
    detail: str
    reference: str | None = None  # e.g. decision id, approval id, ledger ref


class ApprovalSummary(Strict):
    status: ApprovalStatus
    decision_fingerprint: str
    approver_id: str | None = None
    verdict: ApprovalVerdict | None = None
    decided_at: datetime | None = None


class FinalResult(Strict):
    run_id: str
    case_id: str
    status: RunStatus
    recommendation: Recommendation
    sourced_facts: list[SourcedFact] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)  # never model-produced
    inferences: list[Inference] = Field(default_factory=list)  # can never gate a payment
    unknowns: list[Unknown] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    policy_findings: list[ControlResult] = Field(default_factory=list)
    actions_taken: list[ActionTaken] = Field(default_factory=list)
    decision_fingerprint: str | None = None
    approval: ApprovalSummary | None = None
    applied_policy_versions: dict[str, str] = Field(default_factory=dict)  # doc_id → version
