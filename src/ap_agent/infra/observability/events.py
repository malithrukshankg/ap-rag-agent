"""Audit event payloads: one Pydantic model per event type (architecture §6.5).

Each model is a field allow-list: `extra="forbid"` rejects anything not declared, and fields
carry ids, codes and counts only. No free text from cases, documents or model output, and no
amounts or bank details (§4 safe logging). Free-form reasons are closed Literals.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ap_agent.kernel.enums import (
    ControlId,
    ControlStatus,
    DecisionCode,
    FaultKind,
    RecommendationCode,
    RunStatus,
)


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_type: ClassVar[str]


# --- run lifecycle --------------------------------------------------------------------


class RunStarted(EventPayload):
    event_type: ClassVar[str] = "run.started"
    case_id: str


class RunCompleted(EventPayload):
    event_type: ClassVar[str] = "run.completed"
    status: RunStatus
    outcome: RecommendationCode
    decision: DecisionCode | None = None


class RunFailed(EventPayload):
    event_type: ClassVar[str] = "run.failed"
    reason: Literal["budget_exhausted", "interrupted", "unrecoverable"]
    error_type: str | None = None  # exception class name only, never its message


class NodeEntered(EventPayload):
    event_type: ClassVar[str] = "node.entered"


class NodeExited(EventPayload):
    event_type: ClassVar[str] = "node.exited"


class BudgetExhausted(EventPayload):
    event_type: ClassVar[str] = "budget.exhausted"
    counter: Literal["steps", "tool_calls", "model_calls", "retries"]
    limit: int


# --- retrieval and tools --------------------------------------------------------------


class RetrievalPerformed(EventPayload):
    event_type: ClassVar[str] = "retrieval.performed"
    retrieval_id: str
    query_label: str
    chunk_ids: list[str]
    untrusted_chunk_ids: list[str]


class ToolAttempt(EventPayload):
    event_type: ClassVar[str] = "tool.attempt"
    tool: str
    tool_call_id: str
    attempt: int


class ToolRetry(EventPayload):
    event_type: ClassVar[str] = "tool.retry"
    tool: str
    attempt: int
    fault: FaultKind


class ToolTimeout(EventPayload):
    event_type: ClassVar[str] = "tool.timeout"
    tool: str
    attempt: int
    timeout_s: float


# --- model ----------------------------------------------------------------------------


class ModelCall(EventPayload):
    event_type: ClassVar[str] = "model.call"
    model_call_id: str
    attempt: int
    prompt_sha256: str
    parse_ok: bool
    input_tokens: int
    output_tokens: int


class ModelRepair(EventPayload):
    event_type: ClassVar[str] = "model.repair"
    attempt: int
    error_count: int


class CitationRejected(EventPayload):
    event_type: ClassVar[str] = "citation.rejected"
    chunk_id: str
    reason: Literal[
        "not_in_retrieval_set", "quote_not_verbatim", "hash_mismatch", "untrusted_as_authority"
    ]


# --- controls, approval, decision -----------------------------------------------------


class ControlEvaluated(EventPayload):
    event_type: ClassVar[str] = "control.evaluated"
    control_id: ControlId
    status: ControlStatus
    outcome_floor: RecommendationCode


class ApprovalRequested(EventPayload):
    event_type: ClassVar[str] = "approval.requested"
    approval_id: str
    decision_fingerprint: str


class ApprovalCheckFailed(EventPayload):
    event_type: ClassVar[str] = "approval.check_failed"
    approver_id: str
    check: Literal[
        "approver_unknown_or_inactive",
        "authority_limit",
        "approver_raised_case",
        "fingerprint_mismatch",
        "delegation_not_valid",
    ]


class ApprovalGranted(EventPayload):
    event_type: ClassVar[str] = "approval.granted"
    approval_id: str
    grant_id: str
    approver_id: str
    verdict: Literal["approve", "reject"]


class DecisionRecorded(EventPayload):
    event_type: ClassVar[str] = "decision.recorded"
    decision_id: str
    decision: DecisionCode
    idempotency_key: str
    ledger_ref: str


class DecisionReplayed(EventPayload):
    event_type: ClassVar[str] = "decision.replayed"
    decision_id: str
    idempotency_key: str


def all_event_types() -> dict[str, type[EventPayload]]:
    """Registry of every payload model, keyed by event type. Duplicate types are a bug."""
    registry: dict[str, type[EventPayload]] = {}
    for cls in EventPayload.__subclasses__():
        if cls.event_type in registry:
            raise RuntimeError(f"duplicate event type {cls.event_type}")
        registry[cls.event_type] = cls
    return registry
