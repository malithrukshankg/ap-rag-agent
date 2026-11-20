"""Graph nodes (architecture §2.1). Each node receives its dependencies through the node
object (Settings, ModelClient, store, tools: injection), never through graph state.

Every node is wrapped: it charges one budget step, records current_node, emits
node.entered/node.exited with duration, and persists the budget counters on the run row.
"""

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ap_agent.assessment.assess import AssessmentOutcome
from ap_agent.assessment.redaction import build_evidence_pack
from ap_agent.assessment.verify import VerifiedAssessment
from ap_agent.controls import c6_risk
from ap_agent.controls.aggregate import aggregate
from ap_agent.controls.calc import Calculator
from ap_agent.controls.context import ControlContext, Evidence
from ap_agent.controls.engine import run_controls
from ap_agent.controls.params import PolicyParams
from ap_agent.controls.result import ControlResult
from ap_agent.decision.approvals import ApprovalGate
from ap_agent.decision.assemble import build_final_result, with_action, with_approval
from ap_agent.decision.confidence import compute_confidence
from ap_agent.decision.fingerprint import decision_fingerprint
from ap_agent.decision.result import ApprovalSummary, FinalResult
from ap_agent.decision.routing import DECISION_ROUTES, VERDICT_DECISIONS
from ap_agent.decision.submit import SubmitFinanceDecisionTool, SubmitRequest
from ap_agent.evidence.rag.facets import Facets, derive_facets
from ap_agent.evidence.rag.index import RetrievedChunk, filters_applied
from ap_agent.evidence.rag.queries import plan_queries
from ap_agent.evidence.tools.fx import FxRateTool
from ap_agent.evidence.tools.history import InvoiceHistoryTool
from ap_agent.evidence.tools.purchase_order import PurchaseOrderTool
from ap_agent.evidence.tools.retrieve import RetrieveTool
from ap_agent.evidence.tools.schemas import RetrieveRequest
from ap_agent.evidence.tools.vendor import VendorTool
from ap_agent.infra.observability.events import (
    BudgetExhausted as BudgetExhaustedEvent,
)
from ap_agent.infra.observability.events import (
    ControlEvaluated,
    NodeEntered,
    NodeExited,
    RetrievalPerformed,
    RunStarted,
)
from ap_agent.infra.observability.logger import RunEventLogger, Stopwatch
from ap_agent.infra.persistence.protocols import Store
from ap_agent.infra.persistence.records import RetrievalRecord, RunRecord, StoredControlResult
from ap_agent.infra.settings import Settings
from ap_agent.intake.case import CaseRequest
from ap_agent.kernel.enums import (
    ApprovalStatus,
    ApprovalVerdict,
    AssessmentStatus,
    DecisionCode,
    RecommendationCode,
    RunStatus,
    Trust,
)
from ap_agent.orchestration.budget import Budget, BudgetExhausted
from ap_agent.orchestration.model_runner import ModelRunner
from ap_agent.orchestration.state import RunState
from ap_agent.orchestration.tool_runner import ToolOutcome, ToolRunner

REQUEST_FIELDS = (
    "case_id",
    "invoice_ref",
    "vendor_id",
    "vendor_name",
    "amount",
    "tax_amount",
    "currency",
    "invoice_date",
    "po_ref",
    "requested_by",
)


class NodeFn(Protocol):
    """A LangGraph node: takes the state, returns a partial update."""

    def __call__(self, state: RunState) -> RunState: ...


class SubmitFailed(Exception):
    """submit_finance_decision did not return a decision after its retries."""


def redacted_request(case: CaseRequest) -> dict[str, object]:
    """Allow-listed request record: typed fields only, never notes or attachment text."""
    data = case.model_dump(mode="json")
    record: dict[str, object] = {k: data[k] for k in REQUEST_FIELDS}
    record["line_count"] = len(case.lines)
    record["attachment_names"] = [a.name for a in case.attachments]
    record["has_notes"] = case.notes is not None
    return record


def _evidence[T](outcome: ToolOutcome[T]) -> Evidence[T]:  # type: ignore[type-var]
    return Evidence(
        source=outcome.tool,
        value=outcome.output,
        tool_call_ids=outcome.tool_call_ids,
        error=outcome.error,
    )


