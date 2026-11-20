"""Schema-level guarantees the safety design relies on (architecture §6.4, §9)."""

import sqlite3
from decimal import Decimal

import pytest

from ap_agent.infra.persistence.protocols import DuplicateKeyError
from ap_agent.infra.persistence.records import (
    ApprovalRecord,
    AuditEvent,
    DecisionRecord,
    LedgerEntry,
    RunRecord,
)
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.kernel.enums import ApprovalStatus, ApprovalVerdict, DecisionCode, RunStatus

TS = "2026-11-20T00:00:00.000+00:00"


def _approval(approval_id: str, fingerprint: str = "fp-1") -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        run_id="run-1",
        decision_fingerprint=fingerprint,
        status=ApprovalStatus.PENDING,
        amount=Decimal("1100.00"),
        currency="AUD",
        created_at=TS,
    )


def _decision(decision_id: str, key: str = "key-1") -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        run_id="run-1",
        case_id="FIN-001",
        idempotency_key=key,
        decision=DecisionCode.POST_FOR_PAYMENT,
        payload_sha256="p" * 64,
        computed_total=Decimal("1100.00"),
        currency="AUD",
        grant_id="g-1",
        ledger_ref="L-1",
        response={"status": "RECORDED"},
        created_at=TS,
    )


def _ledger(ref: str, key: str = "key-1") -> LedgerEntry:
    return LedgerEntry(
        ledger_ref=ref,
        idempotency_key=key,
        case_id="FIN-001",
        decision=DecisionCode.POST_FOR_PAYMENT,
        amount=Decimal("1100.00"),
        currency="AUD",
        created_at=TS,
    )


def test_wal_mode_enabled(store: SqliteStore) -> None:
    assert store.db.journal_mode() == "wal"


# --- the three unique constraints -------------------------------------------------------


def test_approvals_unique_run_and_fingerprint_raw(store: SqliteStore) -> None:
    store.approvals.insert_pending(_approval("a-1"))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.db.execute(
            "INSERT INTO approvals (approval_id, run_id, decision_fingerprint, status, amount,"
            " currency, created_at) VALUES ('a-2','run-1','fp-1','PENDING','1','AUD',?)",
            (TS,),
        )


def test_duplicate_approval_request_returns_the_existing_row(store: SqliteStore) -> None:
    first = store.approvals.insert_pending(_approval("a-1"))
    second = store.approvals.insert_pending(_approval("a-2"))
    assert second.approval_id == first.approval_id == "a-1"
    other = store.approvals.insert_pending(_approval("a-3", fingerprint="fp-2"))
    assert other.approval_id == "a-3"


def test_decisions_unique_idempotency_key(store: SqliteStore) -> None:
    store.decisions.insert(_decision("d-1"))
    with pytest.raises(DuplicateKeyError):
        store.decisions.insert(_decision("d-2"))
    assert store.decisions.get_by_key("key-1") == _decision("d-1")
    assert len(store.decisions.list_for_case("FIN-001")) == 1


def test_sim_ledger_unique_idempotency_key(store: SqliteStore) -> None:
    first = store.ledger.post(_ledger("L-1"))
    replay = store.ledger.post(_ledger("L-2"))
    assert replay.ledger_ref == first.ledger_ref == "L-1"
    assert store.ledger.count_for_key("key-1") == 1
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.db.execute(
            "INSERT INTO sim_ledger VALUES ('L-3','key-1','FIN-001','HOLD','1','AUD',?)", (TS,)
        )


# --- grant lifecycle --------------------------------------------------------------------


def test_resolve_once_and_grant_is_single_use(store: SqliteStore) -> None:
    rec = store.approvals.insert_pending(_approval("a-1"))
    args = (rec.approval_id, ApprovalStatus.APPROVED, "u-approver", ApprovalVerdict.APPROVE)
    assert store.approvals.resolve(*args, "g-1", TS) is True
    assert store.approvals.resolve(*args, "g-2", TS) is False  # no longer PENDING
    assert store.approvals.consume_grant("g-1", TS) is True
    assert store.approvals.consume_grant("g-1", TS) is False
    assert store.approvals.consume_grant("unknown", TS) is False
    got = store.approvals.get_by_grant("g-1")
    assert got is not None and got.grant_consumed_at == TS


# --- audit trail and transactions -------------------------------------------------------


def test_audit_events_are_append_only(store: SqliteStore) -> None:
    store.audit.append(
        AuditEvent(
            ts=TS,
            run_id="run-1",
            type="run.started",
            node=None,
            outcome="ok",
            duration_ms=None,
            payload={"case_id": "FIN-001"},
        )
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE audit_events SET outcome = 'tampered'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM audit_events")


def test_transaction_rolls_back_every_write(store: SqliteStore) -> None:
    with pytest.raises(RuntimeError), store.transaction():
        store.decisions.insert(_decision("d-1"))
        store.ledger.post(_ledger("L-1"))
        raise RuntimeError("crash mid-transaction")
    assert store.decisions.get_by_key("key-1") is None
    assert store.ledger.count_for_key("key-1") == 0


def test_run_round_trip_and_status_update(store: SqliteStore) -> None:
    run = RunRecord(
        run_id="run-1",
        case_id="FIN-001",
        status=RunStatus.RUNNING,
        current_node="INTAKE",
        request_redacted={"case_id": "FIN-001"},
        config_snapshot={"model_id": "claude-opus-5-5"},
        budget={"steps": 0},
        created_at=TS,
        updated_at=TS,
    )
    store.runs.create(run)
    store.runs.set_status("run-1", RunStatus.AWAITING_APPROVAL, "REQUEST_APPROVAL", TS)
    got = store.runs.get("run-1")
    assert got is not None and got.status is RunStatus.AWAITING_APPROVAL
    assert [r.run_id for r in store.runs.list_by_status(RunStatus.AWAITING_APPROVAL)] == ["run-1"]
    with pytest.raises(DuplicateKeyError):
        store.runs.create(run)


def test_schema_is_idempotent(store: SqliteStore) -> None:
    store.db.init_schema()  # second application must not fail or drop data
    assert store.db.query_one("PRAGMA user_version")[0] == 2  # type: ignore[index]
