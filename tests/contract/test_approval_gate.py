"""The five approval checks and grant minting (architecture §6.1; RA#6)."""

from datetime import date
from decimal import Decimal

import pytest

from ap_agent.decision.approvals import ApprovalGate
from ap_agent.evidence.tools.schemas import Approver
from ap_agent.infra.observability.logger import RunEventLogger
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.kernel.enums import ApprovalStatus, ApprovalVerdict

TS = "2026-11-20T00:00:00.000+00:00"
FP = "f" * 64


def _approver(approver_id: str, **kw: object) -> Approver:
    base: dict[str, object] = {
        "approver_id": approver_id, "name": approver_id, "role": "Cost Centre Manager",
        "authority_limit": "10000.00", "currency": "AUD", "active": True,
        "valid_from": "2026-01-01", "valid_to": "2026-12-31",
    }  # fmt: skip
    base.update(kw)
    return Approver.model_validate(base)


class Directory:
    def __init__(self, *approvers: Approver) -> None:
        self._by_id = {a.approver_id: a for a in approvers}

    def get(self, approver_id: str) -> Approver | None:
        return self._by_id.get(approver_id)


DIRECTORY = Directory(
    _approver("ok"),
    _approver("small", authority_limit="1000.00"),
    _approver("inactive", active=False),
    _approver("expired", valid_to="2026-07-31"),
    _approver("u-requester"),
)


@pytest.fixture
def gate(store: SqliteStore) -> ApprovalGate:
    return ApprovalGate(store, DIRECTORY, clock=lambda: TS, today=lambda: date(2026, 11, 20))


def _pending(gate: ApprovalGate, store: SqliteStore) -> None:
    gate.request(run_id="run-1", fingerprint=FP, amount=Decimal("6050.00"), currency="AUD",
                 events=RunEventLogger(store.audit, "run-1"))  # fmt: skip


@pytest.mark.parametrize(
    ("approver", "fingerprint", "check"),
    [
        ("nobody", FP, "approver_unknown_or_inactive"),
        ("inactive", FP, "approver_unknown_or_inactive"),
        ("small", FP, "authority_limit"),
        ("u-requester", FP, "approver_raised_case"),
        ("ok", "0" * 64, "fingerprint_mismatch"),
        ("expired", FP, "delegation_not_valid"),
    ],
)
def test_each_check_refuses_and_is_audited(
    gate: ApprovalGate, store: SqliteStore, approver: str, fingerprint: str, check: str
) -> None:
    _pending(gate, store)
    res = gate.resolve(run_id="run-1", verdict=ApprovalVerdict.APPROVE, approver_id=approver,
                       fingerprint=fingerprint, raised_by="u-requester",
                       events=RunEventLogger(store.audit, "run-1"))  # fmt: skip
    assert not res.accepted and res.failed_check == check
    assert store.approvals.get_pending_for_run("run-1") is not None  # still pending
    assert store.audit.list_for_run("run-1")[-1].payload["check"] == check


def test_pass_mints_a_single_grant_and_replays(gate: ApprovalGate, store: SqliteStore) -> None:
    _pending(gate, store)
    kw = {"run_id": "run-1", "verdict": ApprovalVerdict.APPROVE, "approver_id": "ok",
          "fingerprint": FP, "raised_by": "u-requester",
          "events": RunEventLogger(store.audit, "run-1")}  # fmt: skip
    first = gate.resolve(**kw)  # type: ignore[arg-type]
    second = gate.resolve(**kw)  # type: ignore[arg-type]
    assert first.accepted and not first.replay
    assert second.accepted and second.replay
    assert first.approval is not None and second.approval is not None
    assert first.approval.grant_id == second.approval.grant_id
    assert first.approval.status is ApprovalStatus.APPROVED


def test_duplicate_approval_request_is_one_row(gate: ApprovalGate, store: SqliteStore) -> None:
    _pending(gate, store)
    _pending(gate, store)
    rows = store.db.query("SELECT COUNT(*) FROM approvals WHERE run_id = 'run-1'")
    assert rows[0][0] == 1