@dataclass
class Base:
    run_id: str
    settings: Settings
    store: Store
    events: RunEventLogger
    budget: Budget
    tools: ToolRunner
    clock: Callable[[], str]

    def wrap(self, name: str, fn: NodeFn) -> NodeFn:
        def node(state: RunState) -> RunState:
            try:
                self.budget.charge("steps")
            except BudgetExhausted as exc:
                self.events.emit(
                    BudgetExhaustedEvent(counter="steps", limit=exc.limit),
                    node=name,
                    outcome="failed",
                )
                raise
            self.events.emit(NodeEntered(), node=name)
            watch = Stopwatch()
            try:
                update = fn(state)
            finally:
                # Persist counters even when the node fails (e.g. budget exhausted mid-node),
                # so Get run shows what was actually spent.
                if self.store.runs.get(self.run_id) is not None:
                    self.store.runs.set_budget(self.run_id, self.budget.snapshot(), self.clock())
            if self.store.runs.get(self.run_id) is not None:
                status = RunStatus(update.get("status") or state.get("status") or "RUNNING")
                self.store.runs.set_status(self.run_id, status, name, self.clock())
            self.events.emit(NodeExited(), node=name, duration_ms=watch.elapsed_ms())
            return update

        return node

    def _result(self) -> FinalResult:
        run = self.store.runs.get(self.run_id)
        assert run is not None and run.result is not None
        return FinalResult.model_validate(run.result)

    def _save_result(self, result: FinalResult) -> None:
        self.store.runs.set_result(self.run_id, result.model_dump(mode="json"), self.clock())

    def _submit(self, tool: SubmitFinanceDecisionTool, request: SubmitRequest) -> RunState:
        outcome = self.tools.run("SUBMIT", tool, request)
        if not outcome.ok or outcome.output is None:
            raise SubmitFailed(outcome.error or "no result")
        r = outcome.output
        current = self._result()
        detail = f"{r.decision.value} recorded ({r.status.value}); ledger {r.ledger_ref}"
        if current.recommendation.reasons:
            detail += "; because " + "; ".join(current.recommendation.reasons)
        result = with_action(current, "decision_recorded", detail, r.decision_id)
        self._save_result(result.model_copy(update={"status": RunStatus.COMPLETED}))
        return {
            "status": RunStatus.COMPLETED.value,
            "decision": r.decision.value,
            "decision_id": r.decision_id,
        }


@dataclass
class RunWork:
    """Per-run working memory between start-graph nodes (not checkpointed; see state.py)."""

    retrieved: list[RetrievedChunk] = field(default_factory=list)
    ctx: ControlContext | None = None
    results: list[ControlResult] = field(default_factory=list)
    assessment: AssessmentOutcome | None = None
    verified: VerifiedAssessment | None = None


