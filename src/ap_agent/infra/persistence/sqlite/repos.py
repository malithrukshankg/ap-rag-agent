"""SQLite implementations of the repository protocols."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ap_agent.controls.calc import Calculation
from ap_agent.controls.result import ControlResult
from ap_agent.infra.persistence.protocols import DuplicateKeyError
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
from ap_agent.infra.persistence.sqlite.db import Database, dumps, loads
from ap_agent.kernel.enums import ApprovalStatus, ApprovalVerdict, RunStatus


@contextmanager
def _unique_violation() -> Iterator[None]:
    try:
        yield
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            raise DuplicateKeyError(str(exc)) from exc
        raise


class SqliteRunRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, run: RunRecord) -> None:
        with _unique_violation():
            self._db.execute(
                "INSERT INTO runs (run_id, case_id, status, current_node, request_redacted,"
                " config_snapshot, budget, result, failure_reason, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.case_id,
                    run.status,
                    run.current_node,
                    dumps(run.request_redacted),
                    dumps(run.config_snapshot),
                    dumps(run.budget),
                    None if run.result is None else dumps(run.result),
                    run.failure_reason,
                    run.created_at,
                    run.updated_at,
                ),
            )

    @staticmethod
    def _row(r: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=r["run_id"],
            case_id=r["case_id"],
            status=RunStatus(r["status"]),
            current_node=r["current_node"],
            request_redacted=loads(r["request_redacted"]),
            config_snapshot=loads(r["config_snapshot"]),
            budget=loads(r["budget"]),
            result=loads(r["result"]),
            failure_reason=r["failure_reason"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def get(self, run_id: str) -> RunRecord | None:
        r = self._db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return None if r is None else self._row(r)

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        current_node: str | None,
        updated_at: str,
        failure_reason: str | None = None,
    ) -> None:
        self._db.execute(
            "UPDATE runs SET status = ?, current_node = ?, failure_reason = ?, updated_at = ?"
            " WHERE run_id = ?",
            (status, current_node, failure_reason, updated_at, run_id),
        )

    def set_budget(self, run_id: str, budget: JsonDict, updated_at: str) -> None:
        self._db.execute(
            "UPDATE runs SET budget = ?, updated_at = ? WHERE run_id = ?",
            (dumps(budget), updated_at, run_id),
        )

    def set_result(self, run_id: str, result: JsonDict, updated_at: str) -> None:
        self._db.execute(
            "UPDATE runs SET result = ?, updated_at = ? WHERE run_id = ?",
            (dumps(result), updated_at, run_id),
        )

    def list_by_status(self, status: RunStatus) -> list[RunRecord]:
        rows = self._db.query("SELECT * FROM runs WHERE status = ? ORDER BY created_at", (status,))
        return [self._row(r) for r in rows]


class SqliteAuditRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, event: AuditEvent) -> int:
        cur = self._db.execute(
            "INSERT INTO audit_events (ts, run_id, type, node, outcome, duration_ms, payload)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                event.ts,
                event.run_id,
                event.type,
                event.node,
                event.outcome,
                event.duration_ms,
                dumps(event.payload),
            ),
        )
        return int(cur.lastrowid or 0)

    def list_for_run(self, run_id: str) -> list[AuditEvent]:
        rows = self._db.query(
            "SELECT * FROM audit_events WHERE run_id = ? ORDER BY event_id", (run_id,)
        )
        return [
            AuditEvent(
                event_id=r["event_id"],
                ts=r["ts"],
                run_id=r["run_id"],
                type=r["type"],
                node=r["node"],
                outcome=r["outcome"],
                duration_ms=r["duration_ms"],
                payload=loads(r["payload"]),
            )
            for r in rows
        ]


class SqliteControlResultRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, record: StoredControlResult) -> None:
        res = record.result
        self._db.execute(
            "INSERT OR REPLACE INTO control_results"
            " (run_id, control_id, status, outcome_floor, body, calculations)"
            " VALUES (?,?,?,?,?,?)",
            (
                record.run_id,
                res.control_id,
                res.status,
                res.outcome_floor,
                dumps(res.model_dump(mode="json")),
                dumps([c.model_dump(mode="json") for c in record.calculations]),
            ),
        )

    def list_for_run(self, run_id: str) -> list[StoredControlResult]:
        rows = self._db.query(
            "SELECT * FROM control_results WHERE run_id = ? ORDER BY control_id", (run_id,)
        )
        return [
            StoredControlResult(
                run_id=r["run_id"],
                result=ControlResult.model_validate(loads(r["body"])),
                calculations=[Calculation.model_validate(c) for c in loads(r["calculations"])],
            )
            for r in rows
        ]


class SqliteRetrievalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, record: RetrievalRecord) -> None:
        self._db.execute(
            "INSERT INTO retrievals (retrieval_id, run_id, query_label, query_text, filters,"
            " chunk_ids, scores, ts) VALUES (?,?,?,?,?,?,?,?)",
            (
                record.retrieval_id,
                record.run_id,
                record.query_label,
                record.query_text,
                dumps(record.filters),
                dumps(record.chunk_ids),
                dumps(record.scores),
                record.ts,
            ),
        )

    def list_for_run(self, run_id: str) -> list[RetrievalRecord]:
        rows = self._db.query("SELECT * FROM retrievals WHERE run_id = ? ORDER BY ts", (run_id,))
        return [
            RetrievalRecord(
                retrieval_id=r["retrieval_id"],
                run_id=r["run_id"],
                query_label=r["query_label"],
                query_text=r["query_text"],
                filters=loads(r["filters"]),
                chunk_ids=loads(r["chunk_ids"]),
                scores=loads(r["scores"]),
                ts=r["ts"],
            )
            for r in rows
        ]


class SqliteToolCallRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, record: ToolCallRecord) -> None:
        self._db.execute(
            "INSERT INTO tool_calls (tool_call_id, run_id, node, tool, attempt, args_redacted,"
            " response_redacted, outcome, error, duration_ms, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.tool_call_id,
                record.run_id,
                record.node,
                record.tool,
                record.attempt,
                dumps(record.args_redacted),
                None if record.response_redacted is None else dumps(record.response_redacted),
                record.outcome,
                record.error,
                record.duration_ms,
                record.ts,
            ),
        )

    def list_for_run(self, run_id: str) -> list[ToolCallRecord]:
        rows = self._db.query(
            "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY ts, attempt", (run_id,)
        )
        return [
            ToolCallRecord(
                tool_call_id=r["tool_call_id"],
                run_id=r["run_id"],
                node=r["node"],
                tool=r["tool"],
                attempt=r["attempt"],
                args_redacted=loads(r["args_redacted"]),
                response_redacted=loads(r["response_redacted"]),
                outcome=r["outcome"],
                error=r["error"],
                duration_ms=r["duration_ms"],
                ts=r["ts"],
            )
            for r in rows
        ]


class SqliteModelCallRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, record: ModelCallRecord) -> None:
        self._db.execute(
            "INSERT INTO model_calls (model_call_id, run_id, node, attempt, prompt_sha256,"
            " prompt_redacted, completion_text, parse_result, model_id, request_id,"
            " input_tokens, output_tokens, duration_ms, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.model_call_id,
                record.run_id,
                record.node,
                record.attempt,
                record.prompt_sha256,
                record.prompt_redacted,
                record.completion_text,
                record.parse_result,
                record.model_id,
                record.request_id,
                record.input_tokens,
                record.output_tokens,
                record.duration_ms,
                record.ts,
            ),
        )

    def list_for_run(self, run_id: str) -> list[ModelCallRecord]:
        rows = self._db.query(
            "SELECT * FROM model_calls WHERE run_id = ? ORDER BY ts, attempt", (run_id,)
        )
        return [ModelCallRecord.model_validate(dict(r)) for r in rows]


class SqliteApprovalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(r: sqlite3.Row) -> ApprovalRecord:
        data = dict(r)
        data["stored_response"] = loads(data["stored_response"])
        return ApprovalRecord.model_validate(data)

    def insert_pending(self, record: ApprovalRecord) -> ApprovalRecord:
        with self._db.write():
            self._db.execute(
                "INSERT INTO approvals (approval_id, run_id, decision_fingerprint, status,"
                " amount, currency, created_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT (run_id, decision_fingerprint) DO NOTHING",
                (
                    record.approval_id,
                    record.run_id,
                    record.decision_fingerprint,
                    record.status,
                    str(record.amount),
                    record.currency,
                    record.created_at,
                ),
            )
            existing = self.get(record.run_id, record.decision_fingerprint)
        assert existing is not None
        return existing

    def get(self, run_id: str, decision_fingerprint: str) -> ApprovalRecord | None:
        r = self._db.query_one(
            "SELECT * FROM approvals WHERE run_id = ? AND decision_fingerprint = ?",
            (run_id, decision_fingerprint),
        )
        return None if r is None else self._row(r)

    def get_pending_for_run(self, run_id: str) -> ApprovalRecord | None:
        r = self._db.query_one(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'PENDING'", (run_id,)
        )
        return None if r is None else self._row(r)

    def get_by_grant(self, grant_id: str) -> ApprovalRecord | None:
        r = self._db.query_one("SELECT * FROM approvals WHERE grant_id = ?", (grant_id,))
        return None if r is None else self._row(r)

    def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        approver_id: str,
        verdict: ApprovalVerdict,
        grant_id: str,
        resolved_at: str,
    ) -> bool:
        cur = self._db.execute(
            "UPDATE approvals SET status = ?, approver_id = ?, verdict = ?, grant_id = ?,"
            " resolved_at = ? WHERE approval_id = ? AND status = 'PENDING'",
            (status, approver_id, verdict, grant_id, resolved_at, approval_id),
        )
        return cur.rowcount == 1

    def consume_grant(self, grant_id: str, consumed_at: str) -> bool:
        cur = self._db.execute(
            "UPDATE approvals SET grant_consumed_at = ?"
            " WHERE grant_id = ? AND grant_consumed_at IS NULL",
            (consumed_at, grant_id),
        )
        return cur.rowcount == 1

    def set_stored_response(self, approval_id: str, response: JsonDict) -> None:
        self._db.execute(
            "UPDATE approvals SET stored_response = ? WHERE approval_id = ?",
            (dumps(response), approval_id),
        )


class SqliteDecisionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(r: sqlite3.Row) -> DecisionRecord:
        data = dict(r)
        data["response"] = loads(data["response"])
        return DecisionRecord.model_validate(data)

    def get_by_key(self, idempotency_key: str) -> DecisionRecord | None:
        r = self._db.query_one(
            "SELECT * FROM decisions WHERE idempotency_key = ?", (idempotency_key,)
        )
        return None if r is None else self._row(r)

    def insert(self, record: DecisionRecord) -> None:
        with _unique_violation():
            self._db.execute(
                "INSERT INTO decisions (decision_id, run_id, case_id, idempotency_key, decision,"
                " payload_sha256, computed_total, currency, grant_id, ledger_ref, response,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.decision_id,
                    record.run_id,
                    record.case_id,
                    record.idempotency_key,
                    record.decision,
                    record.payload_sha256,
                    str(record.computed_total),
                    record.currency,
                    record.grant_id,
                    record.ledger_ref,
                    dumps(record.response),
                    record.created_at,
                ),
            )

    def list_for_case(self, case_id: str) -> list[DecisionRecord]:
        rows = self._db.query(
            "SELECT * FROM decisions WHERE case_id = ? ORDER BY created_at", (case_id,)
        )
        return [self._row(r) for r in rows]


class SqliteLedgerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def post(self, entry: LedgerEntry) -> LedgerEntry:
        with self._db.write():
            self._db.execute(
                "INSERT INTO sim_ledger (ledger_ref, idempotency_key, case_id, decision, amount,"
                " currency, created_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT (idempotency_key) DO NOTHING",
                (
                    entry.ledger_ref,
                    entry.idempotency_key,
                    entry.case_id,
                    entry.decision,
                    str(entry.amount),
                    entry.currency,
                    entry.created_at,
                ),
            )
            r = self._db.query_one(
                "SELECT * FROM sim_ledger WHERE idempotency_key = ?", (entry.idempotency_key,)
            )
        assert r is not None
        return LedgerEntry.model_validate(dict(r))

    def count_for_key(self, idempotency_key: str) -> int:
        r = self._db.query_one(
            "SELECT COUNT(*) FROM sim_ledger WHERE idempotency_key = ?", (idempotency_key,)
        )
        return int(r[0]) if r else 0


class SqliteStore:
    """Unit of work over one Database. Satisfies persistence.protocols.Store."""

    def __init__(self, path: str | Path) -> None:
        self.db = Database(path)
        self.db.init_schema()
        self.runs = SqliteRunRepository(self.db)
        self.audit = SqliteAuditRepository(self.db)
        self.control_results = SqliteControlResultRepository(self.db)
        self.retrievals = SqliteRetrievalRepository(self.db)
        self.tool_calls = SqliteToolCallRepository(self.db)
        self.model_calls = SqliteModelCallRepository(self.db)
        self.approvals = SqliteApprovalRepository(self.db)
        self.decisions = SqliteDecisionRepository(self.db)
        self.ledger = SqliteLedgerRepository(self.db)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.db.write():
            yield

    def close(self) -> None:
        self.db.close()
