"""Orchestrator: composes one run from Settings and injected dependencies (architecture §2).

Operations (brief §2): start_run, get_run, resolve_approval, plus recover_interrupted for the
startup hook (O7). Configuration files are loaded by the caller (the API app, the CLI or a
test) and passed in, so nothing here reads the environment or opens YAML (§5.2).

Single process by design: SQLite has one writer and the idempotency guarantees rely on it.
Approval callbacks for the same run are serialised with a per-run lock.
"""

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.sqlite import SqliteSaver

from ap_agent.assessment.client import ModelClient
from ap_agent.controls.params import PolicyParams
from ap_agent.decision.approvals import ApprovalGate, FixtureApproverDirectory
from ap_agent.decision.fingerprint import idempotency_key
from ap_agent.decision.routing import VERDICT_DECISIONS
from ap_agent.decision.submit import SubmitFinanceDecisionTool
from ap_agent.evidence.rag.ingest import current_corpus_hash, ingest
from ap_agent.evidence.rag.registry import PolicyRegistry
from ap_agent.evidence.tools.backend import FixtureBackend
from ap_agent.evidence.tools.base import FaultProfile
from ap_agent.evidence.tools.faults import FaultInjector
from ap_agent.evidence.tools.fx import FxRateTool
from ap_agent.evidence.tools.history import InvoiceHistoryTool
from ap_agent.evidence.tools.purchase_order import PurchaseOrderTool
from ap_agent.evidence.tools.retrieve import RetrieveTool
from ap_agent.evidence.tools.vendor import VendorTool
from ap_agent.infra.observability.events import RunCompleted, RunFailed
from ap_agent.infra.observability.logger import RunEventLogger, utc_now_iso
from ap_agent.infra.persistence.records import ApprovalRecord, DecisionRecord
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.infra.settings import ConfigError, Settings
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import (
    ApprovalVerdict,
    DecisionCode,
    RecommendationCode,
    RunStatus,
)
from ap_agent.kernel.types import Strict
from ap_agent.orchestration.budget import Budget, BudgetExhausted
from ap_agent.orchestration.model_factory import build_model_client
from ap_agent.orchestration.model_runner import ModelRunner
from ap_agent.orchestration.nodes import ResumeNodes, StartNodes
from ap_agent.orchestration.resume_graph import build_resume_graph
from ap_agent.orchestration.start_graph import build_start_graph
from ap_agent.orchestration.state import RunState
from ap_agent.orchestration.tool_runner import ToolRunner


class RunView(Strict):
    """Get run (§2): status, current state, result and audit events."""

    run_id: str
    case_id: str
    status: RunStatus
    current_node: str | None
    failure_reason: str | None
    budget: dict[str, Any]
    result: dict[str, Any] | None
    audit_events: list[dict[str, Any]]