@dataclass
class StartNodes(Base):
    case: CaseRequest
    params: PolicyParams
    retrieve_tool: RetrieveTool
    vendor_tool: VendorTool
    po_tool: PurchaseOrderTool
    history_tool: InvoiceHistoryTool
    fx_tool: FxRateTool
    submit_tool: SubmitFinanceDecisionTool
    model: ModelRunner
    gate: ApprovalGate
    config_snapshot: dict[str, object]
    work: RunWork = field(default_factory=RunWork)

    # INTAKE: the request was validated at TB1; persist the allow-listed record.
    def intake(self, state: RunState) -> RunState:
        now = self.clock()
        self.store.runs.create(
            RunRecord(
                run_id=self.run_id,
                case_id=self.case.case_id,
                status=RunStatus.RUNNING,
                current_node="INTAKE",
                request_redacted=redacted_request(self.case),
                config_snapshot=self.config_snapshot,
                budget=self.budget.snapshot(),
                created_at=now,
                updated_at=now,
            )
        )
        self.events.emit(RunStarted(case_id=self.case.case_id), node="INTAKE")
        return {"status": RunStatus.RUNNING.value, "case_id": self.case.case_id}

    def _facets(self) -> Facets:
        p = self.settings.policy
        return derive_facets(self.case, entity=p.entity, base_currency=p.base_currency)

    # RETRIEVE: templated per-control queries; each is one tool call.
    def retrieve(self, state: RunState) -> RunState:
        facets = self._facets()
        merged: dict[str, RetrievedChunk] = {}
        for n, q in enumerate(plan_queries(facets, self.case), start=1):
            outcome = self.tools.run(
                "RETRIEVE",
                self.retrieve_tool,
                RetrieveRequest(
                    label=q.label,
                    text=q.text,
                    invoice_date=self.case.invoice_date,
                    top_k=self.settings.retrieval.top_k,
                ),
            )
            chunks = outcome.output.chunks if outcome.output is not None else []
            rid = f"{self.run_id}-r-{n:02d}"
            self.store.retrievals.save(
                RetrievalRecord(
                    retrieval_id=rid,
                    run_id=self.run_id,
                    query_label=q.label,
                    query_text=q.text,
                    filters=filters_applied(self.case.invoice_date),
                    chunk_ids=[c.chunk_id for c in chunks],
                    scores=[c.score for c in chunks],
                    ts=self.clock(),
                )
            )
            self.events.emit(
                RetrievalPerformed(
                    retrieval_id=rid,
                    query_label=q.label,
                    chunk_ids=[c.chunk_id for c in chunks],
                    untrusted_chunk_ids=[c.chunk_id for c in chunks if c.trust is Trust.UNTRUSTED],
                ),
                node="RETRIEVE",
                outcome="ok" if outcome.ok else (outcome.error or "error"),
            )
            for c in chunks:
                merged.setdefault(c.chunk_id, c)
        self.work.retrieved = list(merged.values())
        return {}

    # GATHER: other tools only when needed (§2.2): PO only with a PO, FX only if foreign.
    def gather(self, state: RunState) -> RunState:
        facets, c = self._facets(), self.case
        vendor = _evidence(self.tools.run("GATHER", self.vendor_tool, {"vendor_id": c.vendor_id}))
        history = _evidence(
            self.tools.run(
                "GATHER",
                self.history_tool,
                {
                    "vendor_id": c.vendor_id,
                    "invoice_ref": c.invoice_ref,
                    "amount": c.amount,
                    "currency": c.currency,
                },
            )
        )
        po = (
            _evidence(self.tools.run("GATHER", self.po_tool, {"po_ref": c.po_ref}))
            if facets.po_present
            else None
        )
        fx = (
            _evidence(
                self.tools.run(
                    "GATHER",
                    self.fx_tool,
                    {
                        "base": c.currency,
                        "quote": facets.base_currency,
                        "rate_date": c.invoice_date,
                    },
                )
            )
            if facets.fx_applies
            else None
        )
        self.work.ctx = ControlContext(
            case=c,
            facets=facets,
            retrieved=self.work.retrieved,
            params=self.params,
            calc=Calculator(),
            vendor=vendor,
            history=history,
            purchase_order=po,
            fx=fx,
        )
        return {}

    def _save_control(
        self,
        ctx: ControlContext,
        r: ControlResult,
        node: str,
        model_indicators: int | None = None,
    ) -> None:
        calcs = [c for c in ctx.calc.records if c.id in r.calculation_ids]
        self.store.control_results.save(
            StoredControlResult(run_id=self.run_id, result=r, calculations=calcs)
        )
        self.events.emit(
            ControlEvaluated(
                control_id=r.control_id,
                status=r.status,
                outcome_floor=r.outcome_floor,
                model_indicators=model_indicators,
            ),
            node=node,
        )

    # RECONCILE: every control on every invoice.
    def reconcile(self, state: RunState) -> RunState:
        ctx = self.work.ctx
        assert ctx is not None
        self.work.results = run_controls(ctx)
        for r in self.work.results:
            self._save_control(ctx, r, "RECONCILE")
        return {}

    # ASSESS: the only model call; narrative and citations, verified at TB3.
    def assess(self, state: RunState) -> RunState:
        ctx = self.work.ctx
        assert ctx is not None
        pack = build_evidence_pack(self.case, self.work.retrieved, self.work.results)
        self.work.assessment = self.model.assess(pack)
        self.work.verified = self.model.verify(self.work.assessment, pack, self.work.results)
        return {}

    # DECIDE: re-run C6 with verified model indicators, aggregate, build the result.
    def decide(self, state: RunState) -> RunState:
        ctx, assessment = self.work.ctx, self.work.assessment
        assert ctx is not None and assessment is not None
        verified = self.work.verified
        results = list(self.work.results)
        if verified is not None and verified.risk_indicators:
            ctx = dataclasses.replace(ctx, model_indicators=frozenset(verified.risk_indicators))
            c6 = c6_risk.check(ctx)
            results = [c6 if r.control_id is c6.control_id else r for r in results]
            self._save_control(ctx, c6, "DECIDE", len(verified.risk_indicators))
        available = assessment.status is AssessmentStatus.OK and assessment.assessment is not None
        proposal = (
            assessment.assessment.proposed_outcome
            if available and assessment.assessment is not None
            else RecommendationCode.HOLD_FOR_INFORMATION  # UNAVAILABLE raises the floor
        )
        agg = aggregate(
            results,
            model_proposal=proposal,
            precedence=self.settings.escalation_precedence,
            verified_indicators=len(verified.risk_indicators) if verified else 0,  # O8
        )
        confidence = compute_confidence(
            results,
            claims_dropped=verified.claims_dropped if verified else 0,
            assessment_available=available,
        )
        fp = decision_fingerprint(self.case, agg.outcome, results)
        final = build_final_result(
            run_id=self.run_id,
            ctx=ctx,
            status=RunStatus.RUNNING,
            results=results,
            calculations=ctx.calc.records,
            aggregate=agg,
            confidence=confidence,
            verified=verified,
            assessment_status=assessment.status,
            decision_fingerprint=fp,
        )
        self._save_result(final)
        route = DECISION_ROUTES[agg.outcome]
        return {
            "outcome": agg.outcome.value,
            "route": "approval" if route.requires_approval else "submit",
            "decision_fingerprint": fp,
            "amount": str(self.case.amount),
            "currency": self.case.currency,
        }

    # REQUEST_APPROVAL: write the approval bound to the fingerprint, then stop.
    def request_approval(self, state: RunState) -> RunState:
        row = self.gate.request(
            run_id=self.run_id,
            fingerprint=state["decision_fingerprint"],
            amount=self.case.amount,
            currency=self.case.currency,
            events=self.events,
        )
        result = with_approval(
            self._result(),
            ApprovalSummary(
                status=ApprovalStatus.PENDING, decision_fingerprint=row.decision_fingerprint
            ),
        )
        result = with_action(
            result, "approval_requested", "awaiting an authorised approver", row.approval_id
        )
        self._save_result(result.model_copy(update={"status": RunStatus.AWAITING_APPROVAL}))
        return {"status": RunStatus.AWAITING_APPROVAL.value}

    # SUBMIT (start path): HOLD or REJECTED, recorded without approval (RA#1).
    def submit(self, state: RunState) -> RunState:
        decision = DECISION_ROUTES[RecommendationCode(state["outcome"])].decision
        assert decision is not None and decision is not DecisionCode.POST_FOR_PAYMENT
        c = self.case
        return self._submit(
            self.submit_tool,
            SubmitRequest(
                run_id=self.run_id,
                case_id=c.case_id,
                invoice_ref=c.invoice_ref,
                vendor_id=c.vendor_id,
                decision=decision,
                amount=c.amount,
                currency=c.currency,
                escalation_target=self._result().recommendation.escalation_target,
            ),
        )


