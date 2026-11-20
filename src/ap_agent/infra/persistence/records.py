"""Row-shaped records exchanged with repositories. Payload fields are already redacted."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from ap_agent.controls.calc import Calculation
from ap_agent.controls.result import ControlResult
from ap_agent.kernel.enums import ApprovalStatus, ApprovalVerdict, DecisionCode, RunStatus

JsonDict = dict[str, Any]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRecord(Record):
    run_id: str
    case_id: str
    status: RunStatus
    current_node: str | None
    request_redacted: JsonDict
    config_snapshot: JsonDict
    budget: JsonDict
    result: JsonDict | None = None
    failure_reason: str | None = None
    created_at: str
    updated_at: str


class AuditEvent(Record):
    """Envelope written to audit_events. `payload` comes from a per-type allow-list model."""

    ts: str
    run_id: str
    type: str
    node: str | None
    outcome: str
    duration_ms: int | None
    payload: JsonDict
    event_id: int | None = None  # assigned by the store


class StoredControlResult(Record):
    run_id: str
    result: ControlResult
    calculations: list[Calculation]


class RetrievalRecord(Record):
    retrieval_id: str
    run_id: str
    query_label: str
    query_text: str
    filters: JsonDict
    chunk_ids: list[str]
    scores: list[float]
    ts: str


class ToolCallRecord(Record):
    tool_call_id: str
    run_id: str
    node: str
    tool: str
    attempt: int
    args_redacted: JsonDict
    response_redacted: JsonDict | None
    outcome: str
    error: str | None
    duration_ms: int
    ts: str


class ModelCallRecord(Record):
    model_call_id: str
    run_id: str
    node: str
    attempt: int
    prompt_sha256: str
    prompt_redacted: str
    completion_text: str | None
    parse_result: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int
    ts: str


class ApprovalRecord(Record):
    approval_id: str
    run_id: str
    decision_fingerprint: str
    status: ApprovalStatus
    amount: Decimal
    currency: str
    approver_id: str | None = None
    verdict: ApprovalVerdict | None = None
    grant_id: str | None = None
    grant_consumed_at: str | None = None
    stored_response: JsonDict | None = None
    created_at: str
    resolved_at: str | None = None


class DecisionRecord(Record):
    decision_id: str
    run_id: str
    case_id: str
    idempotency_key: str
    decision: DecisionCode
    payload_sha256: str
    computed_total: Decimal
    currency: str
    grant_id: str | None
    ledger_ref: str
    response: JsonDict
    created_at: str


class LedgerEntry(Record):
    ledger_ref: str
    idempotency_key: str
    case_id: str
    decision: DecisionCode
    amount: Decimal
    currency: str
    created_at: str
