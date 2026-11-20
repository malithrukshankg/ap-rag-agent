"""Repository protocols. Everything above persistence depends on these, never on SQLite."""

from contextlib import AbstractContextManager
from typing import Protocol

from ap_agent.infra.persistence.records import (
    ApprovalRecord,
    AuditEvent,
    DecisionRecord,
    JsonDict,
    LedgerEntry,
    ModelCallRecord,
    RetrievalRecord,
    RunRecord,
    StoredControlResult,
    ToolCallRecord,
)
from ap_agent.kernel.enums import ApprovalStatus, ApprovalVerdict, RunStatus


class RunRepository(Protocol):
    def create(self, run: RunRecord) -> None: ...
    def get(self, run_id: str) -> RunRecord | None: ...
    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        current_node: str | None,
        updated_at: str,
        failure_reason: str | None = None,
    ) -> None: ...
    def set_budget(self, run_id: str, budget: JsonDict, updated_at: str) -> None: ...
    def set_result(self, run_id: str, result: JsonDict, updated_at: str) -> None: ...
    def list_by_status(self, status: RunStatus) -> list[RunRecord]: ...


class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> int: ...
    def list_for_run(self, run_id: str) -> list[AuditEvent]: ...


class ControlResultRepository(Protocol):
    def save(self, record: StoredControlResult) -> None: ...
    def list_for_run(self, run_id: str) -> list[StoredControlResult]: ...


class RetrievalRepository(Protocol):
    def save(self, record: RetrievalRecord) -> None: ...
    def list_for_run(self, run_id: str) -> list[RetrievalRecord]: ...


class ToolCallRepository(Protocol):
    def save(self, record: ToolCallRecord) -> None: ...
    def list_for_run(self, run_id: str) -> list[ToolCallRecord]: ...


class ModelCallRepository(Protocol):
    def save(self, record: ModelCallRecord) -> None: ...
    def list_for_run(self, run_id: str) -> list[ModelCallRecord]: ...


class ApprovalRepository(Protocol):
    def insert_pending(self, record: ApprovalRecord) -> ApprovalRecord:
        """Insert, or return the existing row for (run_id, decision_fingerprint)."""
        ...

    def get(self, run_id: str, decision_fingerprint: str) -> ApprovalRecord | None: ...
    def get_pending_for_run(self, run_id: str) -> ApprovalRecord | None: ...
    def get_by_grant(self, grant_id: str) -> ApprovalRecord | None: ...
    def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        approver_id: str,
        verdict: ApprovalVerdict,
        grant_id: str,
        resolved_at: str,
    ) -> bool:
        """PENDING → resolved. False if it was no longer PENDING."""
        ...

    def consume_grant(self, grant_id: str, consumed_at: str) -> bool:
        """Single use: False if already consumed or unknown."""
        ...

    def set_stored_response(self, approval_id: str, response: JsonDict) -> None: ...


class DecisionRepository(Protocol):
    def get_by_key(self, idempotency_key: str) -> DecisionRecord | None: ...
    def insert(self, record: DecisionRecord) -> None:
        """Raises DuplicateKeyError if the idempotency key exists."""
        ...

    def list_for_case(self, case_id: str) -> list[DecisionRecord]: ...


class LedgerRepository(Protocol):
    def post(self, entry: LedgerEntry) -> LedgerEntry:
        """Idempotent on idempotency_key: returns the existing entry if already posted."""
        ...

    def count_for_key(self, idempotency_key: str) -> int: ...


class Store(Protocol):
    """Unit of work: all repositories share one connection and one transaction scope.

    Repositories are read-only properties so implementations may expose concrete types.
    """

    @property
    def runs(self) -> RunRepository: ...
    @property
    def audit(self) -> AuditRepository: ...
    @property
    def control_results(self) -> ControlResultRepository: ...
    @property
    def retrievals(self) -> RetrievalRepository: ...
    @property
    def tool_calls(self) -> ToolCallRepository: ...
    @property
    def model_calls(self) -> ModelCallRepository: ...
    @property
    def approvals(self) -> ApprovalRepository: ...
    @property
    def decisions(self) -> DecisionRepository: ...
    @property
    def ledger(self) -> LedgerRepository: ...

    def transaction(self) -> AbstractContextManager[None]:
        """BEGIN IMMEDIATE ... COMMIT; rolls back on exception. Re-entrant (joins outer)."""
        ...

    def close(self) -> None: ...


class DuplicateKeyError(Exception):
    """A unique constraint rejected the write."""