@dataclass
class ResumeNodes(Base):
    submit_tool: SubmitFinanceDecisionTool

    # VERIFY_GRANT: the approval for this run must be resolved and carry this grant.
    def verify_grant(self, state: RunState) -> RunState:
        grant = self.store.approvals.get_by_grant(state["grant_id"])
        if grant is None or grant.run_id != self.run_id or grant.status is ApprovalStatus.PENDING:
            raise SubmitFailed("no resolved approval grant for this run")
        return {"decision": VERDICT_DECISIONS[ApprovalVerdict(state["verdict"])].value}

    # SUBMIT (resume path): POST_FOR_PAYMENT or REJECTED, through the gate in the tool.
    def submit(self, state: RunState) -> RunState:
        run = self.store.runs.get(self.run_id)
        assert run is not None
        req = run.request_redacted
        return self._submit(
            self.submit_tool,
            SubmitRequest(
                run_id=self.run_id,
                case_id=run.case_id,
                invoice_ref=str(req["invoice_ref"]),
                vendor_id=str(req["vendor_id"]),
                decision=DecisionCode(state["decision"]),
                amount=state["amount"],
                currency=state["currency"],
                grant_id=state["grant_id"],
                decision_fingerprint=state["decision_fingerprint"],
            ),
        )

    # PERSIST: record the approver's decision on the result.
    def persist(self, state: RunState) -> RunState:
        grant = self.store.approvals.get_by_grant(state["grant_id"])
        assert grant is not None
        result = with_approval(
            self._result(),
            ApprovalSummary(
                status=grant.status,
                decision_fingerprint=grant.decision_fingerprint,
                approver_id=grant.approver_id,
                verdict=grant.verdict,
                decided_at=grant.resolved_at,
            ),
        )
        self._save_result(result.model_copy(update={"status": RunStatus.COMPLETED}))
        return {"status": RunStatus.COMPLETED.value}
