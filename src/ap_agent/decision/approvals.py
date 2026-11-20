"""Approval gate (architecture §6.1; RA#6). Deny-by-default for payment postings.

request(): writes the PENDING approval bound to the decision fingerprint. A duplicate
request returns the same row (UNIQUE(run_id, decision_fingerprint)).

resolve(): one BEGIN IMMEDIATE transaction.
- Already resolved → returns the stored resolution (replay-safe; first resolution wins).
- Otherwise runs the five checks; any failure writes an approval.check_failed event and
  leaves the run pending; all passing mints a single-use grant on the approval row.

Authentication is out of scope: the caller asserts approver identity and it is trusted.
"""

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.schemas import Approver
from ap_agent.infra.observability.events import (
    ApprovalCheckFailed,
    ApprovalGranted,
    ApprovalRequested,
)
from ap_agent.infra.observability.logger import RunEventLogger
from ap_agent.infra.persistence.protocols import Store
from ap_agent.infra.persistence.records import ApprovalRecord
from ap_agent.kernel.enums import ApprovalStatus, ApprovalVerdict
from ap_agent.kernel.types import Strict

CheckName = Literal[
    "approver_unknown_or_inactive",
    "authority_limit",
    "approver_raised_case",
    "fingerprint_mismatch",
    "delegation_not_valid",
]


class ApproverDirectory(Protocol):
    def get(self, approver_id: str) -> Approver | None: ...


class FixtureApproverDirectory:
    """SIMULATED authority register backed by fixtures/systems/approvers.json."""

    def __init__(self, backend: FixtureBackend) -> None:
        self._backend = backend

    def get(self, approver_id: str) -> Approver | None:
        return self._backend.approvers.get(approver_id)


class Resolution(Strict):
    accepted: bool  # a grant exists for this run (now or from an earlier delivery)
    replay: bool  # this delivery found the approval already resolved
    failed_check: CheckName | None = None
    approval: ApprovalRecord | None = None
    reason: str | None = None


class ApprovalGate:
    def __init__(
        self,
        store: Store,
        directory: ApproverDirectory,
        *,
        clock: Callable[[], str],
        today: Callable[[], date],
    ) -> None:
        self._store = store
        self._directory = directory
        self._clock = clock
        self._today = today

    def request(
        self,
        *,
        run_id: str,
        fingerprint: str,
        amount: Decimal,
        currency: str,
        events: RunEventLogger,
        node: str = "REQUEST_APPROVAL",
    ) -> ApprovalRecord:
        with self._store.transaction():
            row = self._store.approvals.insert_pending(
                ApprovalRecord(
                    approval_id=f"apr-{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    decision_fingerprint=fingerprint,
                    status=ApprovalStatus.PENDING,
                    amount=amount,
                    currency=currency,
                    created_at=self._clock(),
                )
            )
            events.emit(
                ApprovalRequested(approval_id=row.approval_id, decision_fingerprint=fingerprint),
                node=node,
            )
        return row

    def _check(
        self, approver: Approver | None, pending: ApprovalRecord, fingerprint: str, raised_by: str
    ) -> CheckName | None:
        if approver is None or not approver.active:
            return "approver_unknown_or_inactive"
        if approver.currency != pending.currency or approver.authority_limit < pending.amount:
            return "authority_limit"
        if approver.approver_id == raised_by:
            return "approver_raised_case"
        if fingerprint != pending.decision_fingerprint:
            return "fingerprint_mismatch"
        if not approver.valid_from <= self._today() <= approver.valid_to:
            return "delegation_not_valid"
        return None

    def resolve(
        self,
        *,
        run_id: str,
        verdict: ApprovalVerdict,
        approver_id: str,
        fingerprint: str,
        raised_by: str,
        events: RunEventLogger,
        node: str = "APPROVAL",
    ) -> Resolution:
        with self._store.transaction():
            existing = self._store.approvals.get(run_id, fingerprint)
            if existing is not None and existing.status is not ApprovalStatus.PENDING:
                return Resolution(accepted=True, replay=True, approval=existing)

            pending = self._store.approvals.get_pending_for_run(run_id)
            if pending is None:
                return Resolution(
                    accepted=False, replay=False, reason="no pending approval for this run"
                )

            failed = self._check(self._directory.get(approver_id), pending, fingerprint, raised_by)
            if failed is not None:
                events.emit(
                    ApprovalCheckFailed(approver_id=approver_id, check=failed),
                    node=node,
                    outcome="rejected",
                )
                return Resolution(
                    accepted=False, replay=False, failed_check=failed, approval=pending
                )

            grant_id = f"grant-{uuid.uuid4().hex[:16]}"
            self._store.approvals.resolve(
                pending.approval_id,
                ApprovalStatus.APPROVED
                if verdict is ApprovalVerdict.APPROVE
                else ApprovalStatus.REJECTED,
                approver_id,
                verdict,
                grant_id,
                self._clock(),
            )
            events.emit(
                ApprovalGranted(
                    approval_id=pending.approval_id,
                    grant_id=grant_id,
                    approver_id=approver_id,
                    verdict=verdict.value,
                ),
                node=node,
            )
            resolved = self._store.approvals.get(run_id, fingerprint)
        return Resolution(accepted=True, replay=False, approval=resolved)