class ApprovalReply(Strict):
    run_id: str
    accepted: bool
    failed_check: str | None = None
    reason: str | None = None
    response: dict[str, Any] | None = None  # identical on every delivery once resolved


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        registry: PolicyRegistry,
        params: PolicyParams,
        clock: Callable[[], str] = utc_now_iso,
        today: Callable[[], date] = date.today,
        sleep: Callable[[float], None] = time.sleep,
        after_ledger: Callable[[], None] | None = None,
    ) -> None:
        if settings.payment_hmac_key is None or not settings.payment_hmac_key.get_secret_value():
            raise ConfigError("AP_PAYMENT_HMAC_KEY must be set")
        self.settings = settings
        self.params = params
        self._clock, self._sleep, self._after_ledger = clock, sleep, after_ledger
        self._hmac_key = settings.payment_hmac_key.get_secret_value().encode("utf-8")
        self.store = SqliteStore(settings.storage.sqlite_path)
        ingest(self.store.db, settings.corpus.path, registry)
        self.backend = FixtureBackend(settings.tools.fixtures_dir)
        # LangGraph checkpoints live in the same file on their own connection (WAL).
        self._saver_conn = sqlite3.connect(
            str(Path(settings.storage.sqlite_path)), check_same_thread=False
        )
        self.saver = SqliteSaver(self._saver_conn)
        self.gate = ApprovalGate(
            self.store, FixtureApproverDirectory(self.backend), clock=clock, today=today
        )
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def close(self) -> None:
        self._saver_conn.close()
        self.store.close()

    # --- helpers --------------------------------------------------------------------

    def _config_snapshot(self) -> dict[str, object]:
        s = self.settings
        return {
            "model_adapter": s.model.adapter,
            "model_id": s.model.id,
            "prompt_version": s.model.prompt_version,
            "corpus_hash": current_corpus_hash(self.store.db),
            "budget": s.budget.model_dump(),
        }

    def _tools(self, run_id: str, events: RunEventLogger, budget: Budget,
               faults: FaultProfile | None) -> ToolRunner:  # fmt: skip
        return ToolRunner(
            run_id=run_id,
            settings=self.settings.tools,
            budget=budget,
            store=self.store,
            events=events,
            faults=FaultInjector(faults),
            sleep=self._sleep,
            clock=self._clock,
            first_seq=len(self.store.tool_calls.list_for_run(run_id)),
        )

    def _submit_tool(self, events: RunEventLogger) -> SubmitFinanceDecisionTool:
        return SubmitFinanceDecisionTool(
            self.store, events, clock=self._clock, after_ledger=self._after_ledger
        )

    def _config(self, run_id: str) -> Any:
        return {"configurable": {"thread_id": run_id}}

    def _complete(self, run_id: str, state: RunState, events: RunEventLogger) -> None:
        self.store.runs.set_status(run_id, RunStatus.COMPLETED, "END", self._clock())
        events.emit(
            RunCompleted(
                status=RunStatus.COMPLETED,
                outcome=RecommendationCode(state["outcome"]),
                decision=DecisionCode(state["decision"]) if state.get("decision") else None,
            ),
            node="END",
        )
        self.saver.delete_thread(run_id)  # checkpoints are deleted on terminal state

    def _fail(
        self,
        run_id: str,
        events: RunEventLogger,
        reason: Literal["budget_exhausted", "interrupted", "unrecoverable"],
        error: str | None,
    ) -> None:
        if self.store.runs.get(run_id) is not None:
            self.store.runs.set_status(run_id, RunStatus.FAILED, None, self._clock(), reason)
        events.emit(RunFailed(reason=reason, error_type=error), outcome="failed")
        self.saver.delete_thread(run_id)

    def _lock(self, run_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(run_id, threading.Lock())

    # --- Start run ------------------------------------------------------------------

    def start_run(
        self,
        case: CaseRequest,
        *,
        faults: FaultProfile | None = None,
        scripted: Sequence[str | dict[str, Any]] = (),
        model_client: ModelClient | None = None,
    ) -> RunView:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        events = RunEventLogger(self.store.audit, run_id, clock=self._clock)
        budget = Budget(self.settings.budget)
        tools = self._tools(run_id, events, budget, faults)
        model = ModelRunner(
            run_id=run_id,
            client=model_client or build_model_client(self.settings, scripted),
            store=self.store,
            events=events,
            budget=budget,
            max_tokens=self.settings.model.max_tokens,
            max_repairs=self.settings.model_repair.max_repair_attempts,
            clock=self._clock,
        )
        nodes = StartNodes(
            run_id=run_id,
            settings=self.settings,
            store=self.store,
            events=events,
            budget=budget,
            tools=tools,
            clock=self._clock,
            case=case,
            params=self.params,
            retrieve_tool=RetrieveTool(self.store.db),
            vendor_tool=VendorTool(self.backend, self._hmac_key),
            po_tool=PurchaseOrderTool(self.backend),
            history_tool=InvoiceHistoryTool(self.backend),
            fx_tool=FxRateTool(self.backend),
            submit_tool=self._submit_tool(events),
            model=model,
            gate=self.gate,
            config_snapshot=self._config_snapshot(),
        )
        graph = build_start_graph(nodes).compile(checkpointer=self.saver)
        try:
            state = cast(RunState, graph.invoke({"run_id": run_id}, self._config(run_id)))
        except BudgetExhausted:
            self._fail(run_id, events, "budget_exhausted", None)
        except Exception as exc:  # unrecoverable: FAILED, no decision written
            self._fail(run_id, events, "unrecoverable", type(exc).__name__)
        else:
            if state.get("status") == RunStatus.COMPLETED.value:
                self._complete(run_id, state, events)
        return self.get_run(run_id)

    # --- Get run --------------------------------------------------------------------

    def get_run(self, run_id: str) -> RunView:
        run = self.store.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return RunView(
            run_id=run.run_id,
            case_id=run.case_id,
            status=run.status,
            current_node=run.current_node,
            failure_reason=run.failure_reason,
            budget=run.budget,
            result=run.result,
            audit_events=[e.model_dump(mode="json") for e in self.store.audit.list_for_run(run_id)],
        )

    # --- Approve / reject -----------------------------------------------------------

    @staticmethod
    def _render(approval: ApprovalRecord, decision: DecisionRecord | None) -> dict[str, Any]:
        """Built only from persisted rows, so every delivery renders the same response."""
        return {
            "run_id": approval.run_id,
            "approval_id": approval.approval_id,
            "approval_status": approval.status.value,
            "verdict": approval.verdict.value if approval.verdict else None,
            "approver_id": approval.approver_id,
            "decided_at": approval.resolved_at,
            "decision": decision.decision.value if decision else None,
            "decision_id": decision.decision_id if decision else None,
            "ledger_ref": decision.ledger_ref if decision else None,
            "idempotency_key": decision.idempotency_key if decision else None,
        }

    def resolve_approval(
        self,
        run_id: str,
        *,
        verdict: ApprovalVerdict,
        approver_id: str,
        decision_fingerprint: str,
    ) -> ApprovalReply:
        with self._lock(run_id):
            run = self.store.runs.get(run_id)
            if run is None:
                return ApprovalReply(run_id=run_id, accepted=False, reason="unknown run")
            events = RunEventLogger(self.store.audit, run_id, clock=self._clock)
            res = self.gate.resolve(
                run_id=run_id,
                verdict=verdict,
                approver_id=approver_id,
                fingerprint=decision_fingerprint,
                raised_by=str(run.request_redacted["requested_by"]),
                events=events,
            )
            if not res.accepted or res.approval is None:
                return ApprovalReply(
                    run_id=run_id, accepted=False, failed_check=res.failed_check, reason=res.reason
                )
            approval = res.approval
            if approval.stored_response is not None:
                return ApprovalReply(
                    run_id=run_id, accepted=True, response=approval.stored_response
                )

            assert approval.verdict is not None
            key = idempotency_key(
                run.case_id,
                str(run.request_redacted["invoice_ref"]),
                str(run.request_redacted["vendor_id"]),
                VERDICT_DECISIONS[approval.verdict],
            )
            # Resume whenever this run has not finished: first delivery, a crash between
            # ledger and decision, or a decision already recorded for the same case by an
            # earlier run (submit then replays it and this run still completes).
            if run.status is RunStatus.AWAITING_APPROVAL:
                self._resume(run_id, approval, events)
            decision = self.store.decisions.get_by_key(key)
            if decision is None:
                return ApprovalReply(run_id=run_id, accepted=True, reason="decision pending")
            self.store.approvals.set_stored_response(
                approval.approval_id, self._render(approval, decision)
            )
            # Return the persisted copy, not the freshly built dict: every delivery, first or
            # replayed, then serialises from the same stored JSON and is byte-identical.
            stored = self.store.approvals.get(run_id, approval.decision_fingerprint)
            assert stored is not None and stored.stored_response is not None
            return ApprovalReply(run_id=run_id, accepted=True, response=stored.stored_response)

    def _resume(self, run_id: str, approval: ApprovalRecord, events: RunEventLogger) -> None:
        run = self.store.runs.get(run_id)
        assert run is not None and run.result is not None and approval.verdict is not None
        budget = Budget.resume(self.settings.budget, run.budget)
        nodes = ResumeNodes(
            run_id=run_id,
            settings=self.settings,
            store=self.store,
            events=events,
            budget=budget,
            tools=self._tools(run_id, events, budget, None),
            clock=self._clock,
            submit_tool=self._submit_tool(events),
        )
        graph = build_resume_graph(nodes).compile(checkpointer=self.saver)
        resume_input: RunState = {
            "run_id": run_id,
            "case_id": run.case_id,
            "outcome": run.result["recommendation"]["outcome"],
            "decision_fingerprint": approval.decision_fingerprint,
            "amount": str(run.request_redacted["amount"]),
            "currency": str(run.request_redacted["currency"]),
            "verdict": approval.verdict.value,
            "approver_id": approval.approver_id or "",
            "grant_id": approval.grant_id or "",
        }
        try:
            state = cast(RunState, graph.invoke(resume_input, self._config(run_id)))
        except BudgetExhausted:
            self._fail(run_id, events, "budget_exhausted", None)
            return
        # Any other exception propagates: the run stays AWAITING_APPROVAL with its approval
        # resolved and its grant unconsumed, so redelivering the callback completes it.
        self._complete(run_id, state, events)

    # --- startup hook (O7) ----------------------------------------------------------

    def recover_interrupted(self) -> list[str]:
        """Stale RUNNING runs become FAILED(interrupted); the caller re-runs the case."""
        failed = []
        for run in self.store.runs.list_by_status(RunStatus.RUNNING):
            events = RunEventLogger(self.store.audit, run.run_id, clock=self._clock)
            self._fail(run.run_id, events, "interrupted", None)
            failed.append(run.run_id)
        return failed
